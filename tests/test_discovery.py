"""P5 acceptance: tiers, routing, provider receipts, explain, pagination (P5a)
and the shipped semantic stage (P5b, ADR-0012).

AC1: with 200 registered tools, advertise(tier="core") stays <= 20 tools / <= 1.2 k
tokens, and registry.route(task, k=5) top-k hit-rate >= 0.9 over the eval suite
(ground truth: each task's `target`).
AC2: the builtin zero-dep backend makes route(semantic=True) a real two-stage
comparison - same candidate ids, reordering observed, same hit-rate; if
discovery yields nothing, route stays lexical and says so.
AC3: expand round-trip - tier switch changes the digest (the wire notification is
asserted in test_mcp_stdio.py).
AC4: provider races expose the winner's provider + why; explain() names the gates.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skeletonkey.core.config import Config
from skeletonkey.core.errors import SkeletonKeyError
from skeletonkey.core.manifest import Requirement, ToolManifest
from skeletonkey.core.profile import CapabilityProfile
from skeletonkey.core.registry import Registry
from skeletonkey.toolkit import build


@pytest.fixture(scope="module")
def eval_toolkit(tmp_path_factory):
    """One real toolkit for the suite-level routing assertions."""
    root = tmp_path_factory.mktemp("discovery-eval")
    tk = build(config=Config.load(cwd=str(root), overrides={
        "roots": [str(root)], "state": {"dir": str(root / ".sk")}}))
    try:
        yield tk
    finally:
        tk.close()


def _man(tool_id: str, *, tier: str = "full", capability: str | None = None,
         provider: str | None = None, priority: int = 50, risk: str = "none",
         description: str = "", reqs: list[Requirement] | None = None,
         tags: list[str] | None = None) -> ToolManifest:
    return ToolManifest(
        id=tool_id, title=tool_id.replace(".", " "),
        description=description or f"{tool_id}: does {tool_id.split('.')[-1]} things for a task",
        capability=capability or tool_id, provider=provider, priority=priority,
        risk=risk, tier=tier, requirements=reqs or [], tags=tags or [])


def _empty_profile() -> CapabilityProfile:
    p = CapabilityProfile(os="linux", os_release="x", arch="amd64",
                          python_version="3.11", is_admin=False)
    p.binaries = {"bash": "/bin/bash"}
    p.capabilities = {"shell.unix"}
    return p


# ---------------------------------------------------------------- manifest
def test_manifest_tier_validated_and_round_trips():
    m = ToolManifest(id="fs.read", tier="core")
    assert m.tier == "core"
    assert m.to_dict()["tier"] == "core"
    assert ToolManifest.from_dict({"id": "fs.write", "tier": "task"}).tier == "task"
    with pytest.raises(SkeletonKeyError) as exc:
        ToolManifest(id="fs.read", tier="quantum")
    assert exc.value.err.code == "BAD_ARGS"   # (E.BAD_ARGS.code) - err.code is a str


# ---------------------------------------------------------------- tiers
def test_core_tools_advertise_in_every_tier():
    reg = Registry(profile=_empty_profile())
    for tid, tier in [("a.core", "core"), ("a.task", "task"), ("a.full", "full")]:
        reg.register(_man(tid, tier=tier), lambda **kw: {"ok": True})
    core = reg.advertise(tier="core")
    assert core.names == ["a.core"]
    assert not core.gates["a.task"].available
    assert "tier core: this tool is task-tier" in core.gates["a.task"].reasons
    task = reg.advertise(tier="task")
    assert task.names == ["a.core", "a.task"]
    full = reg.advertise(tier="full")
    assert full.names == ["a.core", "a.full", "a.task"]


def test_set_tier_changes_default_and_digest():
    reg = Registry(profile=_empty_profile())
    for tid, tier in [("a.core", "core"), ("a.full", "full")]:
        reg.register(_man(tid, tier=tier), lambda **kw: {"ok": True})
    assert reg.set_tier("core") == "full"
    assert reg.active_tier == "core"
    assert reg.advertise().names == ["a.core"]
    assert reg.advertise(tier="full").digest != reg.advertise(tier="core").digest
    with pytest.raises(SkeletonKeyError):
        reg.set_tier("nope")


def test_core_budget_holds_at_200_tools():
    reg = Registry(profile=_empty_profile())
    for i in range(200):
        tier = "core" if i < 5 else "full"
        reg.register(_man(f"b{i:03d}", tier=tier), lambda **kw: {"ok": True})
    budgets = {"core": {"tools": 20, "tokens": 1200},
               "task": {"tools": 48, "tokens": 6000},
               "full": {"tools": 0, "tokens": 0}}
    core = reg.advertise(tier="core", tier_budgets=budgets)
    assert len(core.tools) <= 20 and core.tokens <= 1200
    # all five core tools survive the cap (they are the bootstrap surface)
    assert {"b000", "b001", "b002", "b003", "b004"} <= set(core.names)


def test_budget_drops_are_named_not_silent():
    reg = Registry(profile=_empty_profile())
    for i in range(30):
        reg.register(_man(f"c{i:03d}", tier="task"), lambda **kw: {"ok": True})
    snap = reg.advertise(tier="task", tier_budgets={"task": {"tools": 10, "tokens": 0}})
    assert len(snap.tools) == 10
    assert snap.budget_drops and all(v == "max_tools 10" for v in snap.budget_drops.values())
    dropped = set(snap.budget_drops)
    assert not (dropped & set(snap.names))
    # an explicit budget always wins over the tier budget
    explicit = reg.advertise(tier="task", tier_budgets={"task": {"tools": 10, "tokens": 0}},
                             max_tools=25)
    assert len(explicit.tools) == 25


def test_read_only_still_withholds_mutating_core_tools():
    reg = Registry(profile=_empty_profile())
    reg.register(_man("a.read", tier="core", risk="read"), lambda **kw: {"ok": True})
    reg.register(_man("a.write", tier="core", risk="write"), lambda **kw: {"ok": True})
    snap = reg.advertise(tier="core", read_only=True)
    assert snap.names == ["a.read"]
    assert snap.gates["a.write"].reasons and "read_only" in snap.gates["a.write"].reasons[0]


# ---------------------------------------------------------------- routing
def test_route_exact_name_always_wins():
    reg = Registry(profile=_empty_profile())
    for tid in ["fs.patch", "fs.move", "fs.read"]:
        reg.register(_man(tid), lambda **kw: {"ok": True})
    for task in ("fs.patch", "fs_patch", "fs/patch"):
        r = reg.route(task, k=3)
        assert r["results"][0]["id"] == "fs.patch"
        assert r["results"][0]["reasons"] == ["exact name match"]


def test_route_explains_hits_and_reports_modes():
    reg = Registry(profile=_empty_profile())
    for tid, desc, tags in [("fs.patch", "apply replacement edits to a file", ["patch", "edit"]),
                            ("fs.move", "rename or move a path", ["move", "rename"]),
                            ("fs.search", "find text in files", ["search", "find"])]:
        reg.register(_man(tid, description=desc, tags=tags), lambda **kw: {"ok": True})
    r = reg.route("rename a symbol in one file", k=5, semantic=True)
    assert r["mode"] == "semantic" and r["backend"] == "lexical-tfidf"
    assert r["backends_available"] >= 1
    assert all(hit.get("reasons") and "semantic_score" in hit and "blend" in hit
               for hit in r["results"]), r["results"]
    off = reg.route("rename a symbol in one file", k=5, semantic=False)
    assert off["mode"] == "lexical" and off["backend"] is None
    assert all("semantic_score" not in hit for hit in off["results"])
    # exact-name path is mode-independent
    assert reg.route("fs.search", k=3)["results"][0]["id"] == "fs.search"


def test_route_semantic_reranks_within_lexical_candidates(eval_toolkit):
    """AC2 with the shipped backend: same candidate ids, reordering observed,
    same ground-truth hit-rate - the stage is real and changes no outcomes."""
    reg = eval_toolkit.engine.registry
    reordered = 0
    for task in _suite():
        on = reg.route(task["task"], k=5, semantic=True)
        off = reg.route(task["task"], k=5, semantic=False)
        assert on["mode"] == "semantic" and on["backend"] == "lexical-tfidf"
        assert off["mode"] == "lexical" and off["backend"] is None
        # the semantic stage reranks within the lexical candidate set: same ids,
        # possibly a different order
        on_ids = [h["id"] for h in on["results"]]
        off_ids = [h["id"] for h in off["results"]]
        assert sorted(on_ids) == sorted(off_ids), task["id"]
        if on_ids != off_ids:
            reordered += 1
        # and neither mode loses the ground truth at k=5
        assert task["target"] in [h["id"] for h in on["results"]], (task["id"], "semantic")
        assert task["target"] in [h["id"] for h in off["results"]], (task["id"], "lexical")
    assert reordered > 0, "the semantic stage must actually reorder (else it is a no-op)"
    # determinism: same call twice, same ranking
    first = reg.route("rename a symbol in one file", k=8, semantic=True)
    second = reg.route("rename a symbol in one file", k=8, semantic=True)
    assert [h["id"] for h in first["results"]] == [h["id"] for h in second["results"]]


def test_route_semantic_honest_when_backends_unavailable(monkeypatch):
    """If discovery yields nothing (an exotic install), semantic=True must say so."""
    import skeletonkey.core.registry as regmod

    monkeypatch.setattr(regmod, "discover", lambda: ([], [{"name": "x", "error": "boom"}]))
    reg = Registry(profile=_empty_profile())
    reg.register(_man("fs.patch", description="apply edits", tags=["patch"]),
                 lambda **kw: {"ok": True})
    r = reg.route("patch a file", k=3, semantic=True)
    assert r["mode"] == "lexical" and r["backend"] is None
    assert r["note"] and "no semantic backend" in r["note"]
    assert r["backend_errors"] == [{"name": "x", "error": "boom"}]


def test_route_eval_suite_target_hit_rate(eval_toolkit):
    """AC1 second half: >= 0.9 of the suite's ground-truth targets at k=5."""
    reg = eval_toolkit.engine.registry
    hits = 0
    for task in _suite():
        got = [r["id"] for r in reg.route(task["task"], k=5)["results"]]
        if task["target"] in got:
            hits += 1
    assert hits / len(_suite()) >= 0.9, f"route hit-rate {hits}/{len(_suite())} < 0.9"


