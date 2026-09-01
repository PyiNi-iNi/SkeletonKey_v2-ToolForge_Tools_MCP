"""P6 `sk doctor` — stability, completeness, schema, and safe --fix behavior."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from skeletonkey.cli import main as sk_main
from skeletonkey.doctor import collect, safe_fixes
from skeletonkey.toolkit import build


def _build_tk(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("X = 1\n", encoding="utf-8")
    tk = build(roots=[str(root)], cwd=str(root))
    return tk


def test_doctor_is_stable_json_schema(capsys, tmp_path):
    tk = _build_tk(tmp_path)
    try:
        doc = collect(tk)
        assert doc["schema"] == 1
        # the keys an operator pastes: all present, no None surprises
        for key in ("version", "python", "config", "profile", "advertise",
                    "gates", "registry", "skills", "state", "remote", "build"):
            assert key in doc, key
        jobj = json.dumps(doc, sort_keys=True)
        assert json.loads(jobj) == doc  # serialisable: paste-able
        # secrets are never included: no top-level/nested key is cred-like
        # ("tokens" is a legit token *count*; exact key names are what matters)
        cred_keys = {"token", "password", "secret", "api_key", "ssh_key",
                     "value", "credential", "private_key"}

        def _keys(obj: Any) -> list[str]:
            out: list[str] = []
            if isinstance(obj, dict):
                out.extend(obj.keys())
                for v in obj.values():
                    out.extend(_keys(v))
            elif isinstance(obj, list):
                for v in obj:
                    out.extend(_keys(v))
            return out

        assert not (set(_keys(doc)) & cred_keys), set(_keys(doc)) & cred_keys
        # redundancy, not noise: ledger verify + stats when enabled
        assert doc["state"]["ledger"]["enabled"] is True
        assert "valid" in doc["state"]["ledger"]
        assert doc["advertise"]["registered"] >= 50
        assert doc["advertise"]["advertised"] <= doc["advertise"]["registered"]
        # a gate row exists for the known manifest-withheld tool (tmp build has
        # no skills dir, so shell.selftest is not registered here)
        assert "skills.install" in doc["gates"]
        assert doc["gates"]["skills.install"]["reasons"]
        assert doc["gates"]["skills.install"]["advertised"] is False
        assert "allow_install" in doc["gates"]["skills.install"]["hidden_reason"]
        # remote section is an ordered report, never a crash with no remotes
        assert doc["remote"] == {"servers": [], "registered": [], "errors": []}
    finally:
        tk.close()


def test_doctor_cli_is_stable_and_jqable(capsys, tmp_path):
    tk = _build_tk(tmp_path)
    tk.close()
    try:
        rc = sk_main(["--cwd", str(tmp_path / "ws"), "doctor"])
        out = capsys.readouterr().out
        assert rc == 0
        doc = json.loads(out)
        assert doc["schema"] == 1 and doc["version"]
    except SystemExit as exc:
        assert exc.code in (0, None)


def test_doctor_safe_fix_creates_state_dirs(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "src").mkdir()
    cfg_dir = tmp_path / "state" / "spill"
    tk = build(roots=[str(root)], cwd=str(root), overrides={
        "state": {"dir": str(cfg_dir / "st")},
    })
    try:
        # point spill into a fresh parent that doesn't exist
        tk.config.budget.spill_dir = str(cfg_dir / "sp2")
        tk.config.state.dir = str(cfg_dir / "st")
        applied = safe_fixes(tk)
        assert any("created" in a for a in applied)
        assert os.path.isdir(tk.config.state.dir)
        assert os.path.isdir(tk.config.budget.spill_dir)
    finally:
        tk.close()


def test_doctor_has_no_access_to_store_values(tmp_path):
    """doctor reports the publish store path, never its contents (ADR-0010)."""
    tk = _build_tk(tmp_path)
    try:
        doc = collect(tk)
        store = str(tk.publish.path) if tk.publish else None
        if store:
            # the path may appear (it's in build.publish_store), but no value rows
            assert "value" not in doc
    finally:
        tk.close()
