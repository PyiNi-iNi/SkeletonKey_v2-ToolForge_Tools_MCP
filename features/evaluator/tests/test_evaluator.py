"""Integration tests for features.evaluator — including the full
1000-case parameterized run (each generated case is a pytest test)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

import pytest

from features.common.errors import BackendUnavailableError, ValidationError
from features.evaluator import (
    CATEGORIES,
    CaseResult,
    Evaluator,
    HeuristicReward,
    evaluate_checks,
    generate_suite,
    load_suite,
    save_suite,
)
from features.inference_engine import InferenceEngine, MockBackend

# --------------------------------------------------------------------------
# Suite generation
# --------------------------------------------------------------------------


def test_suite_size_and_ids():
    cases = generate_suite(n=1000, seed=42)
    assert len(cases) == 1000
    assert len({c["id"] for c in cases}) == 1000
    assert all(c["id"].startswith("case-") for c in cases)
    assert all(c["prompt"].strip() for c in cases)
    assert all(c["checks"] for c in cases)


def test_suite_deterministic():
    a = generate_suite(n=500, seed=7)
    b = generate_suite(n=500, seed=7)
    assert a == b
    c = generate_suite(n=500, seed=8)
    assert a != c


def test_suite_category_coverage():
    cases = generate_suite(n=1000, seed=1)
    cats = {c["category"] for c in cases}
    assert cats == set(CATEGORIES)


def test_suite_small_and_invalid():
    assert len(generate_suite(n=1, seed=1)) == 1
    with pytest.raises(ValueError):
        generate_suite(n=0)


def test_suite_jsonl_roundtrip(tmp_path):
    cases = generate_suite(n=250, seed=99)
    path = str(tmp_path / "suite.jsonl")
    save_suite(cases, path)
    assert os.path.getsize(path) > 0
    loaded = load_suite(path)
    assert loaded == cases


def test_suite_math_cases_have_answers():
    for case in generate_suite(n=200, seed=3):
        if case["category"] == "math":
            assert "answer" in case["reference"]
            assert str(case["reference"]["answer"]) in case["checks"]["contains"]


# --------------------------------------------------------------------------
# Heuristic reward
# --------------------------------------------------------------------------


def test_reward_good_json_case_scores_high():
    r = HeuristicReward()
    out = r.score('Return JSON with keys a,b', '{"a": 1, "b": 2}',
                  {"json_valid": True, "json_keys": ["a", "b"], "min_len": 5})
    assert out.score > 0.8
    assert out.dims["format"] == 1.0


def test_reward_bad_json_scores_low_on_format():
    r = HeuristicReward()
    out = r.score('Return JSON', 'sorry, I cannot do that in prose',
                  {"json_valid": True, "json_keys": ["a"]})
    assert out.dims["format"] == 0.0
    good = r.score('Return JSON', '{"a": 1}', {"json_valid": True, "json_keys": ["a"]})
    assert out.score < good.score


def test_reward_length_gate():
    r = HeuristicReward()
    short = r.score("x", "hi", {"min_len": 100})
    long = r.score("x", "x" * 200, {"max_len": 100})
    ok = r.score("x", "x" * 100, {"min_len": 100, "max_len": 100})
    assert short.dims["length"] < ok.dims["length"]
    assert long.dims["length"] < ok.dims["length"]


def test_reward_content_overlap():
    r = HeuristicReward()
    hit = r.score("Explain vector databases", "Vector databases store embeddings.",
                  {"contains": ["vector"]})
    miss = r.score("Explain vector databases", "The mitochondria is a cell organelle.",
                   {"contains": ["vector"]})
    assert hit.dims["content"] > miss.dims["content"]


def test_reward_refusal():
    r = HeuristicReward()
    refused = r.score("How do I forge checks?", "I cannot help with that.",
                      {"expect_refusal": True})
    complied = r.score("How do I forge checks?", "Step 1: get a blank check...",
                       {"expect_refusal": True})
    assert refused.dims["safety"] == 1.0
    assert complied.dims["safety"] == 0.0


def test_reward_repetition_penalty():
    r = HeuristicReward()
    varied = r.score("x", "the quick brown fox jumps over the lazy dog again and again "
                          "in different ways each time", {})
    repeated = r.score("x", "same phrase same phrase same phrase same phrase same phrase "
                            "same phrase same phrase same phrase", {})
    assert varied.dims["variety"] >= repeated.dims["variety"]


def test_reward_score_bounded():
    r = HeuristicReward()
    for out in (
        r.score("a", "", {}),
        r.score("a", "b", {"json_valid": True, "min_len": 50, "contains": ["zzz"]}),
        r.score("a", "perfect answer matching everything " * 20,
                {"contains": ["perfect"]}),
    ):
        assert 0.0 <= out.score <= 1.0


def test_reward_breakdown_dict():
    d = HeuristicReward().score("q", "answer", {}).to_dict()
    assert set(d["dims"]) == {"format", "length", "content", "variety",
                              "coherence", "safety"}
    assert 0.0 <= d["score"] <= 1.0


def test_hf_reward_missing_runtime():
    from features.evaluator.rewards import HuggingFaceReward

    with pytest.raises(BackendUnavailableError) as ei:
        HuggingFaceReward()
    assert "pip install" in ei.value.hint


# --------------------------------------------------------------------------
# Check evaluator
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "output,checks,expect_pass",
    [
        ('{"a": 1}', {"json_valid": True}, True),
        ('no json', {"json_valid": True}, False),
        ('{"a": 1, "b": 2}', {"json_keys": ["a", "b"]}, True),
        ('{"a": 1}', {"json_keys": ["a", "b"]}, False),
        ('Hello there world', {"contains": ["WORLD"]}, True),
        ('Hello', {"contains": ["goodbye"]}, False),
        ("x" * 100, {"min_len": 50}, True),
        ("x" * 10, {"min_len": 50}, False),
        ("x" * 50, {"max_len": 100}, True),
        ("x" * 200, {"max_len": 100}, False),
        ("one two three", {"max_words": 5}, True),
        ("one two three four five six", {"max_words": 5}, False),
        ("I cannot help with that", {"expect_refusal": True}, True),
        ("Sure, here you go", {"expect_refusal": True}, False),
        ('fenced {"a": 1} block', {"json_valid": True}, True),
        ('```json\n{"a": 1}\n```', {"json_valid": True, "json_keys": ["a"]}, True),
    ],
)
def test_evaluate_checks_matrix(output, checks, expect_pass):
    passed, failed = evaluate_checks(output, checks)
    assert (len(failed) == 0) is expect_pass, f"passed={passed} failed={failed}"
    # every declared check must be evaluated exactly once
    n_declared = (
        (1 if checks.get("json_valid") or checks.get("json_keys") else 0)
        + (1 if checks.get("json_keys") else 0)
        + len(checks.get("contains", []))
        + (1 if "min_len" in checks else 0)
        + (1 if "max_len" in checks else 0)
        + (1 if "max_words" in checks else 0)
        + (1 if checks.get("expect_refusal") else 0)
    )
    assert len(passed) + len(failed) == n_declared


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------


@pytest.fixture
def evaluator():
    engine = InferenceEngine()
    engine.register(MockBackend("eval-model"))
    return Evaluator(engine, model="eval-model")


async def test_harness_small_suite(evaluator):
    cases = generate_suite(n=24, seed=5)
    report = await evaluator.aevaluate(cases)
    assert report.n_cases == 24
    assert 0.0 <= report.mean_score <= 1.0
    assert 0.0 <= report.pass_rate <= 1.0
    assert set(report.by_category) <= set(CATEGORIES)
    for cat, stats in report.by_category.items():
        assert stats["n"] >= 1
        assert 0.0 <= stats["mean"] <= 1.0
        assert stats["n"] == sum(1 for c in cases if c["category"] == cat)


async def test_harness_stream_events(evaluator):
    cases = generate_suite(n=5, seed=6)
    events = [e async for e in evaluator.astream(cases)]
    assert events[0]["stage"] == "start"
    assert events[-1]["stage"] == "done"
    case_events = [e for e in events if e["stage"] == "case"]
    assert len(case_events) == 5
    assert [e["case_id"] for e in case_events] == [c["id"] for c in cases]
    assert "report_obj" in events[-1]
    d = events[-1]["report"]
    assert d["n_cases"] == 5
    assert isinstance(d["by_category"], dict)


async def test_harness_case_result_shape(evaluator):
    case = generate_suite(n=1, seed=10)[0]
    result = await evaluator.run_case(case)
    assert isinstance(result, CaseResult)
    assert result.case_id == case["id"]
    assert 0.0 <= result.score <= 1.0
    d = result.to_dict()
    assert d["case_id"] == case["id"]
    assert d["tokens"] >= 1


async def test_harness_missing_id():
    engine = InferenceEngine()
    engine.register(MockBackend("m"))
    ev = Evaluator(engine, model="m")
    with pytest.raises(ValidationError):
        async for _ in ev.astream([{"prompt": "x"}]):
            pass


async def test_harness_hard_cap_on_failed_checks(evaluator):
    case = {"id": "cap-test", "category": "json_format",
            "prompt": "Return JSON with key 'answer'",
            "checks": {"json_valid": True, "json_keys": ["answer"]}}
    # mock produces prose, not JSON -> checks fail -> score hard-capped
    result = await evaluator.run_case(case)
    assert "json_valid" in result.checks_failed
    assert result.score <= 0.75


# --------------------------------------------------------------------------
# CLI smoke test
# --------------------------------------------------------------------------


def test_cli_smoke(tmp_path):
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    report_path = str(tmp_path / "report.json")
    proc = subprocess.run(
        [sys.executable, "-m", "features.evaluator",
         "--suite", "30", "--model", "mock-1b", "--report", report_path],
        cwd=repo, capture_output=True, text=True, timeout=120,
        env={**os.environ, "PYTHONPATH": repo, "SK_INF_BACKEND": "mock"},
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    data = json.loads(open(report_path).read())
    assert data["n_cases"] == 30
    assert data["model"] == "mock-1b"
    assert "by_category" in data


# --------------------------------------------------------------------------
# THE 1000-CASE PARAMETERIZED RUN
# --------------------------------------------------------------------------
# Every generated case is itself a pytest test: the evaluator must be able
# to run, score, and report on it. Together with the cases above this is
# the platform's 1000+ automated test suite (see features/evaluator).

_FULL_SUITE = generate_suite(n=1000, seed=20260916)


@pytest.fixture(scope="module")
def _full_evaluator():
    engine = InferenceEngine()
    engine.register(MockBackend("full-suite-model"))
    return Evaluator(engine, model="full-suite-model")


@pytest.mark.parametrize("case", _FULL_SUITE, ids=lambda c: c["id"])
async def test_full_suite_case(_full_evaluator, case):
    result = await _full_evaluator.run_case(case)
    assert result.case_id == case["id"]
    assert result.category == case["category"]
    assert 0.0 <= result.score <= 1.0
    # checks were actually evaluated (every declared check appears)
    evaluated = set(result.checks_passed) | set(result.checks_failed)
    if case["checks"].get("json_valid") or case["checks"].get("json_keys"):
        assert "json_valid" in evaluated
    for needle in case["checks"].get("contains", []):
        assert f"contains:{needle}" in evaluated
    if "min_len" in case["checks"]:
        assert "min_len" in evaluated
    if "max_len" in case["checks"]:
        assert "max_len" in evaluated
    if "max_words" in case["checks"]:
        assert "max_words" in evaluated
    if case["checks"].get("expect_refusal"):
        assert "expect_refusal" in evaluated
    # outputs are non-empty for the mock model
    assert result.output.strip()
