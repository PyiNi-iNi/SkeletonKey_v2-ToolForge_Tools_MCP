"""V4 of the tool visualizer: why-not, hypothetical hosts, and diffing.

The explainer mirrors `Registry.gate()` / `Registry.advertise()`, and a mirror
is a liability: two implementations of one rule drift apart silently. So the
load-bearing test here is not any individual chain, it is the invariant that
makes the mirror safe -

    a tool is advertised  IF AND ONLY IF  its chain has no failing step

- asserted for every registered tool, under every projection the suite tries
(default, read-only, each tier, disabled ids, unmet requirements, hypothetical
hosts). If the registry's gating changes and `explain.py` does not, that test
fails, which is exactly what should happen.

The hypothetical-host switcher gets its own guarantee: it must be *pure*. The
real profile is never mutated and nothing is re-probed, because a "what would
Windows see" question that quietly rewrote the live profile would be a very
expensive way to be wrong.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from skeletonkey.core.manifest import Requirement, ToolManifest
from skeletonkey.viz import build_viz_model
from skeletonkey.viz.explain import (
    FAIL,
    NOTE,
    PASS,
    SKIP,
    diff_models,
    explain_model,
    explain_tool,
    hypothetical_profile,
    project,
)

REPO = Path(__file__).resolve().parents[1]
OUTCOMES = {PASS, FAIL, NOTE, SKIP}


@pytest.fixture
def registry(toolkit):
    return toolkit.engine.registry


# ------------------------------------------------------ the load-bearing test

def assert_iff(registry, **kwargs) -> None:
    """advertised <=> no failing step, for every tool."""
    model = build_viz_model(registry, **kwargs)
    chains = explain_model(registry, **kwargs)
    advertised = {n.id for n in model.nodes if n.advertised}
    for tool_id, chain in chains.items():
        blocked = bool(chain.blocked_by)
        is_adv = tool_id in advertised
        assert is_adv != blocked, (
            f"{tool_id}: advertised={is_adv} but blocked_by="
            f"{[s.rule for s in chain.blocked_by]}"
        )
        assert chain.advertised == is_adv


def test_chain_agrees_with_the_snapshot_by_default(registry):
    assert_iff(registry)


def test_chain_agrees_under_read_only(registry):
    assert_iff(registry, read_only=True)


@pytest.mark.parametrize("tier", ["core", "task", "full"])
def test_chain_agrees_under_every_tier(registry, tier):
    assert_iff(registry, tier=tier)


def test_chain_agrees_when_a_tool_is_disabled(registry):
    model = build_viz_model(registry, disabled=["fs.read"])
    chains = explain_model(registry, disabled=["fs.read"])
    assert model.node("fs.read").advertised is False
    assert chains["fs.read"].blocked_by[0].rule == "tools.disable"
    advertised = {n.id for n in model.nodes if n.advertised}
    for tool_id, chain in chains.items():
        assert (tool_id in advertised) != bool(chain.blocked_by)


def test_chain_agrees_with_an_unmet_requirement(registry):
    registry.register(
        ToolManifest(id="viz.needs_nothing_real", description="Needs a missing binary.",
                     risk="none",
                     requirements=[Requirement("binary", "no-such-binary-zzz")]),
        handler=lambda **kw: None,
    )
    assert_iff(registry)
    chain = explain_tool(registry, "viz.needs_nothing_real")
    assert [s.rule for s in chain.blocked_by] == ["requirements"]
    step = chain.blocked_by[0]
    assert step.evidence and "no-such-binary-zzz" in " ".join(step.evidence)
    assert step.fix


# ------------------------------------------------------------ chain structure

def test_every_step_is_well_formed(registry):
    for chain in explain_model(registry).values():
        assert chain.steps, f"{chain.id} produced an empty chain"
        for step in chain.steps:
            assert step.outcome in OUTCOMES
            assert step.rule
            # a failure must always be actionable
            if step.outcome == FAIL:
                assert step.detail, f"{chain.id}/{step.rule}: failure with no explanation"


def test_the_rule_order_mirrors_the_registry(registry):
    """The chain must read in the order the registry actually decides."""
    chain = explain_tool(registry, "fs.read")
    rules = [s.rule for s in chain.steps]
    assert rules == ["tools.disable", "manifest.advertised", "policy.read_only",
                     "manifest.platforms", "requirements", "require_any",
                     "discovery.tier", "internal", "provider.dedupe",
                     "advertise.budget"]


def test_disable_short_circuits_like_the_registry_does(registry):
    chain = explain_tool(registry, "fs.read", disabled=["fs.read"])
    assert [s.rule for s in chain.steps] == ["tools.disable"]
    assert chain.steps[0].outcome == FAIL


def test_an_advertised_tool_has_no_failures_and_a_positive_verdict(registry):
    chain = explain_tool(registry, "fs.read")
    assert chain.advertised is True
    assert chain.blocked_by == []
    assert "advertised" in chain.verdict


def test_withheld_internal_tools_name_the_internal_rule(registry):
    model = build_viz_model(registry)
    internal = [n.id for n in model.nodes if n.withheld_by == "internal"]
    assert internal, "fixture drifted: no internal tools to explain"
    for tool_id in internal:
        chain = explain_tool(registry, tool_id)
        assert [s.rule for s in chain.blocked_by] == ["internal"]
        # the manifest.advertised step recorded it earlier without deciding
        noted = [s for s in chain.steps if s.rule == "manifest.advertised"]
        assert noted[0].outcome == NOTE


def test_read_only_blames_the_policy_not_the_host(registry):
    chain = explain_tool(registry, "fs.write", read_only=True)
    assert [s.rule for s in chain.blocked_by] == ["policy.read_only"]
    assert "read_only" in chain.blocked_by[0].detail
    assert chain.blocked_by[0].fix


def test_tier_withholding_names_the_tier(registry):
    model = build_viz_model(registry, tier="core")
    dropped = [n.id for n in model.nodes if n.withheld_by == "tier"]
    assert dropped
    chain = explain_tool(registry, dropped[0], tier="core")
    assert "discovery.tier" in [s.rule for s in chain.blocked_by]


def test_chain_serialises_to_json(registry):
    doc = explain_tool(registry, "fs.read").to_json()
    assert json.loads(json.dumps(doc))["id"] == "fs.read"
    assert doc["steps"] and "blocked_by" in doc


def test_explaining_does_not_mutate_the_registry(registry):
    before = sorted(m.id for m in registry.all())
    digest = registry.advertise().digest
    explain_model(registry)
    explain_model(registry, read_only=True, tier="core")
    assert sorted(m.id for m in registry.all()) == before
    assert registry.advertise().digest == digest


# ------------------------------------------------------- hypothetical hosts

@pytest.mark.parametrize("os_name", ["windows", "linux", "darwin"])
def test_hypothetical_profile_is_pure(registry, os_name):
    base = registry.profile
    before = (base.os, sorted(base.shells), set(base.capabilities), base.fingerprint)
    prof = hypothetical_profile(base, os_name)

    assert prof.os == os_name
    assert prof is not base
    assert (base.os, sorted(base.shells), set(base.capabilities), base.fingerprint) == before, \
        "the real profile was mutated"


def test_hypothetical_profile_is_labelled_as_derived(registry):
    prof = hypothetical_profile(registry.profile, "windows")
    assert prof.fingerprint.startswith("hypothetical:")
    assert prof.probe_receipt == []
    assert any("not probed" in w for w in prof.warnings)


def test_hypothetical_windows_swaps_the_shell_capability(registry):
    prof = hypothetical_profile(registry.profile, "windows")
    assert "shell.powershell" in prof.capabilities
    assert "shell.unix" not in prof.capabilities


def test_python_stays_available_on_every_hypothetical_host(registry):
    """python is a first-class dialect on every target; dropping it fakes a failure."""
    base = registry.profile
    if "python" not in base.shells:
        pytest.skip("this host has no python dialect probed")
    for os_name in ("windows", "linux", "darwin"):
        assert "python" in hypothetical_profile(base, os_name).shells


@pytest.mark.parametrize("os_name", ["windows", "linux", "darwin"])
def test_projection_builds_a_labelled_model_and_keeps_the_iff(registry, os_name):
    model = project(registry, os_name=os_name)
    assert model.host["os"] == os_name
    assert model.host["hypothetical"] is True
    assert len(model.nodes) == len(registry.all())

    prof = hypothetical_profile(registry.profile, os_name)
    chains = explain_model(registry, profile=prof)
    advertised = {n.id for n in model.nodes if n.advertised}
    for tool_id, chain in chains.items():
        assert (tool_id in advertised) != bool(chain.blocked_by)


def test_a_platform_locked_tool_is_withheld_off_platform(registry):
    registry.register(
        ToolManifest(id="viz.windows_only", description="Windows-only tool.",
                     risk="none", platforms=["windows"]),
        handler=lambda **kw: None,
    )
    on = project(registry, os_name="windows").node("viz.windows_only")
    off = project(registry, os_name="linux").node("viz.windows_only")
    assert on.advertised is True
    assert off.advertised is False

    chain = explain_tool(registry, "viz.windows_only",
                         profile=hypothetical_profile(registry.profile, "linux"))
    assert [s.rule for s in chain.blocked_by] == ["manifest.platforms"]
    assert "windows" in chain.blocked_by[0].detail


# ------------------------------------------------------------------- diffing

def test_diff_of_a_model_against_itself_is_empty(registry):
    model = build_viz_model(registry)
    report = diff_models(model, model)
    assert report["same"] is True
    assert report["counts"] == {"added": 0, "removed": 0, "changed": 0, "exposure": 0}
    assert report["tokens"]["delta"] == 0


def test_diff_notices_an_added_tool(registry):
    before = build_viz_model(registry)
    registry.register(
        ToolManifest(id="viz.brandnew", description="Added after the snapshot.",
                     risk="none"),
        handler=lambda **kw: None,
    )
    report = diff_models(before, build_viz_model(registry))
    assert report["added"] == ["viz.brandnew"]
    assert report["removed"] == []
    assert report["same"] is False
    assert report["tokens"]["delta"] > 0


def test_diff_notices_exposure_changes(registry):
    plain = build_viz_model(registry)
    locked = build_viz_model(registry, read_only=True)
    report = diff_models(plain, locked)
    assert report["exposure"], "read-only must change what is exposed"
    for row in report["exposure"]:
        assert row["from"] is True and row["to"] is False
        assert row["why"]
    assert report["tokens"]["delta"] < 0


def test_diff_works_on_serialised_json(registry, tmp_path):
    """A saved model from another host or commit must diff without a registry."""
    model = build_viz_model(registry)
    doc = model.to_json()
    doc["model_digest"] = model.digest()
    path = tmp_path / "saved.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    report = diff_models(json.loads(path.read_text(encoding="utf-8")), model)
    assert report["same"] is True
    assert report["digest"]["from"] == report["digest"]["to"] == model.digest()


def test_diff_reports_field_level_changes(registry):
    before = build_viz_model(registry)
    man = registry.get("fs.read")
    registry.register(
        ToolManifest(id="fs.read", description=man.description, risk="write",
                     input_schema=man.input_schema, capability=man.capability,
                     group=man.group, tier=man.tier),
        handler=lambda **kw: None, replace=True,
    )
    report = diff_models(before, build_viz_model(registry))
    row = next(r for r in report["changed"] if r["id"] == "fs.read")
    assert row["changes"]["risk"] == {"from": "read", "to": "write"}


# ----------------------------------------------------------------------- CLI

def _sk(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "skeletonkey.cli", "viz", *argv],
                          capture_output=True, text=True, cwd=str(REPO), timeout=180)


def test_cli_explain_prints_a_chain():
    proc = _sk("--explain", "fs.read")
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "fs.read" in proc.stdout
    assert "tools.disable" in proc.stdout
    assert "advertise.budget" in proc.stdout


def test_cli_why_not_lists_only_withheld_tools():
    proc = _sk("--why-not", "--json")
    assert proc.returncode == 0, proc.stderr[-2000:]
    doc = json.loads(proc.stdout)
    assert doc, "this host withholds at least the internal tools"
    for chain in doc.values():
        assert chain["advertised"] is False
        assert chain["blocked_by"]


def test_cli_as_host_marks_the_model_hypothetical():
    proc = _sk("--as-host", "windows", "--json")
    assert proc.returncode == 0, proc.stderr[-2000:]
    doc = json.loads(proc.stdout)
    assert doc["host"]["os"] == "windows"
    assert doc["host"]["hypothetical"] is True


def test_cli_diff_against_a_saved_model(tmp_path):
    saved = tmp_path / "ro.json"
    assert _sk("--read-only", "-o", str(saved)).returncode == 0
    proc = _sk("--diff", str(saved), "--json")
    assert proc.returncode == 0, proc.stderr[-2000:]
    report = json.loads(proc.stdout)
    assert report["exposure"], "read-only vs normal must differ"
    assert report["tokens"]["delta"] > 0


def test_served_page_carries_the_chains(toolkit):
    import urllib.request

    from skeletonkey.viz.server import VizServer

    with VizServer(toolkit.engine.registry, port=0) as srv:
        with urllib.request.urlopen(srv.url + "api/explain", timeout=10) as resp:
            doc = json.load(resp)
        assert len(doc) == len(toolkit.engine.registry.all())

        with urllib.request.urlopen(srv.url, timeout=10) as resp:
            page = resp.read().decode("utf-8")
        assert '"chains"' in page, "the served drawer should get the chains"


# --------------------------------------------------------------------------
# performance: explain_model must not be O(n^2) in advertise() calls
# --------------------------------------------------------------------------

def test_explain_model_advertises_once_not_once_per_tool(toolkit):
    """Regression: explain_model called registry.advertise() per tool, which
    made a whole-registry explain quadratic (86ms -> 1.8ms when fixed).

    Counting calls rather than timing keeps this deterministic on a noisy CI
    box: the property is "one snapshot, shared", not "fast on this machine".
    """
    reg = toolkit.engine.registry
    calls = {"n": 0}
    real = reg.advertise

    def counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    reg.advertise = counting          # type: ignore[method-assign]
    try:
        chains = explain_model(reg)
    finally:
        reg.advertise = real          # type: ignore[method-assign]

    assert len(chains) == len(reg.all()) > 10
    assert calls["n"] == 1, f"advertise() called {calls['n']}x for one explain"


def test_shared_snapshot_gives_identical_chains(toolkit):
    """The optimisation must not change a single answer."""
    reg = toolkit.engine.registry
    shared = explain_model(reg)
    for tool_id, chain in shared.items():
        alone = explain_tool(reg, tool_id)          # recomputes its own snapshot
        assert chain.to_json() == alone.to_json(), tool_id


def test_shared_snapshot_respects_the_projection(toolkit):
    """A read-only explain must not silently reuse a full-tier snapshot."""
    reg = toolkit.engine.registry
    full = explain_model(reg)
    ro = explain_model(reg, read_only=True)
    assert full != ro
    for tool_id, chain in ro.items():
        alone = explain_tool(reg, tool_id, read_only=True)
        assert chain.to_json() == alone.to_json(), tool_id
