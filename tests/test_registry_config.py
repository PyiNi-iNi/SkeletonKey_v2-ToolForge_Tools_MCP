"""Config layering + the profile-driven advertisement set (AC-P1.1, AC-P1.3)."""

from __future__ import annotations

import pytest

from skeletonkey.core.config import Config, to_bool, to_float, to_int
from skeletonkey.core.manifest import Requirement, ToolManifest
from skeletonkey.core.profile import CapabilityProfile, ShellProbe
from skeletonkey.core.registry import Registry


# ---------------------------------------------------------------- config
def test_defaults_are_usable_without_any_file():
    cfg = Config.load(cwd="/nonexistent-xyz")
    assert "write" in cfg.policy.auto_approve
    assert cfg.shell.default_dialect is None, "None means: ask the profile"
    assert cfg.budget.max_result_tokens > 0
    assert cfg.roots, "roots always resolve to something"


def test_layers_are_file_then_env_then_overrides(tmp_path):
    path = tmp_path / "sk.toml"
    path.write_text('[shell]\ndefault_dialect = "pwsh"\n\n[budget]\nmax_read_bytes = 1234\n', encoding="utf-8")
    cfg = Config.load(path=path, cwd=str(tmp_path), overrides={"budget": {"max_read_bytes": 99}})
    assert cfg.shell.default_dialect == "pwsh", "file beats defaults"
    assert cfg.budget.max_read_bytes == 99, "overrides beat the file"

    cfg2 = Config.load(path=path, cwd=str(tmp_path), env={"SKELETONKEY_BUDGET__MAX_READ_BYTES": "77"})
    assert cfg2.budget.max_read_bytes == 77, "env beats the file"
    assert cfg2.shell.default_dialect == "pwsh"
    assert cfg2.source_files == [str(path)]


def test_project_config_beats_user_config(tmp_path, monkeypatch):
    """Precedence is a security property: a stray ~/.config file must not be able
    to widen (or skew) what the repository declares."""
    user = tmp_path / "userconf"
    user.mkdir()
    (user / "skeletonkey").mkdir()
    (user / "skeletonkey" / "config.toml").write_text(
        '[policy]\nread_only = false\n', encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(user))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user))
    proj = tmp_path / "proj"
    (proj / "config").mkdir(parents=True)
    (proj / "config" / "config.toml").write_text('[policy]\nread_only = true\n', encoding="utf-8")
    cfg = Config.load(cwd=str(proj), env={"PWD": str(proj)})
    assert cfg.policy.read_only is True


def test_env_values_are_coerced_to_declared_types():
    """A limit that is the *string* "2000000" is worse than no limit at all."""
    cfg = Config.load(cwd="/nonexistent-xyz", env={
        "SKELETONKEY_MCP__PORT": "9111",
        "SKELETONKEY_BUDGET__MAX_RESULT_TOKENS": "1200",
        "SKELETONKEY_POLICY__READ_ONLY": "true",
        "SKELETONKEY_SHELL__TIMEOUT_S": "3.5",
    })
    assert cfg.mcp.port == 9111 and isinstance(cfg.mcp.port, int)
    assert cfg.budget.max_result_tokens == 1200
    assert cfg.policy.read_only is True
    assert cfg.shell.timeout_s == 3.5


def test_comma_separated_env_lists():
    cfg = Config.load(cwd="/nonexistent-xyz", env={"SKELETONKEY_TOOLS__DISABLE": "fs.write, shell.run"})
    assert cfg.tools.disable == ["fs.write", "shell.run"]


def test_unknown_keys_are_recorded_not_fatal(tmp_path):
    path = tmp_path / "sk.toml"
    path.write_text('[fs]\nnot_a_real_key = 5\nallow_dotfiles = "no"\n', encoding="utf-8")
    cfg = Config.load(path=path, cwd=str(tmp_path), env={"SKELETONKEY_NOPE__WHAT": "1"})
    assert any("ignored-unknown" in o for o in cfg.overrides_applied)
    assert cfg.fs.allow_dotfiles is False, "the valid key in the same section still applied"


def test_garbage_toml_does_not_brick_startup(tmp_path):
    path = tmp_path / "sk.toml"
    path.write_text("[broken\n??????\n", encoding="utf-8")
    cfg = Config.load(path=path, cwd=str(tmp_path))
    assert cfg.warnings, "a broken config file must be reported, not swallowed"
    assert cfg.policy.auto_approve


def test_state_and_spill_dirs_default_under_the_workspace(tmp_path):
    cfg = Config.load(cwd=str(tmp_path), overrides={"roots": [str(tmp_path)]})
    assert cfg.state.dir.endswith(".sk")
    assert cfg.budget.spill_dir == cfg.state.dir + "/spill"