def _suite() -> list[dict]:
    here = Path(__file__).parent
    return [json.loads(line) for line in (here / "eval" / "suite.jsonl").read_text().splitlines()
            if line.strip()]


# ---------------------------------------------------------------- receipts
def test_provider_race_receipt_exposes_winner_and_why():
    reg = Registry(profile=_empty_profile())
    fast = _man("search.fast", capability="search.text", provider="python", priority=60,
                description="built-in text search")
    slow = _man("search.slow", capability="search.text", provider="rg", priority=70,
                description="ripgrep text search")
    reg.register(fast, lambda **kw: {"ok": True})
    reg.register(slow, lambda **kw: {"ok": True})
    # no call evidence yet: declared priority should win regardless of score maths
    snap = reg.advertise()
    assert snap.selected["search.text"] == "search.slow"
    rec = snap.selection_receipts["search.text"]
    assert rec["tool"] == "search.slow" and rec["provider"] == "rg"
    assert "provider race" in rec["why"] and "no call evidence" in rec["why"]
    assert {c["id"] for c in rec["competitors"]} == {"search.fast"}
    assert snap.receipt_for(fast)["tool"] == "search.slow"  # race receipt for both rows


def test_sole_provider_receipt_is_honest():
    reg = Registry(profile=_empty_profile())
    reg.register(_man("search.only", capability="search.text", provider="python"),
                 lambda **kw: {"ok": True})
    rec = reg.advertise().receipt_for(reg.get("search.only"))
    assert rec["sole"] is True and "sole provider" in rec["why"]


