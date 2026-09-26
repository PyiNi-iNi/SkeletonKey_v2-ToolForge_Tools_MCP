"""Wire tests for the ``sandbox.*`` skill tools (skills/sandbox pack).

(Named ``test_sandbox_tools`` because ``test_sandbox`` is already taken by the path-sandbox
security suite.) These are the "house rule" tests (HANDOFF §7): the pack is shipped in the
repo's ``skills/`` dir, so we exercise it through ``engine.call`` over a real subprocess exactly
as a host would, in a throwaway workspace. The pack's logic lives in
``scripts/sandboxlib.py``, which we also load directly for pure path-checking cases.

Isolation claim under test: create/run/status agree on a real on-disk sandbox whose own venv,
when present, is the interpreter a ``python`` run resolves to.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import tomllib

import pytest

from skeletonkey.core.config import Config
from skeletonkey.skills.compiler import compile_tool
from skeletonkey.skills.loader import SkillLoader
from skeletonkey.toolkit import build

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_DIR = os.path.join(REPO, "skills", "sandbox")
TOOL_IDS = ("sandbox.create", "sandbox.runtime", "sandbox.run", "sandbox.status")


@pytest.fixture(scope="module")
def sbk(tmp_path_factory):
    """Toolkit whose skills path is the *repo's* shipped packs; writes auto-approved."""
    root = tmp_path_factory.mktemp("sbtools")
    cfg = Config.load(cwd=str(root), overrides={
        "roots": [str(root)],
        "state": {"dir": str(root / ".sk")},
        "shell": {"tempdir": str(root / ".sk" / "shell")},
        "skills": {"dirs": [os.path.join(REPO, "skills")]},
        "policy": {"auto_approve": ["none", "read", "write", "destructive"],
                   "confirm_destructive": False},
        "log_level": "ERROR"})
    tk = build(config=cfg)
    try:
        yield tk, root
    finally:
        tk.close()


def call(tk, tool: str, **args):
    return tk.engine.call(tool, args)


def result(r):
    """The pack returns one JSON doc under data['result'] (its own ok field)."""
    assert r.ok, r.error
    return r.data["result"]


# ------------------------------------------------------------------ registration
def test_pack_registers_and_advertises_all_four_tools(sbk):
    tk, _ = sbk
    reg = {m.id for m in tk.registry.all()}
    for tid in TOOL_IDS:
        assert tid in reg, f"{tid} not registered"
        man = tk.registry.get(tid)
        assert man.advertised, f"{tid} should be advertised"
    # none of the pack's declarations may be load errors
    errs = [e for e in tk.registry.load_errors if "sandbox" in str(e)]
    assert errs == [], errs


def test_pack_discovered_and_guidance_wellformed(sbk):
    sl = SkillLoader([os.path.join(REPO, "skills")])
    skill = next((s for s in sl.discover() if s.name == "sandbox"), None)
    assert skill is not None
    assert skill.description and skill.when_to_use and skill.allowed_tools
    assert skill.token_estimate < 4000, "move detail to references/ if this grows"
    assert skill.body.startswith("# ")


# ------------------------------------------------------------------ create
def test_create_scaffolds_python_lib(sbk):
    tk, root = sbk
    r = result(call(tk, "sandbox.create", name="s1", template="python-lib",
                    description="integration", files={"notes.md": "# notes\n"}))
    assert r["created"] and r["template"] == "python-lib"
    sb = root / "s1"
    assert sb.is_dir()
    assert (sb / ".sandbox" / "manifest.json").is_file()
    for rel in ("README.md", "pyproject.toml", "src/s1/__init__.py",
                "tests/test_smoke.py", "notes.md"):
        assert (sb / rel).is_file(), f"expected {rel}"
    assert ".gitignore" in r["files_written"]


def test_create_dry_run_writes_nothing(sbk):
    tk, root = sbk
    r = result(call(tk, "sandbox.create", name="s2", template="minimal", dry_run=True))
    assert r["dry_run"] is True and r["name"] == "s2"
    assert not (root / "s2").exists(), "dry_run must not touch disk"


def test_create_conflicts_on_existing_nonempty_unless_force(sbk):
    tk, root = sbk
    result(call(tk, "sandbox.create", name="s3", template="minimal"))
    (root / "s3" / "keep.txt").write_text("do not delete me", encoding="utf-8")
    # a conflicting create is reported in the result, not a crash
    r = result(call(tk, "sandbox.create", name="s3", template="python-app"))
    assert r["ok"] is False and r["error"]["code"] == "CONFLICT"
    assert (root / "s3" / "keep.txt").is_file(), "existing files are never removed"


def test_create_rejects_unsafe_name(sbk):
    tk, _ = sbk
    for bad in ("a/b", "..", ".hidden", "a b"):
        r = result(call(tk, "sandbox.create", name=bad, template="minimal", dry_run=True))
        assert r["ok"] is False, f"name {bad!r} should be refused"
        assert r["error"]["code"] == "BAD_ARGS"


