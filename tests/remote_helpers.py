"""Shared P5b remote-server fixture: a drop-in module for the *remote* child.

Imported by tests/test_remotes.py (engine level) and tests/test_mcp_stdio.py
(wire level). Not collected: no test_ prefix.
"""

from __future__ import annotations

from pathlib import Path

DEMO_MODULE = '''
from skeletonkey.core.errors import E, SkeletonKeyError
from skeletonkey.core.manifest import ToolManifest


def demo_echo(text: str) -> dict:
    return {"echoed": text, "upper": (text or "").upper()}


def demo_bad(why: str = "nope") -> None:
    raise SkeletonKeyError(E.BAD_ARGS, "the remote refuses", details={"why": why})


TOOLS = [
    ToolManifest(
        id="demo.echo", title="Echo", group="demo", capability="demo.echo",
        risk="none", idempotent=True, stateful="none", tags=["demo", "echo"],
        description="Echo text back verbatim",
        input_schema={"type": "object",
                      "properties": {"text": {"type": "string", "minLength": 1}},
                      "required": ["text"], "additionalProperties": False},
        handler=demo_echo,
    ),
    ToolManifest(
        id="demo.bad", title="Refuse", group="demo", capability="demo.bad",
        risk="write", idempotent=False, stateful="none", tags=["demo", "refuse"],
        description="Always raise a remote BAD_ARGS - the passthrough probe",
        input_schema={"type": "object",
                      "properties": {"why": {"type": "string"}},
                      "additionalProperties": False},
        handler=demo_bad,
    ),
]
'''


def write_remote_root(root: Path) -> Path:
    """A filesystem root whose `tools/demo.py` drop-in ships demo.echo/demo.bad."""
    tools = root / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    (tools / "demo.py").write_text(DEMO_MODULE, encoding="utf-8")
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    return root