def test_explain_names_gates_and_winner():
    p = _empty_profile()
    reg = Registry(profile=p)
    reg.register(_man("search.only", capability="search.text", provider="python"),
                 lambda **kw: {"ok": True})
    reg.register(_man("search.gated", capability="search.text", provider="rg",
                      reqs=[Requirement("binary", "rg")]),
                 lambda **kw: {"ok": True})
    out = reg.explain("search.text")
    assert out["winner"]["tool"] == "search.only"
    row = next(r for r in out["tools"] if r["id"] == "search.gated")
    assert not row["gate"]["available"] and row["gate"]["unmet"]
    assert out["gated_out"] == ["search.gated"]
    # explain accepts a tool id and resolves its capability; unknown caps fail loudly
    assert reg.explain("search.only")["capability"] == "search.text"
    with pytest.raises(SkeletonKeyError) as exc:
        reg.explain("no.such.capability")
    assert exc.value.err.code == "BAD_ARGS"


def test_explain_all_lists_every_gate_gated_first():
    p = _empty_profile()
    reg = Registry(profile=p)
    reg.register(_man("a.shown", tier="core"), lambda **kw: {"ok": True})
    reg.register(_man("a.gated", tier="core", reqs=[Requirement("binary", "nope")]),
                 lambda **kw: {"ok": True})
    reg.register(_man("a.full_only", tier="full"), lambda **kw: {"ok": True})
    out = reg.explain_all()
    assert out["tier"] == "full" and out["registered"] == 3
    assert out["advertised"] == 2 and out["gated_out"] == 1
    assert [r["id"] for r in out["rows"]] == ["a.gated"]
    row = out["rows"][0]
    assert row["advertised"] is False and row["tier"] == "core"
    assert row["gate"]["available"] is False and row["gate"]["unmet"]
    # advertised rows are appended after the gated ones when asked, id-sorted
    wide = reg.explain_all(include_advertised=True)
    assert [r["id"] for r in wide["rows"]] == ["a.gated", "a.full_only", "a.shown"]
    # querying a smaller tier previews its withholds without switching to it
    core = reg.explain_all(tier="core")
    assert core["tier"] == "core" and reg.active_tier == "full"
    assert {r["id"] for r in core["rows"]} == {"a.gated", "a.full_only"}
    withheld = next(r for r in core["rows"] if r["id"] == "a.full_only")
    assert "tier core" in withheld["gate"]["reasons"][0]
    with pytest.raises(SkeletonKeyError) as exc:
        reg.explain_all(tier="quantum")
    assert exc.value.err.code == "BAD_ARGS"


