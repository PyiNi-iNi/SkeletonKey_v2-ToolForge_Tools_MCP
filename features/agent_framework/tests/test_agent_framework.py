"""Integration tests for features.agent_framework.

Note: the code-execution tests spawn real subprocesses of the *current*
interpreter in isolated mode — safe by construction (no host env, no
site-packages, timeout kill).
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from features.agent_framework import (
    Agent,
    AutoGenConversation,
    CodeAgent,
    CSVAgent,
    DuckDuckGoSearch,
    MockSearch,
    PythonExecAgent,
    SearchAgent,
)
from features.common.errors import ValidationError
from features.inference_engine import InferenceEngine, MockBackend


# --------------------------------------------------------------------------
# PythonExecAgent
# --------------------------------------------------------------------------


async def test_exec_success():
    agent = PythonExecAgent(timeout=10)
    report = await agent.run("print('hello from sandbox')")
    assert report.ok
    assert report.exit_code == 0
    assert "hello from sandbox" in report.stdout
    assert report.timed_out is False


async def test_exec_failure_captures_stderr():
    agent = PythonExecAgent(timeout=10)
    report = await agent.run("import sys; sys.stderr.write('boom\\n'); raise ValueError('nope')")
    assert not report.ok
    assert report.exit_code != 0
    assert "boom" in report.stderr
    assert "ValueError" in report.stderr


async def test_exec_timeout_kills():
    agent = PythonExecAgent(timeout=1)
    report = await agent.run("import time; time.sleep(30); print('never')")
    assert report.timed_out is True
    assert not report.ok
    assert "never" not in report.stdout
    assert report.duration_sec < 10


async def test_exec_isolation_no_site_packages():
    # -I isolation: pytest is installed in the venv but must NOT be importable
    agent = PythonExecAgent(timeout=10)
    report = await agent.run("import pytest")
    assert not report.ok
    assert "ModuleNotFoundError" in report.stderr


async def test_exec_minimal_env():
    agent = PythonExecAgent(timeout=10)
    report = await agent.run("import os; print(os.environ.get('SK_TEST_SECRET', 'ABSENT'))")
    assert "ABSENT" in report.stdout  # host env is not inherited


async def test_exec_workdir(tmp_path):
    agent = PythonExecAgent(timeout=10, workdir=str(tmp_path))
    report = await agent.run("import os; print(os.getcwd())")
    assert report.ok
    assert os.path.realpath(str(tmp_path)) in os.path.realpath(report.stdout.strip()) or \
        str(tmp_path) in report.stdout


def test_exec_report_dict():
    from features.agent_framework.exec_agent import ExecReport

    r = ExecReport(0, "out", "err", 0.5, code="x")
    d = r.to_dict()
    assert d["ok"] is True and d["exit_code"] == 0
    assert d["timed_out"] is False


# --------------------------------------------------------------------------
# CodeAgent (generate → execute → feedback)
# --------------------------------------------------------------------------


async def test_code_agent_happy_path():
    engine = InferenceEngine()
    mock = MockBackend("mock-1b")
    mock.script('```python\nprint(2 + 2)\n```')
    engine.register(mock)
    agent = CodeAgent(engine, PythonExecAgent(timeout=10))
    report = await agent.solve("print the sum of 2 and 2", model="mock-1b")
    assert report.success
    assert report.rounds == 1
    assert "4" in report.report.stdout


async def test_code_agent_recovers_from_failure():
    engine = InferenceEngine()
    mock = MockBackend("mock-1b")
    # round 1: syntax error; round 2: fixed
    mock.script('```python\ndef broken(:\n    pass\n```')
    mock.script('```python\nprint("fixed")\n```')
    engine.register(mock)
    agent = CodeAgent(engine, PythonExecAgent(timeout=10))
    report = await agent.solve("do a thing", model="mock-1b")
    assert report.success
    assert report.rounds == 2
    assert "fixed" in report.report.stdout
    # history carries the failure feedback
    assert report.history[0]["exit_code"] != 0
    assert "SyntaxError" in report.history[0]["stderr"] or "invalid syntax" in report.history[0]["stderr"]


async def test_code_agent_exhausts_rounds():
    engine = InferenceEngine()
    mock = MockBackend("mock-1b")
    for _ in range(5):
        mock.script('```python\nraise RuntimeError("always fails")\n```')
    engine.register(mock)
    agent = CodeAgent(engine, PythonExecAgent(timeout=10), max_rounds=3)
    report = await agent.solve("impossible", model="mock-1b")
    assert not report.success
    assert report.rounds == 3
    assert len(report.history) == 3


def test_code_agent_extract_code():
    text = 'Here you go:\n```python\nprint(1)\n```\nHope that helps.'
    assert CodeAgent.extract_code(text) == "print(1)"
    assert CodeAgent.extract_code("no block here") == "no block here"


# --------------------------------------------------------------------------
# CSVAgent
# --------------------------------------------------------------------------

CSV_TEXT = """name,age,city,salary
alice,34,berlin,52000
bob,28,paris,48000
carol,41,berlin,61000
dave,22,rome,
eve,39,paris,55000
"""


@pytest.fixture
def csv():
    return CSVAgent(text=CSV_TEXT).load()


def test_csv_describe(csv):
    d = csv.describe()
    assert d["rows"] == 5 and d["columns"] == 4
    by_name = {c["name"]: c for c in d["column_profiles"]}
    assert by_name["age"]["dtype"] == "number"
    assert by_name["age"]["min"] == 22 and by_name["age"]["max"] == 41
    assert by_name["salary"]["nulls"] == 1
    assert by_name["name"]["dtype"] == "text"
    assert by_name["name"]["distinct"] == 5


def test_csv_query_simple(csv):
    rows = csv.query("city == 'berlin'")
    assert [r["name"] for r in rows] == ["alice", "carol"]


def test_csv_query_numeric_and(csv):
    rows = csv.query("age > 30 and city == 'paris'")
    assert [r["name"] for r in rows] == ["eve"]


def test_csv_query_or_semantics(csv):
    rows = csv.query("city == 'rome' or age > 40")
    names = {r["name"] for r in rows}
    assert names == {"dave", "carol"}


def test_csv_query_contains_and_in(csv):
    assert [r["name"] for r in csv.query("city contains 'ari'")] == ["bob", "eve"]
    assert [r["name"] for r in csv.query("city in 'rome, berlin'")] == ["alice", "carol", "dave"]


def test_csv_query_limit(csv):
    assert len(csv.query("age > 20", limit=2)) == 2


def test_csv_query_all(csv):
    assert len(csv.query("*")) == 5
    assert len(csv.query("")) == 5


def test_csv_query_bad_expression(csv):
    with pytest.raises(ValidationError):
        csv.query("age ~~ 30")


def test_csv_aggregate(csv):
    out = csv.aggregate("city", {"salary": "sum"})
    by_city = {r["city"]: r for r in out}
    assert by_city["berlin"]["salary_sum"] == 113000
    assert by_city["berlin"]["count"] == 2
    # rome has no salary -> None
    assert by_city["rome"]["salary_sum"] is None
    means = csv.aggregate("city", {"salary": "mean"})
    berlin_mean = {r["city"]: r for r in means}["berlin"]["salary_mean"]
    assert berlin_mean == pytest.approx((52000 + 61000) / 2)


def test_csv_aggregate_unknown_column(csv):
    with pytest.raises(ValidationError):
        csv.aggregate("city", {"nope": "sum"})
    with pytest.raises(ValidationError):
        csv.aggregate("nope", {"age": "sum"})


async def test_csv_async_facade(csv):
    rows = await csv.aquery("city == 'berlin'")
    assert len(rows) == 2
    agg = await csv.aaggregate("city", {"age": "mean"})
    assert agg
    d = await csv.adescribe()
    assert d["rows"] == 5


def test_csv_from_path(tmp_path):
    p = tmp_path / "data.csv"
    p.write_text(CSV_TEXT)
    csv = CSVAgent(path=str(p))
    assert csv.describe()["rows"] == 5


def test_csv_requires_input():
    with pytest.raises(ValidationError):
        CSVAgent()


# --------------------------------------------------------------------------
# Web search
# --------------------------------------------------------------------------

DDG_HTML = """
<html><body>
<div class="result">
  <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fone">First Title</a>
  <a class="result__snippet" href="#">First snippet about <b>vectors</b>.</a>
