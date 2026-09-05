"""ADR-0001, enforced instead of asserted: the core runs with site-packages unavailable.

"The core has zero mandatory dependencies" is only a promise if something checks it in
an environment where the deps *cannot* be reached. This test runs a subprocess with
`-S` (no site-packages, no user site) and PYTHONPATH pointed at the repo only, imports
the stdlib-only surface, then fails if any third-party module is either already loaded
(a sneaky top-level import) or importable at all.

Also enforced one level up: the operator-facing instant-onboarding commands (`sk wire`,
`sk doctor --no-probe`) complete in that same stripped interpreter, which is what makes
"drop it in and wire it up" true on a locked-down box before any extra is installed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FORBIDDEN = ["mcp", "mcp_types", "pydantic", "watchfiles", "jsonschema", "yaml",
             "uvicorn", "starlette", "httpx", "anyio"]

CORE_SCRIPT = """
import sys
import skeletonkey                                  # the public surface (core only)
import skeletonkey.fsx.ops
import skeletonkey.shells.dialect
import skeletonkey.skills.loader

loaded = [m for m in %(forbidden)r if m in sys.modules]
assert not loaded, f"core import pulled in third-party modules: {loaded}"
for m in %(forbidden)r:
    try:
        __import__(m)
    except ImportError:
        continue
    raise AssertionError(f"{m!r} is importable with site-packages hidden")
print("CORE_OK")
"""


def _stripped(argv: list[str], *, cwd: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=REPO, PYTHONNOUSERSITE="1")
    env.pop("PYTHONSTARTUP", None)
    return subprocess.run([sys.executable, "-S", *argv], capture_output=True, text=True,
                          env=env, cwd=cwd, timeout=120)


def test_core_imports_with_site_packages_hidden(tmp_path):
    p = _stripped(["-c", CORE_SCRIPT % {"forbidden": FORBIDDEN}], cwd=str(tmp_path))
    assert p.returncode == 0, f"stdout={p.stdout}\nstderr={p.stderr[-2000:]}"
    assert "CORE_OK" in p.stdout


def test_onboarding_commands_run_without_any_extra(tmp_path):
    # `sk wire --check` and `sk doctor --no-probe` are the two commands a locked-down
    # box needs first; neither may require the [mcp] extra (or any third-party module)
    p = _stripped(["-m", "skeletonkey.cli", "--json", "wire", "--check"], cwd=str(tmp_path))
    assert p.returncode == 0, p.stderr[-2000:]
    rep = json.loads(p.stdout)
    assert rep["schema"] == "sk.wire/1" and rep["hosts"]

    p2 = _stripped(["-m", "skeletonkey.cli", "--json", "--cwd", str(tmp_path),
                    "doctor", "--no-probe"], cwd=str(tmp_path))
    assert p2.returncode == 0, p2.stderr[-2000:]
    rep2 = json.loads(p2.stdout)
    assert rep2["schema"] == "sk.doctor/1"
    live = next(c for c in rep2["checks"] if c["id"] == "mcp.stdio")
    assert live["skipped"] and live["reason"] == "--no-probe"
