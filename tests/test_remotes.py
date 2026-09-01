"""P5b remote MCP servers (ADR-0013): enrollment, passthrough, honesty.

Engine-level: an outer toolkit enrolls a real second skeletonkey MCP server
(child process) as `remote.demo.*`; the wire-level counterpart lives in
tests/test_mcp_stdio.py. Every assertion here is about the *envelope* data the
autopilot reads - not about the connector's internals.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from skeletonkey.core.config import Config
from skeletonkey.toolkit import build
from tests.remote_helpers import write_remote_root


def _cfg(root: Path, remote_root: Path, **remote_extra: dict) -> Config:
    cfg = Config.load(cwd=str(root), overrides={
        "roots": [str(root)],
        "skills": {"dirs": []},
        "mcp": {"remotes": {"demo": {
            "command": sys.executable,
            "args": ["-m", "skeletonkey.mcp", "--cwd", str(remote_root)],
            **remote_extra,
        }}},
    })
    cfg.workspace = str(root)
    return cfg


@pytest.fixture()
def outer(tmp_path: Path):
    """Outer toolkit with a live remote server enrolled at build time."""
    remote = write_remote_root(tmp_path / "remote")
    cfg = _cfg(tmp_path / "outer", remote)
    tk = build(config=cfg)
    yield tk
    tk.close()


def test_remote_tools_registered_and_advertised(outer):
    reg = outer.engine.registry
    assert reg.has("remote.demo.demo.echo") and reg.has("remote.demo.demo.bad")
    names = set(reg.advertise().names)
    assert "remote.demo.demo.echo" in names and "remote.demo.demo.bad" in names
    man = reg.get("remote.demo.demo.echo")
    assert man.group == "remote" and man.source == "remote:demo"
    assert man.reversible is False and man.stateful == "host"
    assert man.risk == "read"  # risk="none" -> readOnlyHint true on the wire


def test_remote_call_passes_through_payload(outer):
    r = outer.engine.call("remote.demo.demo.echo", {"text": "hi there"})
    assert r.ok, r.error
    assert r.data["echoed"] == "hi there" and r.data["upper"] == "HI THERE"
    assert r.metrics.provider == "remote", r.metrics.provider
    assert outer.engine.registry.stats("remote.demo.demo.echo")[
        "remote.demo.demo.echo"]["calls"] >= 1


def test_remote_error_code_passes_through_verbatim(outer):
    r = outer.engine.call("remote.demo.demo.bad", {"why": "test"})
    assert not r.ok
    assert r.error.code == "BAD_ARGS", f"remote code must not be re-wrapped: {r.error.code}"
    assert r.error.details.get("why") == "test"
    assert "remote refuses" in r.error.message


def test_remote_unknown_tool_is_spelled_out(outer):
    r = outer.engine.call("remote.demo.nope", {})
    assert not r.ok and r.error.code == "UNKNOWN_TOOL"
    assert isinstance(r.error.details.get("suggested"), list)


def test_stats_keep_remote_and_local_rows_separate(outer):
    reg = outer.engine.registry
    outer.engine.call("remote.demo.demo.echo", {"text": "x"})
    outer.engine.call("fs.list", {})
    remote_rows = reg.stats(source="remote:demo")
    assert remote_rows and all(v["source"] == "remote:demo" for v in remote_rows.values())
    local = reg.stats(source="builtin")
    assert "remote.demo.demo.echo" not in local
    # tool-level shape is unchanged (keyed by id)
    assert reg.stats("remote.demo.demo.echo")["remote.demo.demo.echo"]["source"] == "remote:demo"
    by_source = reg.stats_by_source()
    assert "remote:demo" in by_source and "builtin" in by_source


def test_remote_failure_is_a_load_error_not_a_silent_absence(tmp_path):
    root = tmp_path / "outer"
    cfg = _cfg(root, tmp_path / "nowhere", command="/definitely/not/a/real/sk-binary")
    tk = build(config=cfg)
    try:
        report = tk.build_report.get("remote", {})
        assert report["errors"], report
        names = set(tk.engine.registry.all_ids() if hasattr(tk.engine.registry, "all_ids")
                    else [m.id for m in tk.engine.registry.all()])
        assert not any(n.startswith("remote.demo.") for n in names)
        assert any(e.get("stage") in ("connect", "config") for e in report["errors"])
        assert tk.engine.registry.load_errors
    finally:
        tk.close()


def test_disabled_remote_is_reported_not_enrolled(tmp_path):
    root = tmp_path / "outer"
    remote = write_remote_root(tmp_path / "remote")
    cfg = _cfg(root, remote, enabled=False)
    tk = build(config=cfg)
    try:
        report = tk.build_report.get("remote", {})
        assert report["errors"] and any("disabled" in e.get("error", "") for e in report["errors"])
        assert not reg_has_remote(tk)
    finally:
        tk.close()


def reg_has_remote(tk) -> bool:
    return any(m.id.startswith("remote.") for m in tk.engine.registry.all())


def test_bad_spec_is_a_config_error_not_a_crash(tmp_path):
    root = tmp_path / "outer"
    cfg = Config.load(cwd=str(root), overrides={
        "roots": [str(root)],
        "mcp": {"remotes": {"badname": {"command": sys.executable}}},
    })
    tk = build(config=cfg)
    try:
        errors = tk.build_report.get("remote", {}).get("errors", [])
        assert errors and any(e.get("stage") == "config" for e in errors)
        assert not reg_has_remote(tk)
    finally:
        tk.close()
