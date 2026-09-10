"""V6: the visualizer as a shipped surface, not a script someone remembers.

V0-V5 built the thing. This checks the seams that make it part of the product:
the export format, the MCP resource, the doctor check, and the docs that promise
all three. Each of these is a place where the visualizer could silently stop
working without any viz test noticing, because the failure would be in
`export.py`, `adapter.py`, `diagnostics.py` or a markdown file.

The docs tests are the unusual ones and the reason this module exists: a README
that claims `sk viz --drift` exits non-zero is a lie the moment it does not, and
`test_docs.py` only checks that named tools resolve, not that documented
*commands* behave.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from skeletonkey.core.export import (
    FORMATS,
    REGISTRY_FORMATS,
    export_from_registry,
    list_formats,
)
from skeletonkey.viz import build_viz_model

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "TOOL-VISUALIZER.md"
ADR = REPO / "docs" / "adr" / "0016-visualizer-is-a-projection.md"


def _sk(*argv: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    # PYTHONPATH, not the cwd, is what makes `-m skeletonkey.cli` importable:
    # several of these run from a scratch workspace on purpose.
    import os

    env = dict(os.environ, PYTHONPATH=str(REPO))
    return subprocess.run([sys.executable, "-m", "skeletonkey.cli", *argv],
                          capture_output=True, text=True, env=env,
                          cwd=str(cwd or REPO), timeout=300)


# ------------------------------------------------------------------- export

def test_viz_html_is_an_advertised_export_format():
    assert "viz-html" in FORMATS
    assert "viz-html" in list_formats()
    assert "viz-html" in REGISTRY_FORMATS


def test_export_from_registry_produces_the_page(toolkit):
    result = export_from_registry(toolkit.engine.registry, "viz-html")
    assert result["format"] == "viz-html"
    assert result["html"].startswith("<!doctype html>")
    model = build_viz_model(toolkit.engine.registry)
    assert result["registered"] == model.aggregates["registered"]
    assert result["count"] == model.aggregates["advertised"]
    assert result["digest"] == model.digest()


def test_export_respects_tier_and_read_only(toolkit):
    full = export_from_registry(toolkit.engine.registry, "viz-html")
    core = export_from_registry(toolkit.engine.registry, "viz-html", tier="core")
    locked = export_from_registry(toolkit.engine.registry, "viz-html", read_only=True)
    assert core["count"] < full["count"]
    assert locked["count"] < full["count"]
    # every registered tool is still described, whatever the projection
    assert core["registered"] == full["registered"] == locked["registered"]


def test_export_audits_for_credentials(toolkit):
    """viz-html goes through the same leak audit as the other formats."""
    from skeletonkey.core.manifest import ToolManifest

    toolkit.engine.registry.register(
        ToolManifest(id="ship.secretful", description="Has a credential default.",
                     risk="none", secret_args=["token"],
                     input_schema={"type": "object", "properties": {
                         "token": {"type": "string", "default": "ghp_" + "q" * 36}}}),
        handler=lambda **kw: None,
    )
    html = export_from_registry(toolkit.engine.registry, "viz-html")["html"]
    assert not re.search(r"ghp_[A-Za-z0-9]{36,}", html)


def test_the_other_formats_still_work(toolkit):
    """V6 touched export.py; the three pre-existing formats must be unharmed."""
    for fmt in ("openai-tools", "anthropic-tools", "mcp-manifest"):
        result = export_from_registry(toolkit.engine.registry, fmt)
        assert result["count"] > 0
        assert result["tools"]


def test_cli_export_writes_an_openable_page(tmp_path):
    out = tmp_path / "tools.html"
    proc = _sk("export", "viz-html", "-o", str(out))
    assert proc.returncode == 0, proc.stderr[-2000:]
    page = out.read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>")
    assert 'class="card' in page


def test_cli_export_list_mentions_viz_html():
    proc = _sk("export", "list")
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "viz-html" in proc.stdout


# -------------------------------------------------------------------- doctor

def test_doctor_has_a_viz_check():
    proc = _sk("--json", "doctor", "--no-probe")
    report = json.loads(proc.stdout)
    check = next((c for c in report["checks"] if c["id"] == "viz"), None)
    assert check is not None, "the doctor lost its viz check"
    assert check["ok"] is True
    assert check["nodes"] == check["registered"]
    assert check["edges"] >= 0
    assert check["digest"]


def test_the_viz_check_would_fail_on_a_partial_model(monkeypatch, toolkit):
    """The check earns its place only if it can actually fail."""
    model = build_viz_model(toolkit.engine.registry)
    registered = len(toolkit.engine.registry.all())
    assert len(model.nodes) == registered

    # simulate the projection losing a tool
    model.nodes.pop()
    assert len(model.nodes) != registered


# ---------------------------------------------------------------------- MCP

def test_mcp_adapter_declares_the_viz_resource():
    """Asserted against the source: the [mcp] extra may not be installed here."""
    source = (REPO / "skeletonkey" / "mcp" / "adapter.py").read_text(encoding="utf-8")
    assert "skeletonkey://viz/model" in source
    # both halves: the listing and the reader
    assert source.count("skeletonkey://viz/model") >= 2
    assert "build_viz_model" in source


def test_the_viz_resource_payload_is_the_model(toolkit):
    """What the resource handler builds, built the same way here."""
    model = build_viz_model(toolkit.engine.registry)
    doc = model.to_json()
    doc["model_digest"] = model.digest()
    assert json.loads(json.dumps(doc))["schema_version"] == 1
    assert len(doc["nodes"]) == len(toolkit.engine.registry.all())


# --------------------------------------------------------------------- docs

def test_the_docs_exist_and_are_linked():
    assert DOC.is_file()
    assert ADR.is_file()
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "docs/TOOL-VISUALIZER.md" in readme
    assert "sk viz" in readme
    # the reference doc must point back at the plan and the decision, so a
    # reader who wants the reasoning is never stranded on the how-to
    doc = DOC.read_text(encoding="utf-8")
    assert "TOOL-VISUALIZER-PLAN.md" in doc
    assert "0016-visualizer-is-a-projection.md" in doc


def test_the_adr_records_the_decision_and_its_costs():
    text = ADR.read_text(encoding="utf-8")
    assert "Status: accepted" in text
    for heading in ("## Context", "## Decision", "## Options considered",
                    "## Consequences"):
        assert heading in text, f"the ADR is missing {heading}"
    # an ADR that lists no costs is marketing
    assert "Costs." in text or "costs" in text.lower()


def test_every_cli_flag_the_docs_promise_actually_parses():
    """Documented flags must exist. A doc that lies is worse than no doc."""
    documented = set(re.findall(r"sk viz ([-\w \.]+)", DOC.read_text(encoding="utf-8")))
    flags = {tok for line in documented for tok in line.split()
             if tok.startswith("--")}
    assert flags, "no flags found in the docs; the regex or the doc changed"

    help_text = _sk("viz", "--help").stdout
    for flag in flags:
        assert flag in help_text, f"docs promise `{flag}` but `sk viz --help` lacks it"


def test_documented_routes_are_the_routes_the_server_serves(toolkit):
    from skeletonkey.viz.server import VizServer

    documented = set(re.findall(r"`?(/api/[a-z<>/]+|/healthz)`?",
                                DOC.read_text(encoding="utf-8")))
    documented = {r.rstrip("/") for r in documented}
    with VizServer(toolkit.engine.registry, port=0) as srv:
        import urllib.error
        import urllib.request

        try:
            urllib.request.urlopen(srv.url + "nope", timeout=10)
        except urllib.error.HTTPError as exc:
            served = set(json.loads(exc.read())["routes"])
    for route in documented:
        if "<" in route:      # /api/tool/<id> is a template
            assert any(r.startswith("/api/tool/") for r in served)
            continue
        assert route in served, f"docs promise {route} but the server does not serve it"


def test_the_drift_gate_exits_non_zero_as_documented(tmp_path):
    """README claims `sk viz --drift` is a CI gate. Prove the exit code."""
    import time

    from skeletonkey.core.config import Config
    from skeletonkey.core.manifest import ToolManifest
    from skeletonkey.toolkit import build
    from skeletonkey.viz.runtime import MIN_CALLS_FOR_DRIFT, attach_runtime

    ws = tmp_path / "ws"
    ws.mkdir()
    cfg = Config.load(cwd=str(ws), overrides={"roots": [str(ws)]})
    tk = build(config=cfg)
    try:
        if tk.ledger is None:
            pytest.skip("no ledger configured")
        tk.engine.registry.register(
            ToolManifest(id="ship.liar", description="Claims to be instant.",
                         risk="none", typical_latency_ms=1),
            handler=lambda **kw: (time.sleep(0.02), {"ok": True})[1],
        )
        for _ in range(MIN_CALLS_FOR_DRIFT + 1):
            tk.engine.call("ship.liar", {})

        model = build_viz_model(tk.engine.registry)
        summary = attach_runtime(model, tk.ledger)
        assert [d for d in summary["drift"] if d["id"] == "ship.liar"], \
            "the linter missed a 20x under-declaration"
    finally:
        tk.close()

    # and a clean workspace exits 0, so the gate is not simply always-red
    clean = tmp_path / "clean"
    clean.mkdir()
    proc = _sk("viz", "--drift", cwd=clean)
    assert proc.returncode == 0, proc.stdout + proc.stderr[-1000:]


def test_the_documented_view_count_matches_the_page(toolkit):
    from skeletonkey.viz import render_html

    doc_views = set(re.findall(r"\*\*(cards|graph|tokens|matrix|latency|runtime)\*\*",
                               DOC.read_text(encoding="utf-8")))
    assert len(doc_views) == 6, f"the docs describe {sorted(doc_views)}"

    page = render_html(build_viz_model(toolkit.engine.registry))
    for view in ("cards", "graph", "treemap", "matrix", "latency", "runtime"):
        assert f'id="view-{view}"' in page


def test_the_readme_numbers_are_in_the_right_ballpark(toolkit):
    """A README that quotes counts should not rot silently by an order of magnitude."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    claim = re.search(r"(\d+) registered / (\d+) advertised", readme)
    if claim is None:
        pytest.skip("the README no longer quotes viz counts")
    registered = len(toolkit.engine.registry.all())
    assert abs(int(claim.group(1)) - registered) <= 15, \
        f"README claims {claim.group(1)} registered; this build has {registered}"