# ------------------------------------------------------------------ run
def test_run_inside_sandbox_cwd(sbk):
    tk, root = sbk
    result(call(tk, "sandbox.create", name="s4", template="generic"))
    r = result(call(tk, "sandbox.run", name="s4", root=str(root), argv=["pwd"]))
    assert r["exit_code"] == 0, r
    assert os.path.realpath(r["cwd"]) == os.path.realpath(str(root / "s4"))
    assert os.path.realpath(r["stdout"].strip()) == os.path.realpath(str(root / "s4"))


def test_run_reports_failure_and_times_out(sbk):
    tk, root = sbk
    result(call(tk, "sandbox.create", name="s5", template="generic"))
    r = result(call(tk, "sandbox.run", name="s5", root=str(root),
                    argv=["python", "-c", "import sys; sys.exit(3)"]))
    assert r["exit_code"] == 3
    # a genuinely slow command is killed at the timeout, not left running
    r2 = result(call(tk, "sandbox.run", name="s5", root=str(root),
                     argv=["python", "-c", "import time; time.sleep(30)"], timeout_s=2))
    assert r2["timed_out"] is True and r2["exit_code"] != 0


# ------------------------------------------------------------------ runtime
def test_create_with_runtime_makes_an_isolated_venv(sbk):
    tk, root = sbk
    r = result(call(tk, "sandbox.create", name="s6", template="python-app",
                    make_runtime=True))
    assert r["created"] and r["runtime"]["created"] is True
    assert re.fullmatch(r"\d+\.\d+", r["runtime"]["python_version"] or "")
    venv_dir = root / "s6" / ".venv"
    cfg = venv_dir / "pyvenv.cfg"
    assert cfg.is_file(), "a venv must carry pyvenv.cfg"
    assert r["runtime"]["venv"] == str(venv_dir)


def test_runtime_tool_provisions_and_python_resolves_into_venv(sbk):
    tk, root = sbk
    result(call(tk, "sandbox.create", name="s7", template="python-app"))
    r = result(call(tk, "sandbox.runtime", path=str(root / "s7")))
    assert r["runtime"]["created"] is True
    run = result(call(tk, "sandbox.run", path=str(root / "s7"),
                      argv=["python", "-c", "import sys; print(sys.prefix)"]))
    assert run["used_runtime"] is True
    prefix = run["stdout"].strip()
    assert os.path.realpath(prefix) == os.path.realpath(str(root / "s7" / ".venv")), \
        f"python must resolve into the sandbox venv, got {prefix}"


# ------------------------------------------------------------------ status/lifecycle
def test_status_deep_and_inventory_agree(sbk):
    tk, root = sbk
    result(call(tk, "sandbox.create", name="s8", template="python-lib"))
    deep = result(call(tk, "sandbox.status", path=str(root / "s8")))
    assert deep["name"] == "s8" and deep["template"] == "python-lib"
    assert deep["files"] >= 5 and deep["path"].endswith("s8")
    inv = result(call(tk, "sandbox.status", root=str(root)))
    names = {s["name"] for s in inv["sandboxes"]}
    assert "s8" in names and inv["count"] >= 1


def test_status_reports_a_missing_sandbox_not_a_crash(sbk):
    tk, root = sbk
    r = result(call(tk, "sandbox.status", path=str(root / "does_not_exist")))
    assert r["ok"] is False and r["error"]["code"] == "NOT_A_SANDBOX"


def test_teardown_via_journaled_fs_removes_from_inventory(sbk):
    tk, root = sbk
    result(call(tk, "sandbox.create", name="s9", template="generic"))
    sb = root / "s9"
    assert sb.is_dir()
    d = call(tk, "fs.delete", path=str(sb), recursive=True)
    assert d.ok, d.error
    assert d.data.get("undo_token"), "teardown must be journaled/undoable"
    assert not sb.exists()
    inv = result(call(tk, "sandbox.status", root=str(root)))
    assert all(s["name"] != "s9" for s in inv["sandboxes"]), "inventory derives from disk"


# ------------------------------------------------------------------ direct logic (no subprocess)
def test_logic_functions_are_importable_and_schemas_load():
    """sanity for the shared module and that the tool.toml declares matching arg names."""
    mod_path = os.path.join(SKILL_DIR, "scripts", "sandboxlib.py")
    spec = importlib.util.spec_from_file_location("sandboxlib_under_test", mod_path)
    sandboxlib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sandboxlib)
    assert sandboxlib.TEMPLATES
    assert sandboxlib.cmd_create({"name": "x/../y", "dry_run": True})["ok"] is False
    decls = {}
    with open(os.path.join(SKILL_DIR, "tool.toml"), "rb") as fh:
        data = tomllib.load(fh)
    for d in data["tool"]:
        if d.get("handler_script"):
            compile_tool("sandbox", SKILL_DIR, d)  # no load error means valid
            decls[d["id"]] = set(json.loads(d["input_schema"])["properties"])
    assert decls["sandbox.run"] >= {"path", "argv", "timeout_s", "use_runtime"}
    assert decls["sandbox.create"] >= {"name", "template", "make_runtime", "packages"}