@pytest.mark.parametrize(
    "raw,expected",
    [("1", True), ("yes", True), ("ON", True), ("true", True), ("0", False), ("no", False),
     ("maybe", False), ("", False), (None, False), (True, True)],
)
def test_to_bool_accepts_what_humans_type(raw, expected):
    assert to_bool(raw) is expected


@pytest.mark.parametrize("raw,expected", [("12", 12), ("1.9", 1), ("abc", 7), ("", 7), (None, 7)])
def test_to_int_tolerates_garbage(raw, expected):
    assert to_int(raw, default=7) == expected


def test_to_float_tolerates_garbage():
    assert to_float("1.5", default=0) == 1.5
    assert to_float("nope", default=3.3) == 3.3


# ---------------------------------------------------------------- registry
def _manifest(mid, *, risk="read", provider=None, capability="", needs=(), any_needs=(),
              description="", priority=50, **kw):
    return ToolManifest(
        id=mid,
        title=mid,
        description=description or f"tool {mid}",
        risk=risk,
        provider=provider,
        capability=capability,
        priority=priority,
        requirements=[Requirement("binary", n) for n in needs],
        require_any=[Requirement("binary", n) for n in any_needs],
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        **kw,
    )


def _profile(*, os="linux", binaries=None, shells=None, **kw):
    if shells is None:
        shells = {"bash": ShellProbe(dialect="bash", kind="unix", path="/bin/bash",
                                      version=(5, 2), version_text="5.2")}
    return CapabilityProfile(os=os, binaries={"bash": "/bin/bash"} if binaries is None else binaries,
                             shells=shells, **kw)


def test_advertise_drops_tools_the_host_cannot_run():
    reg = Registry()
    reg.register(_manifest("shell.pwsh.thing", needs=("pwsh",)), handler=lambda **kw: {})
    reg.register(_manifest("fs.tar", needs=("nonexistent-binary-xyz",)), handler=lambda **kw: {})
    reg.register(_manifest("fs.read", description="read a file"), handler=lambda **kw: {})
    ad = reg.advertise(profile=_profile())
    assert ad.names == ["fs.read"]
    assert not ad.gates["shell.pwsh.thing"].available
    assert "pwsh" in ad.gates["shell.pwsh.thing"].unmet[0]
    assert ad.gates["fs.read"].available


def test_require_any_is_an_or_not_an_and():
    reg = Registry()
    reg.register(_manifest("fs.search", any_needs=("rg", "grep", "python"),
                           description="search file contents"), handler=lambda **kw: {})
    prof = _profile(binaries={"bash": "/bin/bash", "grep": "/bin/grep"})
    assert reg.advertise(profile=prof).names == ["fs.search"]
    prof_bare = _profile(binaries={})
    assert reg.advertise(profile=prof_bare).names == []


def test_read_only_withholds_mutations_from_advertisement():
    reg = Registry()
    reg.register(_manifest("fs.read", description="read"), handler=lambda **kw: {})
    reg.register(_manifest("fs.write", risk="write", description="write"), handler=lambda **kw: {})
    ad = reg.advertise(profile=_profile(), read_only=True)
    assert ad.names == ["fs.read"]
    assert "read_only" in " ".join(ad.gates["fs.write"].reasons)


def test_platform_gating():
    reg = Registry()
    reg.register(_manifest("win.thing", description="windows only", platforms=["windows"]),
                 handler=lambda **kw: {})
    assert reg.advertise(profile=_profile()).names == []
    reg2 = Registry()
    reg2.register(_manifest("win.thing", description="windows only", platforms=["windows"]),
                  handler=lambda **kw: {})
    assert reg2.advertise(profile=_profile(os="windows")).names == ["win.thing"]


def test_provider_race_keeps_one_tool_per_capability():
    reg = Registry()
    reg.register(_manifest("fs.fast", provider="ripgrep", capability="search.text", priority=90,
                           needs=("rg",), description="search contents"), handler=lambda **kw: {})
    reg.register(_manifest("fs.slow", provider="grep", capability="search.text", priority=40,
                           needs=("grep",), description="search contents"), handler=lambda **kw: {})
    prof = _profile(binaries={"bash": "/bin/bash", "rg": "/bin/rg", "grep": "/bin/grep"})
    ad = reg.advertise(profile=prof)
    assert ad.names == ["fs.fast"], "the host must not see two interchangeable search tools"
    assert ad.selected["search.text"] == "fs.fast"

    prof_norg = _profile(binaries={"bash": "/bin/bash", "grep": "/bin/grep"})
    assert reg.advertise(profile=prof_norg).names == ["fs.slow"]