def test_explain_all_group_filter_and_budget_drops():
    reg = Registry(profile=_empty_profile())
    for tid, group in [("fs.a", "fs"), ("shell.b", "shell")]:
        reg.register(ToolManifest(id=tid, title=tid, description=f"{tid} does things",
                                  capability=tid, group=group, tier="task"),
                     lambda **kw: {"ok": True})
    fs_only = reg.explain_all(tier="core", group="fs")
    assert fs_only["group"] == "fs" and fs_only["registered"] == 1
    assert [r["id"] for r in fs_only["rows"]] == ["fs.a"]
    assert "group" not in reg.explain_all()
    # a tier-budget trim names the reason on the row instead of going quiet: a
    # dropped tool passes its gate (available) but is not in the snapshot, so
    # the budget_drop field is the only honest answer to "why didn't I see it"
    for i in range(6):
        reg.register(_man(f"z{i:02d}", tier="task"), lambda **kw: {"ok": True})
    trimmed = reg.explain_all(tier="task", tier_budgets={"task": {"tools": 3, "tokens": 0}})
    drops = [r for r in trimmed["rows"] if "budget_drop" in r]
    assert drops, "a 3-tool cap over 8 task tools must trim"
    assert all(d["budget_drop"] == "max_tools 3" for d in drops)
    assert all(d["advertised"] is False and d["gate"]["available"] is True for d in drops)


def test_explain_all_through_the_engine_names_the_real_gates(toolkit):
    eng = toolkit.engine
    listed = eng.call("registry.list", {})
    assert listed.ok
    r = eng.call("registry.explain_all", {})
    assert r.ok
    d = r.data
    # the same snapshot the host sees: same digest, same advertised count
    assert d["digest"] == listed.data["digest"]
    assert d["registered"] == len(eng.registry.all())
    assert d["advertised"] == listed.data["total"]
    assert d["advertised"] + d["gated_out"] == d["registered"]
    rows = {row["id"]: row for row in d["rows"]}
    assert "skills.install" in rows and "allow_install" in rows["skills.install"]["hidden_reason"]
    if eng.registry.has("shell.selftest"):
        # skill-synthesized tools only exist when the repo skills dirs load
        assert "shell.selftest" in rows and rows["shell.selftest"]["hidden_reason"]
    # group filter narrows rows and counts; a bad tier is BAD_ARGS, not silence
    g = eng.call("registry.explain_all", {"group": "fs", "include_advertised": True})
    assert g.ok and g.data["group"] == "fs"
    assert g.data["rows"] and all(row["id"].startswith("fs.") for row in g.data["rows"])
    assert g.data["registered"] == len(eng.registry.by_group("fs"))
    bad = eng.call("registry.explain_all", {"tier": "quantum"})
    assert not bad.ok and bad.error.code == "BAD_ARGS"