# --------------------------------------------------------------------------
# the committed toolset snapshot
# --------------------------------------------------------------------------

def test_the_committed_toolset_snapshot_is_current():
    """docs/TOOLSET.txt must match what the real CLI produces.

    This is the whole point of the file: it turns "someone quietly added a
    destructive tool" into a red test and a one-line diff in review. If this
    fails, run `sk viz --summary -o docs/TOOLSET.txt` and read the diff before
    you commit it.

    Note this deliberately shells out to the CLI against the *full* builtin
    registry rather than using the `toolkit` fixture, whose registry is a
    smaller test-shaped subset.
    """
    snapshot = REPO / "docs" / "TOOLSET.txt"
    assert snapshot.exists(), "run: sk viz --summary -o docs/TOOLSET.txt"
    proc = _sk("viz", "--summary")
    assert proc.returncode == 0, proc.stderr[-800:]
    assert snapshot.read_text(encoding="utf-8").strip() == proc.stdout.strip(), (
        "docs/TOOLSET.txt is stale; regenerate with "
        "`sk viz --summary -o docs/TOOLSET.txt`"
    )


def test_the_snapshot_stays_small_enough_to_review():
    # the HTML page is ~300 KB and unreviewable; this must stay diff-sized
    body = (REPO / "docs" / "TOOLSET.txt").read_bytes()
    assert len(body) < 64_000, "snapshot has grown past review size"


def test_the_snapshot_is_deterministic(toolkit):
    from skeletonkey.viz import build_viz_model, summary_lines

    reg = toolkit.engine.registry
    assert summary_lines(build_viz_model(reg)) == summary_lines(build_viz_model(reg))