def test_variants_sharing_a_capability_are_not_collapsed():
    """Only provider races collapse; jobs/wait/kill share a namespace but are not
    interchangeable, so silently hiding one would break the workflow."""
    reg = Registry()
    reg.register(_manifest("fs.read", capability="fs.read", provider=None, description="read"),
                 handler=lambda **kw: {})
    reg.register(_manifest("fs.sniff", capability="fs.read", provider=None, description="sniff"),
                 handler=lambda **kw: {})
    assert reg.advertise(profile=_profile()).names == ["fs.read", "fs.sniff"]


def test_token_budget_trims_by_score_not_by_order():
    reg = Registry()
    for i in range(10):
        reg.register(_manifest(
            f"t.tool{i}",
            description="edit files precisely with fuzzy patching" if i == 0 else "x" * 400,
            priority=90 if i == 0 else 10,
        ), handler=lambda **kw: {})
    ad = reg.advertise(profile=_profile(), token_budget=400)
    assert len(ad.names) < 10
    assert "t.tool0" in ad.names, "the best match must survive trimming"
    assert ad.tokens <= 400 + 120


def test_disabled_list_is_honoured():
    reg = Registry()
    reg.register(_manifest("fs.read", description="read"), handler=lambda **kw: {})
    assert reg.advertise(profile=_profile(), disabled={"fs.read"}).names == []


def test_internal_tools_are_hidden_but_still_callable():
    reg = Registry()
    reg.register(_manifest("registry.search", description="internal helper", advertised=False),
                 handler=lambda **kw: {})
    ad = reg.advertise(profile=_profile())
    assert ad.names == []
    assert "advertised" in " ".join(ad.gates["registry.search"].reasons).lower()
    assert reg.advertise(profile=_profile(), include_internal=True).names == ["registry.search"]


def test_digest_changes_when_advertisement_changes():
    prof = _profile()
    reg = Registry()
    d0 = reg.advertise(profile=prof).digest
    reg.register(_manifest("fs.read", description="read"), handler=lambda **kw: {})
    d1 = reg.advertise(profile=prof).digest
    assert d0 != d1
    assert reg.advertise(profile=prof).digest == d1, "must be stable across calls"


def test_duplicate_registration_requires_replace():
    from skeletonkey.core.errors import SkeletonKeyError

    reg = Registry()
    reg.register(_manifest("fs.read", description="read"), handler=lambda **kw: {})
    with pytest.raises(SkeletonKeyError):
        reg.register(_manifest("fs.read", description="again"), handler=lambda **kw: {})
    reg.register(_manifest("fs.read", description="again"), handler=lambda **kw: {}, replace=True)
    assert reg.get("fs.read").description == "again"


def test_search_ranks_by_coverage_not_substrings():
    reg = Registry()
    reg.register(_manifest("fs.read", description="read a text file with paging"), handler=lambda **kw: {})
    reg.register(_manifest("fs.search", description="search file contents with ripgrep regex"),
                 handler=lambda **kw: {})
    reg.register(_manifest("fs.glob", description="find files by glob pattern"), handler=lambda **kw: {})
    assert reg.search("search file contents", limit=1)[0]["id"] == "fs.search"
    assert reg.search("find files by pattern", limit=1)[0]["id"] == "fs.glob"
    assert reg.search("read a file", limit=1)[0]["id"] == "fs.read"
    assert reg.search("xyzzy nothing matches", limit=5) == []


def test_search_filters():
    reg = Registry()
    reg.register(_manifest("fs.read", description="read a file", risk="read"), handler=lambda **kw: {})
    reg.register(_manifest("fs.delete", description="delete a file forever", risk="destructive",
                           destructive=True), handler=lambda **kw: {})
    assert [h["id"] for h in reg.search("file", max_risk="read")] == ["fs.read"]
    assert any(h["id"] == "fs.delete" for h in reg.search("delete file", limit=5))
    reg.register(_manifest("fs.write", description="write a file", needs=("nope",)),
                 handler=lambda **kw: {})
    reg.profile = _profile()
    hits = reg.search("write a file", include_gated=True)
    assert hits and hits[0]["gated"]["available"] is False, "gated tools must explain themselves"


def test_advertisement_snapshot_diff_drives_list_changed():
    prof = _profile()
    reg = Registry()
    snap_a = reg.advertise(profile=prof)
    reg.register(_manifest("fs.read", description="read"), handler=lambda **kw: {})
    reg.register(_manifest("fs.write", risk="write", description="write"), handler=lambda **kw: {})
    snap_b = reg.advertise(profile=prof, read_only=True)
    diff = snap_a.diff(snap_b)
    assert diff == {"added": ["fs.read"], "removed": []}
    assert snap_b.diff(snap_a) == {"added": [], "removed": ["fs.read"]}