def test_explain_all_budget_drops_named_through_the_engine(tmp_path):
    cfg = Config.load(cwd=str(tmp_path), overrides={
        "roots": [str(tmp_path)], "state": {"dir": str(tmp_path / ".sk")},
        "log_level": "ERROR",
        "advertise": {"task_max_tools": 2, "task_max_tokens": 0}})
    tk = build(config=cfg)
    try:
        r = tk.engine.call("registry.explain_all", {"tier": "task"})
        assert r.ok
        drops = [row for row in r.data["rows"] if "budget_drop" in row]
        assert drops, "a 2-tool cap must trim the task tier"
        assert all("max_tools 2" in row["budget_drop"] for row in drops)
    finally:
        tk.close()


# ---------------------------------------------------------------- wiring
def test_expand_and_list_tiers_through_the_engine(toolkit):
    eng = toolkit.engine
    res = eng.call("registry.expand", {"tier": "task"})
    assert res.ok and res.data["previous_tier"] == "full" and res.data["tier"] == "task"
    assert eng.registry.active_tier == "task"

    listed = eng.call("registry.list", {})
    assert listed.ok
    assert listed.data["tier"] == "task"
    assert "fs.patch" in {t["id"] for t in listed.data["tools"]}

    # querying another tier does not switch the session tier
    core = eng.call("registry.list", {"tier": "core"})
    assert core.ok and core.data["tier"] == "core"
    assert eng.registry.active_tier == "task"
    assert "fs.patch" not in {t["id"] for t in core.data["tools"]}

    back = eng.call("registry.expand", {"tier": "full"})
    assert back.ok and eng.registry.active_tier == "full"


def test_registry_list_paginates_completely(toolkit):
    eng = toolkit.engine
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        args: dict = {"limit": 10}
        if cursor:
            args["cursor"] = cursor
        res = eng.call("registry.list", args)
        assert res.ok
        chunk = [t["id"] for t in res.data["tools"]]
        assert chunk and not (set(chunk) & set(seen)), "pages must not overlap"
        seen.extend(chunk)
        pages += 1
        cursor = res.data.get("next_cursor")
        if not cursor:
            break
        assert pages < 20, "pagination must terminate"
    assert len(seen) == res.data["total"] == len(eng.registry.advertise().tools)
    assert seen == sorted(seen)


def test_route_and_explain_callable_through_the_engine(toolkit):
    eng = toolkit.engine
    r = eng.call("registry.route", {"task": "rename a symbol in one file", "k": 5})
    assert r.ok
    assert r.data["results"] and all(hit.get("reasons") for hit in r.data["results"])
    assert any(hit["id"] == "fs.patch" for hit in r.data["results"])

    e = eng.call("capabilities.explain", {"capability": "fs.patch"})
    assert e.ok
    assert e.data["winner"]["tool"] == "fs.patch"

    bad = eng.call("capabilities.explain", {"capability": "no.such.thing"})
    assert not bad.ok and bad.error.code == "BAD_ARGS"


def test_config_advertise_budgets_semantic_flag():
    cfg = Config.load(cwd="/nonexistent-xyz")
    a = cfg.advertise
    assert (a.core_max_tools, a.core_max_tokens) == (20, 1200)
    assert a.budgets()["core"] == {"tools": 20, "tokens": 1200}
    assert cfg.tools.semantic is False
    cfg2 = Config.load(cwd="/nonexistent-xyz", env={"SKELETONKEY_TOOLS__SEMANTIC": "true"})
    assert cfg2.tools.semantic is True
