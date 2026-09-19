"""The optional-dependency contract (V10).

ADR-0001 says the core has no mandatory dependencies. The corollary is a test
discipline: anything needing an optional extra must SKIP without it, never
fail. This file guards that discipline, because the failure mode is insidious
- a suite that is always a bit red stops being read at all, and this repo
carried a bogus 27-failure "baseline" for exactly that reason.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from _extras import HAS_MCP, NO_MCP_REASON

REPO = Path(__file__).resolve().parents[1]
TESTS = REPO / "tests"

#: Test modules that drive a real MCP server and therefore need the extra.
MCP_DEPENDENT = ("test_mcp_stdio.py", "test_mcp_http.py", "test_remotes.py")


def test_the_probe_agrees_with_reality():
    """HAS_MCP must reflect an actual import, not just a spec lookup."""
    try:
        import mcp  # noqa: F401
        really = True
    except Exception:
        really = False
    assert HAS_MCP is really


def test_the_reason_tells_you_how_to_fix_it():
    assert "pip install" in NO_MCP_REASON and "mcp" in NO_MCP_REASON


def test_mcp_dependent_modules_are_guarded():
    """Each module that needs a live server must carry a module-level skip.

    Parsed rather than imported: importing them would need the extra, which is
    precisely the situation this is protecting.
    """
    for name in MCP_DEPENDENT:
        src = (TESTS / name).read_text(encoding="utf-8")
        tree = ast.parse(src)
        has_mark = any(
            isinstance(node, ast.Assign)
            and any(getattr(t, "id", "") == "pytestmark" for t in node.targets)
            for node in tree.body
        )
        assert has_mark, f"{name} needs `pytestmark = pytest.mark.skipif(not HAS_MCP, ...)`"
        assert "HAS_MCP" in src, f"{name} does not consult HAS_MCP"


def test_no_test_imports_mcp_at_module_scope_unguarded():
    """A bare top-level `import mcp` makes collection explode instead of skip,
    which turns one absent extra into a suite-wide failure."""
    for path in sorted(TESTS.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:                       # module scope only
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for n in names:
                if n == "mcp" or n.startswith("mcp."):
                    assert path.name in MCP_DEPENDENT, (
                        f"{path.name} imports {n} at module scope; either guard the "
                        f"module or move the import inside the test")


def test_the_core_installs_without_optional_dependencies():
    """ADR-0001, checked rather than asserted: importing the toolkit and
    building a registry must not require any third-party package."""
    script = """
import sys

BLOCKED = {"mcp", "watchfiles"}


class Hide:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError("blocked: " + name)
        return None


sys.meta_path.insert(0, Hide())

import skeletonkey.toolkit as t

tk = t.build(overrides={"cwd": "."})
print(len(tk.engine.registry.all()))
"""
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True, cwd=str(REPO), timeout=180)
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert int(proc.stdout.strip()) > 40
