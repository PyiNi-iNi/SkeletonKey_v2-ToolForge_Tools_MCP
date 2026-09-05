"""`sk wire`: the auto-wire contract, driven through the same function the CLI calls.

The promises under test: a wire never loses user content (merge, not rewrite; comments
are refused, not dropped), a second wire is "already" (idempotent, never a duplicate),
`--remove` takes out exactly what `sk wire` put in (and never a hand-written entry),
and every write is atomic with a restorable backup. Path resolution is aimed at a temp
home through the `env` parameter - the suite never touches a real host config.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from skeletonkey import wire as wire_mod
from skeletonkey.cli import main as sk_main

PY = sys.executable


def _rd(path):
    return Path(path).read_text(encoding="utf-8")


def fake_env(tmp_path, monkeypatch=None):
    e = {"HOME": str(tmp_path / "home"), "USERPROFILE": str(tmp_path / "home"),
         "XDG_CONFIG_HOME": str(tmp_path / "home" / ".config"),
         "APPDATA": str(tmp_path / "home" / "AppData" "Roaming"),
         "PWD": str(tmp_path / "ws")}
    os.makedirs(e["HOME"], exist_ok=True)
    os.makedirs(e["PWD"], exist_ok=True)
    return e


def claude_desktop_path(env):
    if sys.platform == "darwin":
        return os.path.join(env["HOME"], "Library", "Application Support", "Claude",
                            "claude_desktop_config.json")
    if os.name == "nt":
        return os.path.join(env["APPDATA"], "Claude", "claude_desktop_config.json")
    return os.path.join(env["XDG_CONFIG_HOME"], "Claude", "claude_desktop_config.json")


# --------------------------------------------------------------------- wire + merge
def test_wire_creates_a_fresh_user_config_with_the_right_stanza(tmp_path):
    env = fake_env(tmp_path)
    rep = wire_mod.wire(host_ids=["claude-desktop"], env=env)
    row = rep["hosts"][0]
    assert rep["ok"] and row["status"] == "wired"
    data = json.loads(_rd(claude_desktop_path(env)))
    entry = data["mcpServers"]["skeletonkey"]
    assert entry["command"] == PY
    assert entry["args"][:3] == ["-m", "skeletonkey.mcp"]
    # no state dir, no journal, no toolkit: wiring is config-file surgery, nothing else
    assert not os.path.isdir(os.path.join(env["PWD"], ".sk"))


def test_wire_merges_and_preserves_foreign_servers_and_keys(tmp_path):
    env = fake_env(tmp_path)
    path = claude_desktop_path(env)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Path(path).write_text(json.dumps({"otherkey": {"a": 1},
                                      "mcpServers": {"weather": {"command": "/bin/weather",
                                                                 "args": ["--x"]}}}),
                          encoding="utf-8")
    rep = wire_mod.wire(host_ids=["claude-desktop"], env=env)
    assert rep["hosts"][0]["status"] == "wired"
    data = json.loads(_rd(path))
    assert data["otherkey"] == {"a": 1}
    assert data["mcpServers"]["weather"] == {"command": "/bin/weather", "args": ["--x"]}
    assert "skeletonkey" in data["mcpServers"]


def test_second_wire_is_already_never_a_duplicate(tmp_path):
    env = fake_env(tmp_path)
    first = wire_mod.wire(host_ids=["cursor"], env=env)
    second = wire_mod.wire(host_ids=["cursor"], env=env)
    assert first["hosts"][0]["status"] == "wired"
    assert second["hosts"][0]["status"] == "already"
    data = json.loads(_rd(os.path.join(env["HOME"], ".cursor", "mcp.json")))
    assert list(data["mcpServers"]).count("skeletonkey") == 1


def test_changed_stanza_updates_and_backs_up(tmp_path):
    env = fake_env(tmp_path)
    wire_mod.wire(host_ids=["claude-desktop"], env=env)
    path = claude_desktop_path(env)
    before = _rd(path)
    rep = wire_mod.wire(host_ids=["claude-desktop"], env=env, read_only=True)
    row = rep["hosts"][0]
    assert row["status"] == "updated" and row["backup"]
    assert json.loads(_rd(row["backup"])) == json.loads(before)
    entry = json.loads(_rd(path))["mcpServers"]["skeletonkey"]
    assert entry["args"][-1] == "--read-only"
    assert "--read-only" not in before


def test_roots_are_pinned_into_the_stanza(tmp_path):
    env = fake_env(tmp_path)
    rep = wire_mod.wire(host_ids=["claude-desktop"], env=env, roots=["/srv/proj"])
    entry = rep["hosts"][0]["stanza"]["skeletonkey"]
    assert entry["args"][-2:] == ["--root", "/srv/proj"]


# --------------------------------------------------------------------- removal
def test_remove_takes_out_ours_and_refuses_a_hand_written_entry(tmp_path):
    env = fake_env(tmp_path)
    wire_mod.wire(host_ids=["claude-desktop"], env=env)
    rep = wire_mod.wire(host_ids=["claude-desktop"], env=env, remove=True)
    assert rep["hosts"][0]["status"] == "removed"
    assert "skeletonkey" not in json.loads(_rd(claude_desktop_path(env)))["mcpServers"]

    path = claude_desktop_path(env)
    Path(path).write_text(json.dumps(
        {"mcpServers": {"skeletonkey": {"command": "/usr/bin/sk-mine"}}}), encoding="utf-8")
    rep2 = wire_mod.wire(host_ids=["claude-desktop"], env=env, remove=True)
    assert rep2["hosts"][0]["status"] == "skipped"
    assert "not ours" in rep2["hosts"][0]["reason"]
    # the hand-written entry survives
    assert "skeletonkey" in json.loads(_rd(path))["mcpServers"]


# --------------------------------------------------------------------- jsonc honesty
VSCODE_JSONC = """{
  // comments live here: which servers I actually use
  "servers": {
    "weather": { "command": "/bin/weather", },  // trailing comma too
  },
}
"""


def test_jsonc_file_is_reported_needs_manual_not_silently_rewritten(tmp_path):
    env = fake_env(tmp_path)
    p = os.path.join(env["XDG_CONFIG_HOME"], "Code", "User")
    os.makedirs(p, exist_ok=True)
    with open(os.path.join(p, "mcp.json"), "w", encoding="utf-8") as fh:
        fh.write(VSCODE_JSONC)
    rep = wire_mod.wire(host_ids=["vscode"], env=env)
    row = rep["hosts"][0]
    assert row["status"] == "needs-manual"
    assert rep["ok"] is False                       # a manual step is a real exit-1 state
    assert "skeletonkey" in json.dumps(row["stanza"])
    # and nothing was written: the file still parses only as JSONC
    assert "// comments" in _rd(os.path.join(p, "mcp.json"))


def test_jsonc_rewrite_requires_the_explicit_flag_and_keeps_data(tmp_path):
    env = fake_env(tmp_path)
    p = os.path.join(env["XDG_CONFIG_HOME"], "Code", "User")
    os.makedirs(p, exist_ok=True)
    with open(os.path.join(p, "mcp.json"), "w", encoding="utf-8") as fh:
        fh.write(VSCODE_JSONC)
    rep = wire_mod.wire(host_ids=["vscode"], env=env, allow_jsonc=True)
    row = rep["hosts"][0]
    assert row["status"] == "wired" and "comments were dropped" in row.get("note", "")
    data = json.loads(_rd(os.path.join(p, "mcp.json")))
    assert data["servers"]["weather"]["command"] == "/bin/weather"
    assert "skeletonkey" in data["servers"]


# --------------------------------------------------------------------- transports
def test_http_stanza_written_for_hosts_that_support_it(tmp_path):
    env = fake_env(tmp_path)
    rep = wire_mod.wire(host_ids=["claude-code"], env=env, transport="http", port=8123)
    entry = rep["hosts"][0]["stanza"]["skeletonkey"]
    assert entry == {"type": "http", "url": "http://127.0.0.1:8123/mcp"}


def test_http_refused_for_stdio_only_host_with_the_reason(tmp_path):
    env = fake_env(tmp_path)
    rep = wire_mod.wire(host_ids=["claude-desktop"], env=env, transport="http")
    row = rep["hosts"][0]
    assert row["status"] == "skipped" and "stdio" in row["reason"]
    assert not os.path.exists(claude_desktop_path(env))


def test_unknown_host_is_an_error_row_not_a_crash(tmp_path):
    env = fake_env(tmp_path)
    rep = wire_mod.wire(host_ids=["no-such-host"], env=env)
    assert rep["ok"] is False and rep["hosts"][0]["status"] == "error"


# --------------------------------------------------------------------- project scope
def test_project_scope_writes_the_project_config_with_a_pinned_root(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    rep = wire_mod.wire(host_ids=["claude-code"], scope="project", cwd=str(ws), env={})
    row = rep["hosts"][0]
    assert row["status"] == "wired" and row["path"] == str(ws / ".mcp.json")
    entry = json.loads((ws / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["skeletonkey"]
    assert entry["args"][-2:] == ["--root", str(ws)]        # project root pinned


# --------------------------------------------------------------------- check / dry-run
def test_check_and_dry_run_write_nothing(tmp_path):
    env = fake_env(tmp_path)
    for kw in ({"check_only": True}, {"dry_run": True}):
        rep = wire_mod.wire(host_ids=["claude-desktop"], env=env, **kw)
        assert rep["hosts"][0]["status"] in ("dry-run", "checked")
        assert not os.path.exists(claude_desktop_path(env))
    wire_mod.wire(host_ids=["claude-desktop"], env=env)
    rep = wire_mod.wire(host_ids=["claude-desktop"], env=env, dry_run=True, remove=True)
    assert rep["hosts"][0]["status"] == "dry-run"
    assert "skeletonkey" in json.loads(_rd(claude_desktop_path(env)))["mcpServers"]


def test_status_rows_reports_installed_and_wired(tmp_path):
    env = fake_env(tmp_path)
    rows = wire_mod.status_rows(env=env)
    assert {r["host"] for r in rows} >= {"claude-desktop", "cursor", "vscode"}
    assert all(r["installed"] is False and r["wired"] is False for r in rows)
    wire_mod.wire(host_ids=["cursor"], env=env)
    rows = wire_mod.status_rows(env=env)
    by = {r["host"]: r for r in rows}
    assert by["cursor"]["installed"] and by["cursor"]["wired"] and by["cursor"]["wired_entry"]


# --------------------------------------------------------------------- CLI end to end
def test_cli_wire_project_then_check_then_remove(tmp_path, capsys):
    # global flags go BEFORE the subcommand (argparse aborts otherwise)
    ws = tmp_path / "proj"
    ws.mkdir()
    rc = sk_main(["--json", "--cwd", str(ws), "wire", "--project"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    by = {r["host"]: r for r in out["hosts"]}
    # user-scope-only hosts are skipped in project mode - never a fallback to the real home
    assert by["claude-desktop"]["status"] == "skipped" and "no project" in by["claude-desktop"]["reason"]
    assert by["claude-code"]["status"] == "wired"
    assert (ws / ".mcp.json").is_file()
    rc = sk_main(["--json", "--cwd", str(ws), "wire", "--project", "claude-code", "--check"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["hosts"][0]["status"] == "already"
    rc = sk_main(["--json", "--cwd", str(ws), "wire", "--project", "claude-code", "--remove"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["hosts"][0]["status"] == "removed"
    text = (ws / ".mcp.json").read_text(encoding="utf-8")
    assert not text.strip() or "skeletonkey" not in text


def test_cli_human_output_and_host_alias(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    rc = sk_main(["wire", "claude"])
    assert rc == 0
    text = capsys.readouterr().out
    assert "wired" in text and "claude-desktop" in text


def test_cli_unknown_host_exits_1(tmp_path, capsys):
    rc = sk_main(["--json", "wire", "definitely-not-a-host"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["hosts"][0]["status"] == "error"


@pytest.mark.parametrize("bad", ["{ not json", "[]"])
def test_unparsable_config_is_an_error_never_an_overwrite(tmp_path, bad):
    env = fake_env(tmp_path)
    path = claude_desktop_path(env)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Path(path).write_text(bad, encoding="utf-8")
    rep = wire_mod.wire(host_ids=["claude-desktop"], env=env)
    row = rep["hosts"][0]
    assert row["status"] == "error" and _rd(path) == bad
