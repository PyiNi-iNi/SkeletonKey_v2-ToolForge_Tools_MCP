"""`sk doctor`: the diagnostics contract.

The promise: one stable-schema JSON blob that answers "what does this host actually
see", whose `mcp.stdio` check is a *live* end-to-end conversation with the real server
(initialize -> tools/list -> a read-only fs.stat), and whose diagnosis is read-only on
the operator's state - only `--fix` writes, and it says what it wrote.
"""

from __future__ import annotations

import json
import os

import pytest

from skeletonkey import diagnostics
from skeletonkey.cli import main as sk_main

EXPECTED_ORDER = ["meta", "config", "roots", "state", "tools", "skills", "profile",
                  "journal", "ledger", "mcp.stdio", "wire"]


def ws(tmp_path):
    w = tmp_path / "ws"
    w.mkdir(exist_ok=True)
    (w / "hello.txt").write_text("hi\n", encoding="utf-8")
    return str(w)


def test_doctor_is_healthy_end_to_end_on_a_fresh_workspace(tmp_path):
    rep = diagnostics.doctor(cwd=ws(tmp_path), env=dict(os.environ))
    assert rep["schema"] == diagnostics.SCHEMA
    assert [c["id"] for c in rep["checks"]] == EXPECTED_ORDER      # stable, documented order
    assert rep["ok"] is True, json.dumps(rep, indent=1)[:2000]
    live = next(c for c in rep["checks"] if c["id"] == "mcp.stdio")
    assert live["ok"] and live["tools"] > 40 and live["call_ok"] is True
    assert live["call_ms"] >= 0 and live["digest"]                 # real list conversation
    tools = next(c for c in rep["checks"] if c["id"] == "tools")
    assert tools["registered"] >= tools["advertised"] > 40
    # read-only promise: diagnosing a fresh workspace must not create its state dir
    assert not (tmp_path / "ws" / ".sk").exists()


def test_probe_stdio_directly_proves_exposed_and_callable(tmp_path):
    rep = diagnostics.probe_stdio()
    assert rep["ok"] is True, rep.get("error")
    assert rep["tools"] > 40 and rep.get("call_ok") is True
    assert rep["call_ms"] >= 0
    assert not os.path.isdir(rep["workspace"])                     # cleaned up on success


def test_probe_reports_a_dead_server_with_the_reason():
    dead = "NUL:" if os.name == "nt" else "/bin/false"
    if not os.path.exists(dead.rstrip(":")):
        pytest.skip("no dead-binary stand-in on this platform")
    rep = diagnostics.probe_stdio(python=dead)
    assert rep["ok"] is False
    assert "exited early" in rep["error"]


def test_fresh_workspace_state_is_healthy_not_a_failure(tmp_path):
    rep = diagnostics.doctor(cwd=ws(tmp_path), probe=False)
    st = next(c for c in rep["checks"] if c["id"] == "state")
    assert st["ok"] is True and st["existed_before"]["state"] is False
    assert "never ran" in (st["hint"] or "")
    j = next(c for c in rep["checks"] if c["id"] == "journal")
    assert j["ok"] and j["entries"] == 0


def test_doctor_reports_partial_state_and_fix_repairs_it(tmp_path):
    env = dict(os.environ)
    state = tmp_path / "elsewhere" / "state"
    state.mkdir(parents=True)                       # ran before: state exists, spill missing
    env["SKELETONKEY_STATE_DIR"] = str(state)
    rep = diagnostics.doctor(cwd=ws(tmp_path), env=env, probe=False)
    st = next(c for c in rep["checks"] if c["id"] == "state")
    assert st["ok"] is False and st["existed_before"]["state"] is True
    assert not (state / "spill").exists()                          # diagnosis wrote nothing

    fixed = diagnostics.doctor(cwd=ws(tmp_path), env=env, probe=False, fix=True)
    st2 = next(c for c in fixed["checks"] if c["id"] == "state")
    assert st2["ok"] is True and fixed["ok"] is True
    assert any(f["fix"].startswith("created spill") for f in fixed["fixes"])
    assert (state / "spill").is_dir()


def test_doctor_wire_check_sees_a_wired_host(tmp_path):
    from skeletonkey import wire as wire_mod

    env = dict(os.environ)
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["HOME"] = str(home)
    wire_mod.wire(host_ids=["claude-desktop"], env=env)
    rep = diagnostics.doctor(cwd=ws(tmp_path), env=env, probe=False)
    w = next(c for c in rep["checks"] if c["id"] == "wire")
    assert w["wired_anywhere"] is True
    assert any(r["host"] == "claude-desktop" and r["wired"] for r in w["hosts"])
    assert w["hint"] is None


def test_no_probe_skips_the_live_check_without_failing_it(tmp_path):
    rep = diagnostics.doctor(cwd=ws(tmp_path), probe=False)
    live = next(c for c in rep["checks"] if c["id"] == "mcp.stdio")
    assert live["ok"] and live["skipped"] and live["reason"] == "--no-probe"


def test_doctor_schema_is_identical_across_runs(tmp_path):
    def strip(rep):
        return [{c["id"]: sorted(c.keys())} for c in rep["checks"]]

    a = diagnostics.doctor(cwd=ws(tmp_path), probe=False)
    b = diagnostics.doctor(cwd=ws(tmp_path), probe=False)
    assert strip(a) == strip(b)


# --------------------------------------------------------------------- CLI
def test_cli_doctor_json_and_human(tmp_path, capsys):
    rc = sk_main(["--json", "--cwd", ws(tmp_path), "doctor", "--no-probe"])
    out = capsys.readouterr()
    assert rc == 0
    rep = json.loads(out.out)
    assert rep["schema"] == diagnostics.SCHEMA

    rc2 = sk_main(["--cwd", str(tmp_path / "ws"), "doctor", "--no-probe"])
    text = capsys.readouterr()
    assert rc2 == 0
    assert "doctor: OK" in text.err and f'"{diagnostics.SCHEMA}"' in text.out


def test_cli_doctor_exits_1_when_a_check_fails(tmp_path, capsys):
    rc = sk_main(["--json", "--cwd", ws(tmp_path), "doctor", "--no-probe"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True
    # force a failure deterministically: unparsable config file
    bad = tmp_path / "bad.toml"
    bad.write_text("this is [ not toml", encoding="utf-8")
    rc2 = sk_main(["--json", "--cwd", ws(tmp_path), "doctor", "--no-probe",
                   "--config", str(bad)])
    out2 = json.loads(capsys.readouterr().out)
    assert rc2 == 1 and out2["ok"] is False
    cfg = next(c for c in out2["checks"] if c["id"] == "config")
    assert cfg["ok"] is False and any("not valid TOML" in w for w in cfg["warnings"])
