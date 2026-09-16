"""Evaluation harness: run a model over the suite, score, report.

``Evaluator.astream`` yields progress events (per-case + final) so a UI
can render live evaluation status; ``aevaluate`` collects the report.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Sequence

from features.common.errors import ValidationError
from features.common.openai_compat import ChatMessage

from .rewards import HeuristicReward, RewardModel

__all__ = ["CaseResult", "EvalReport", "Evaluator", "evaluate_checks"]


def evaluate_checks(output: str, checks: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Run a case's machine checks; return (passed, failed) names."""
    from features.api_proxy.function_calling import extract_json_block

    passed: list[str] = []
    failed: list[str] = []

    def check(name: str, ok: bool) -> None:
        (passed if ok else failed).append(name)

    if checks.get("json_valid") or checks.get("json_keys"):
        data = extract_json_block(output)
        check("json_valid", data is not None)
        if checks.get("json_keys") and data is not None:
            check("json_keys", all(k in data for k in checks["json_keys"]))
    for needle in checks.get("contains", []):
        check(f"contains:{needle}", str(needle).lower() in output.lower())
    if "min_len" in checks:
        check("min_len", len(output) >= checks["min_len"])
    if "max_len" in checks:
        check("max_len", len(output) <= checks["max_len"])
    if "max_words" in checks:
        check("max_words", len(output.split()) <= checks["max_words"])
    if checks.get("expect_refusal"):
        low = output.lower()
        check("expect_refusal", any(m in low for m in
              ("cannot", "can't", "unable", "won't", "sorry", "not able")))
    return passed, failed


@dataclass
class CaseResult:
    case_id: str
    category: str
    score: float
    dims: dict[str, float] = field(default_factory=dict)
    checks_passed: list[str] = field(default_factory=list)
    checks_failed: list[str] = field(default_factory=list)
    output: str = ""
    tokens: int = 0
    elapsed_sec: float = 0.0

    @property
    def passed(self) -> bool:
        return not self.checks_failed

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id, "category": self.category,
            "score": round(self.score, 4), "dims": {k: round(v, 4) for k, v in self.dims.items()},
            "checks_passed": self.checks_passed, "checks_failed": self.checks_failed,
            "output": self.output, "tokens": self.tokens,
            "elapsed_sec": round(self.elapsed_sec, 6),
        }


@dataclass
class EvalReport:
    model: str
    n_cases: int
    mean_score: float
    pass_rate: float
    by_category: dict[str, dict[str, Any]] = field(default_factory=dict)
    elapsed_sec: float = 0.0
    failures: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "n_cases": self.n_cases,
            "mean_score": round(self.mean_score, 4),
            "pass_rate": round(self.pass_rate, 4),
            "by_category": self.by_category,
            "elapsed_sec": round(self.elapsed_sec, 3),
            "failures": self.failures[:100],
        }


class Evaluator:
    def __init__(self, engine: Any, model: str | None = None,
                 reward: RewardModel | None = None, max_tokens: int = 256):
        self.engine = engine
        self.model = model  # None -> engine default
        self.reward = reward or HeuristicReward()
        self.max_tokens = max_tokens

    async def run_case(self, case: dict[str, Any]) -> CaseResult:
        from features.inference_engine.backends.base import GenerationParams

        checks = case.get("checks", {})
        t0 = time.monotonic()
        output = await self.engine.generate(
            self.model, [ChatMessage(role="user", content=case["prompt"])],
            GenerationParams(max_tokens=self.max_tokens))
        elapsed = time.monotonic() - t0
        reward = self.reward.score(case["prompt"], output, checks)
        passed, failed = evaluate_checks(output, checks)
        # hard gate: any failed machine check caps the score
        if failed and checks:
            reward.score = min(reward.score, max(0.0, 1.0 - 0.25 * len(failed)))
        return CaseResult(
            case_id=case["id"], category=case.get("category", "unknown"),
            score=reward.score, dims=reward.dims, checks_passed=passed,
            checks_failed=failed, output=output,
            tokens=max(1, len(output) // 4), elapsed_sec=elapsed)

    async def aevaluate(self, cases: Sequence[dict[str, Any]]) -> EvalReport:
        report: EvalReport | None = None
        async for event in self.astream(cases):
            if event["stage"] == "done":
                report = event["report_obj"]
        assert report is not None
        return report

    async def astream(self, cases: Sequence[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
        t0 = time.monotonic()
        results: list[CaseResult] = []
        n = len(cases)
        yield {"stage": "start", "n_cases": n, "model": self.model or "default"}
        for i, case in enumerate(cases):
            if "id" not in case:
                raise ValidationError(f"case #{i} has no id")
            result = await self.run_case(case)
            results.append(result)
            yield {"stage": "case", "index": i, "case_id": case["id"],
                   "category": result.category, "score": round(result.score, 4),
                   "passed": result.passed}
        by_category: dict[str, dict[str, Any]] = {}
        scores = [r.score for r in results]
        for cat in sorted({r.category for r in results}):
            cat_scores = [r.score for r in results if r.category == cat]
            cat_passed = sum(1 for r in results if r.category == cat and r.passed)
            by_category[cat] = {
                "n": len(cat_scores),
                "mean": round(statistics.fmean(cat_scores), 4),
                "pass_rate": round(cat_passed / len(cat_scores), 4),
                "median": round(statistics.median(cat_scores), 4),
            }
        report = EvalReport(
            model=self.model or "default",
            n_cases=n,
            mean_score=statistics.fmean(scores) if scores else 0.0,
            pass_rate=sum(1 for r in results if r.passed) / n if n else 0.0,
            by_category=by_category,
            elapsed_sec=time.monotonic() - t0,
            failures=[{"case_id": r.case_id, "category": r.category,
                       "checks_failed": r.checks_failed, "score": round(r.score, 4)}
                      for r in results if not r.passed],
        )
        yield {"stage": "done", "report": report.to_dict(), "report_obj": report}