</div>
<div class="result">
  <a rel="nofollow" class="result__a" href="https://example.com/two?q=1">Second Title</a>
  <a class="result__snippet" href="#">Second snippet.</a>
</div>
</body></html>
"""


def test_ddg_parser():
    results = DuckDuckGoSearch.parse(DDG_HTML, max_results=5)
    assert len(results) == 2
    assert results[0].title == "First Title"
    assert results[0].url == "https://example.com/one"  # uddg redirect resolved
    assert "vectors" in results[0].snippet
    assert "<b>" not in results[0].snippet  # tags stripped
    assert results[1].url == "https://example.com/two?q=1"


async def test_ddg_empty_query():
    with pytest.raises(ValidationError):
        await DuckDuckGoSearch().search("   ", 3)


async def test_mock_search_deterministic():
    s = MockSearch()
    a = await s.search("attention sinks", 3)
    b = await s.search("attention sinks", 3)
    assert a == b
    assert len(a) == 3
    assert all(r.url.startswith("http") for r in a)
    assert all(r.title for r in a)


async def test_search_agent_grounds_answer():
    engine = InferenceEngine()
    mock = MockBackend("mock-1b")
    mock.script("According to [1], sinks retain initial tokens. Sources: [1] example.com")
    engine.register(mock)
    agent = SearchAgent(engine, MockSearch())
    result = await agent.ask("What are attention sinks?", model="mock-1b")
    assert "[1]" in result["answer"]
    assert len(result["sources"]) == 5
    assert result["sources"][0]["url"].startswith("http")


async def test_search_agent_mock_captures_prompt():
    engine = InferenceEngine()
    mock = MockBackend("mock-1b")
    captured = {}

    def spy(messages, params):
        captured["n"] = len(messages)
        captured["user"] = messages[-1].content
        return "ok"

    mock.respond_fn = spy
    engine.register(mock)
    await SearchAgent(engine, MockSearch()).ask("test question", model="mock-1b")
    assert "[1]" in captured["user"]
    assert "test question" in captured["user"]


# --------------------------------------------------------------------------
# AutoGen conversation
# --------------------------------------------------------------------------


async def test_autogen_terminates_on_token():
    engine = InferenceEngine()
    coder = MockBackend("coder-model")
    coder.script("I wrote the code.")
    coder.script("I approve. APPROVE")
    reviewer = MockBackend("reviewer-model")
    reviewer.script("Looks good so far.")
    engine.register(coder).register(reviewer)
    talk = AutoGenConversation([
        Agent("coder", engine, model="coder-model"),
        Agent("reviewer", engine, model="reviewer-model"),
    ], max_rounds=6)
    conv = await talk.run("build a rate limiter")
    assert conv.terminated is True
    assert "APPROVE" in conv.termination_reason
    assert conv.rounds == 3  # coder, reviewer, coder
    assert [m.sender for m in conv.messages] == ["coder", "reviewer", "coder"]


async def test_autogen_max_rounds():
    engine = InferenceEngine()
    a = MockBackend("m-a")
    b = MockBackend("m-b")
    for _ in range(10):
        a.script("not yet")
        b.script("still going")
    engine.register(a).register(b)
    talk = AutoGenConversation([Agent("A", engine, "m-a"),
                                Agent("B", engine, "m-b")], max_rounds=4)
    conv = await talk.run("loop")
    assert conv.terminated is False
    assert conv.rounds == 4
    assert conv.termination_reason == "max_rounds"


async def test_autogen_predicate_stop():
    engine = InferenceEngine()
    a = MockBackend("m-a")
    a.script("STOP-HERE")
    engine.register(a)
    talk = AutoGenConversation([Agent("A", engine, "m-a")], max_rounds=5,
                               should_stop=lambda msgs: any("STOP" in m.content for m in msgs))
    conv = await talk.run("stop test")
    assert conv.terminated is True
    assert conv.termination_reason == "predicate"
    assert conv.rounds == 1


async def test_autogen_requires_agents():
    with pytest.raises(ValueError):
        AutoGenConversation([])


def test_autogen_result_dict():
    from features.agent_framework.autogen import ConversationResult, Message

    r = ConversationResult(task="t", messages=[Message("a", "hi")], rounds=1,
                           terminated=True, termination_reason="token 'APPROVE'")
    d = r.to_dict()
    assert d["messages"][0]["sender"] == "a"
    assert d["terminated"] is True
