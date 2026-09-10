"""V5 of the tool visualizer: the runtime overlay and the manifest linter.

Everything before this rendered *declarations*. This reads the ledger and puts
measurements beside them, which is what turns the visualizer into a diagnostic.
The tests hold it to the three rules that make a diagnostic safe to leave on:

* **absent by default** - no ledger, an empty one, a torn one, a directory
  where a file should be: all yield an empty overlay and a page that works
  exactly as it did before. A diagnostic that breaks the thing it diagnoses is
  worse than no diagnostic;
* **redacted** - ledger rows carry args and result previews, which is the one
  place a secret can reach the page;
* **read-only** - reading the overlay never writes to the ledger.

And the linter itself has to be *right*: it must fire on a tool that genuinely
under-declares its latency, and stay quiet on one that does not, and refuse to
draw conclusions from two data points.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import pytest

from skeletonkey.core.ledger import Ledger
from skeletonkey.core.manifest import ToolManifest
from skeletonkey.viz import build_viz_model
from skeletonkey.viz.runtime import (
    DRIFT_FACTOR,
    MIN_CALLS_FOR_DRIFT,
    attach_runtime,
    find_drift,
    percentile,
    read_runtime,
    recent_calls,
)

# ---------------------------------------------------------------- fake ledger

@dataclass
class Row:
    tool: str
    duration_ms: int = 10
    ok: bool = True
    error_code: str | None = None
    run_id: str = "r1"
    ts: float = 0.0
    args: dict[str, Any] = field(default_factory=dict)
    result_preview: str = ""
    task_id: str = ""


class FakeLedger:
    def __init__(self, rows: list[Row]) -> None:
        self.rows = rows
        self.reads = 0

    def read(self, **kwargs: Any):
        self.reads += 1
        return iter(self.rows)


@dataclass
class FakeNode:
    id: str
    typical_latency_ms: int = 50
    runtime: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeModel:
    nodes: list[FakeNode]
    runtime: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------ percentile

def test_percentile_is_nearest_rank_and_handles_edges():
    assert percentile([], 95) == 0.0
    assert percentile([7.0], 50) == 7.0
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50) == 5
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 100) == 10
    # order of input must not matter
    assert percentile([9, 1, 5, 3], 50) == percentile([1, 3, 5, 9], 50)


# --------------------------------------------------------------- reading rows

def test_read_runtime_folds_calls_per_tool():
    ledger = FakeLedger([
        Row("fs.read", 10), Row("fs.read", 20), Row("fs.read", 30),
        Row("fs.write", 5, ok=False, error_code="EPERM"),
    ])
    rt = read_runtime(ledger)
    assert set(rt) == {"fs.read", "fs.write"}
    assert rt["fs.read"].calls == 3
    assert rt["fs.read"].ok == 3
    assert rt["fs.read"].p50_ms == 20
    assert rt["fs.read"].max_ms == 30
    assert rt["fs.write"].failed == 1
    assert rt["fs.write"].error_codes == {"EPERM": 1}
    assert rt["fs.write"].success_rate == 0.0


def test_read_runtime_tracks_the_latest_run():
    ledger = FakeLedger([Row("fs.read", ts=100, run_id="old"),
                         Row("fs.read", ts=200, run_id="new"),
                         Row("fs.read", ts=150, run_id="middle")])
    assert read_runtime(ledger)["fs.read"].last_run_id == "new"


@pytest.mark.parametrize("bad", [None, "", "/nonexistent/path/ledger.ndjson"])
def test_a_missing_ledger_is_an_empty_overlay_not_an_error(bad):
    assert read_runtime(bad) == {}
    assert recent_calls(bad) == []


def test_an_unreadable_ledger_degrades_quietly(tmp_path):
    """A directory where a file should be, and a ledger that raises."""
    assert read_runtime(str(tmp_path)) == {}

    class Exploding:
        def read(self, **kwargs):
            raise OSError("disk went away")

    assert read_runtime(Exploding()) == {}
    assert recent_calls(Exploding()) == []


def test_a_torn_ledger_file_yields_what_it_can(tmp_path):
    path = tmp_path / "ledger.ndjson"
    good = json.dumps({"seq": 1, "ts": 1.0, "tool": "fs.read", "ok": True,
                       "duration_ms": 5})
    path.write_text(good + "\n{ this line is truncated", encoding="utf-8")
    rt = read_runtime(str(path))
    assert "fs.read" in rt and rt["fs.read"].calls == 1


def test_rows_without_a_tool_are_ignored():
    with_blank = read_runtime(FakeLedger([Row(""), Row("fs.read")]))
    without = read_runtime(FakeLedger([Row("fs.read")]))
    assert set(with_blank) == set(without) == {"fs.read"}


# ----------------------------------------------------------------- the linter

def test_drift_fires_when_p95_outruns_the_declared_latency():
    model = FakeModel([FakeNode("slow.tool", typical_latency_ms=10)])
    ledger = FakeLedger([Row("slow.tool", 500)] * 10)
    drift = find_drift(model, read_runtime(ledger))
    assert len(drift) == 1
    assert drift[0].kind == "latency"
    assert drift[0].declared == 10
    assert drift[0].ratio == pytest.approx(50.0)
    assert "p95" in drift[0].detail


def test_drift_is_quiet_when_the_manifest_is_honest():
    model = FakeModel([FakeNode("honest.tool", typical_latency_ms=100)])
    ledger = FakeLedger([Row("honest.tool", 90)] * 10)
    assert find_drift(model, read_runtime(ledger)) == []


def test_drift_needs_evidence_before_it_accuses():
    """Two slow calls are an anecdote, not a mis-declared manifest."""
    model = FakeModel([FakeNode("rare.tool", typical_latency_ms=1)])
    few = FakeLedger([Row("rare.tool", 900)] * (MIN_CALLS_FOR_DRIFT - 1))
    many = FakeLedger([Row("rare.tool", 900)] * MIN_CALLS_FOR_DRIFT)
    assert find_drift(model, read_runtime(few)) == []
    assert find_drift(model, read_runtime(many))


def test_drift_tolerance_is_the_declared_factor():
    model = FakeModel([FakeNode("edge.tool", typical_latency_ms=100)])
    under = FakeLedger([Row("edge.tool", int(100 * DRIFT_FACTOR) - 5)] * 10)
    over = FakeLedger([Row("edge.tool", int(100 * DRIFT_FACTOR) + 50)] * 10)
    assert find_drift(model, read_runtime(under)) == []
    assert find_drift(model, read_runtime(over))


def test_reliability_drift_reports_a_failing_tool():
    model = FakeModel([FakeNode("flaky.tool")])
    rows = [Row("flaky.tool", 5, ok=False, error_code="EIO")] * 8 + \
           [Row("flaky.tool", 5)] * 2
    drift = find_drift(model, read_runtime(FakeLedger(rows)))
    kinds = {d.kind for d in drift}
    assert "reliability" in kinds
    row = next(d for d in drift if d.kind == "reliability")
    assert "EIO" in row.detail
    assert row.calls == 10


def test_a_tool_with_no_declared_latency_is_not_accused():
    model = FakeModel([FakeNode("unknown.cost", typical_latency_ms=0)])
    ledger = FakeLedger([Row("unknown.cost", 5000)] * 10)
    assert [d for d in find_drift(model, read_runtime(ledger))
            if d.kind == "latency"] == []


def test_drift_is_deterministic_and_sorted():
    model = FakeModel([FakeNode("b.tool", 1), FakeNode("a.tool", 1)])
    ledger = FakeLedger([Row("a.tool", 900)] * 10 + [Row("b.tool", 900)] * 10)
    ids = [d.id for d in find_drift(model, read_runtime(ledger))]
    assert ids == sorted(ids)


# ---------------------------------------------------------------- attachment

def test_attach_runtime_annotates_nodes_and_summarises(toolkit):
    model = build_viz_model(toolkit.engine.registry)
    ledger = FakeLedger([Row("fs.read", 12)] * 4 + [Row("fs.list", 3)])
    summary = attach_runtime(model, ledger)

    assert summary["available"] is True
    assert summary["calls"] == 5
    assert summary["tools_called"] == 2
    assert model.node("fs.read").runtime["calls"] == 4
    # a tool nobody called carries an empty overlay, not a fake zero row
    assert model.node("registry.list").runtime == {}
    assert model.runtime["busiest"][0]["id"] == "fs.read"


def test_attach_runtime_with_no_ledger_leaves_the_model_usable(toolkit):
    model = build_viz_model(toolkit.engine.registry)
    summary = attach_runtime(model, None)
    assert summary["available"] is False
    assert summary["drift"] == []
    assert all(n.runtime == {} for n in model.nodes)
    # and it still serialises exactly like a V0-V4 model
    assert "runtime" not in model.to_json()["nodes"][0]


def test_an_overlay_free_model_serialises_as_before(toolkit):
    """V5 must not change the document for anyone who did not ask for it."""
    model = build_viz_model(toolkit.engine.registry)
    doc = model.to_json()
    assert "runtime" not in doc
    for node in doc["nodes"]:
        assert "runtime" not in node


def test_overlay_appears_in_the_json_once_attached(toolkit):
    model = build_viz_model(toolkit.engine.registry)
    attach_runtime(model, FakeLedger([Row("fs.read", 9)] * 3))
    doc = model.to_json()
    assert doc["runtime"]["available"] is True
    node = next(n for n in doc["nodes"] if n["id"] == "fs.read")
    assert node["runtime"]["calls"] == 3


def test_orphans_are_surfaced(toolkit):
    """A ledger that remembers a tool the registry no longer has is a clue."""
    model = build_viz_model(toolkit.engine.registry)
    summary = attach_runtime(model, FakeLedger([Row("gone.forever", 5)]))
    assert summary["orphans"] == ["gone.forever"]


def test_attaching_does_not_mutate_the_registry(toolkit):
    reg = toolkit.engine.registry
    before = reg.advertise().digest
    model = build_viz_model(reg)
    attach_runtime(model, FakeLedger([Row("fs.read", 5)] * 3))
    assert reg.advertise().digest == before


# ------------------------------------------------------------------- secrets

_CREDENTIALS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]+"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]


def test_credentials_in_the_ledger_never_reach_the_overlay(toolkit):
    secret = "ghp_" + "z" * 36
    ledger = FakeLedger([
        Row("fs.read", 5, args={"token": secret, "authorization": f"Bearer {secret}"},
            result_preview=f"logged in with {secret} ok"),
    ])
    model = build_viz_model(toolkit.engine.registry)
    attach_runtime(model, ledger)
    blob = json.dumps(model.to_json())
    for pattern in _CREDENTIALS:
        assert not pattern.search(blob), f"credential leaked: {pattern.pattern}"
    assert secret not in blob


def test_recent_calls_are_redacted_and_bounded():
    rows = [Row("fs.read", 1, args={"password": "hunter2"},
                result_preview="x" * 5000)] * 100
    feed = recent_calls(FakeLedger(rows), limit=10)
    assert len(feed) == 10
    for row in feed:
        assert len(row["preview"]) <= 220
        assert "hunter2" not in json.dumps(row)


# ------------------------------------------------- against a real ledger file

@pytest.fixture
def exercised(toolkit, workspace):
    """A toolkit whose ledger has real, measured calls in it."""
    for _ in range(6):
        toolkit.engine.call("fs.read", {"path": "README.md"})
    for _ in range(3):
        toolkit.engine.call("fs.read", {"path": "does-not-exist.txt"})
    return toolkit


def test_a_real_ledger_reads_back(exercised):
    if exercised.ledger is None:
        pytest.skip("this toolkit has no ledger configured")
    rt = read_runtime(exercised.ledger)
    assert rt["fs.read"].calls == 9
    assert rt["fs.read"].failed == 3
    assert rt["fs.read"].ok == 6
    assert rt["fs.read"].error_codes


def test_a_real_slow_tool_is_caught_by_the_linter(exercised):
    """End to end: register a tool that lies about its cost, call it, lint it."""
    if exercised.ledger is None:
        pytest.skip("this toolkit has no ledger configured")
    exercised.engine.registry.register(
        ToolManifest(id="viz.liar", description="Claims to be instant.",
                     risk="none", typical_latency_ms=1),
        handler=lambda **kw: (time.sleep(0.02), {"done": True})[1],
    )
    for _ in range(MIN_CALLS_FOR_DRIFT + 1):
        exercised.engine.call("viz.liar", {})

    model = build_viz_model(exercised.engine.registry)
    summary = attach_runtime(model, exercised.ledger)
    drift = [d for d in summary["drift"] if d["id"] == "viz.liar"]
    assert drift, f"the linter missed a 20x under-declaration: {summary['drift']}"
    assert drift[0]["kind"] == "latency"
    assert drift[0]["ratio"] > DRIFT_FACTOR


def test_reading_never_writes_to_the_ledger(exercised):
    if exercised.ledger is None:
        pytest.skip("this toolkit has no ledger configured")
    path = exercised.ledger.path
    before = (os.path.getsize(path), os.path.getmtime(path))
    model = build_viz_model(exercised.engine.registry)
    attach_runtime(model, exercised.ledger)
    read_runtime(exercised.ledger)
    recent_calls(exercised.ledger)
    assert (os.path.getsize(path), os.path.getmtime(path)) == before


def test_the_ledger_hash_chain_still_verifies_after_reading(exercised):
    if exercised.ledger is None:
        pytest.skip("this toolkit has no ledger configured")
    model = build_viz_model(exercised.engine.registry)
    attach_runtime(model, exercised.ledger)
    assert exercised.ledger.verify()["valid"] is True


def test_a_ledger_path_string_works_as_well_as_an_object(exercised):
    if exercised.ledger is None:
        pytest.skip("this toolkit has no ledger configured")
    by_object = read_runtime(exercised.ledger)
    by_path = read_runtime(exercised.ledger.path)
    assert {k: v.calls for k, v in by_object.items()} == \
           {k: v.calls for k, v in by_path.items()}


# --------------------------------------------------------------- page + routes

def test_the_page_renders_the_runtime_view(toolkit):
    from skeletonkey.viz import render_html

    model = build_viz_model(toolkit.engine.registry)
    attach_runtime(model, FakeLedger([Row("fs.read", 12)] * 6))
    page = render_html(model)
    assert 'id="view-runtime"' in page
    assert 'data-view="runtime"' in page
    assert "measured, per tool" in page


def test_the_runtime_view_is_honest_when_there_is_nothing_to_show(toolkit):
    from skeletonkey.viz import render_html

    page = render_html(build_viz_model(toolkit.engine.registry))
    assert 'id="view-runtime"' in page
    assert "No ledger activity yet" in page


def test_drift_count_shows_as_a_pip_on_the_tab(toolkit):
    from skeletonkey.viz import render_html

    model = build_viz_model(toolkit.engine.registry)
    # fs.read declares a small latency; 900ms x10 is unambiguous drift
    attach_runtime(model, FakeLedger([Row("fs.read", 900)] * 10))
    page = render_html(model)
    assert 'class="pip"' in page
    assert "contract drift" in page


def test_server_runtime_route(toolkit, tmp_path):
    import urllib.request

    from skeletonkey.viz.server import VizServer

    path = tmp_path / "ledger.ndjson"
    Ledger(str(path)).append(tool="fs.read", ok=True, duration_ms=7, args={})
    with VizServer(toolkit.engine.registry, port=0, ledger=str(path)) as srv:
        with urllib.request.urlopen(srv.url + "api/runtime", timeout=10) as resp:
            doc = json.load(resp)
        assert doc["available"] is True
        assert doc["calls"] >= 1


def test_server_without_a_ledger_reports_no_overlay(toolkit):
    import urllib.request

    from skeletonkey.viz.server import VizServer

    with VizServer(toolkit.engine.registry, port=0) as srv:
        with urllib.request.urlopen(srv.url + "api/runtime", timeout=10) as resp:
            doc = json.load(resp)
        assert doc["available"] is False
        # and the page is still perfectly usable
        with urllib.request.urlopen(srv.url, timeout=10) as resp:
            assert b"view-runtime" in resp.read()
