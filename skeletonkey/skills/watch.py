"""Optional hot reload of the skills directories (PLAN P2, `tools.hot_reload`).

A skill pack is a directory of text files, so "reload the toolkit" reduces to "re-read that
directory" - which is what an agent editing its own skill with `fs.write` needs, and what makes
`skills.install` useful without a restart.

`watchfiles` is an **extra**, and that shapes this module: `sk mcp` must start on a locked-down
box where no wheel installs, so a missing dependency is a reported state here, never an
ImportError at import time. Nothing in this file is imported unless the operator turns the flag
on.
"""

from __future__ import annotations

import importlib.util
import os
from typing import Any

INSTALL_HINT = "pip install 'skeletonkey-toolforge[watch]'"


def available() -> bool:
    return importlib.util.find_spec("watchfiles") is not None


def status(toolkit: Any, *, dirs: list[str] | None = None) -> dict[str, Any]:
    """Whether a watch loop could run here, and over which directories."""
    wanted = dirs if dirs is not None else list(getattr(toolkit.config.skills, "dirs", []) or [])
    expanded = [os.path.abspath(os.path.expanduser(str(x))) for x in wanted]
    live = [d for d in expanded if os.path.isdir(d)]
    out: dict[str, Any] = {
        "requested": bool(getattr(toolkit.config.tools, "hot_reload", False)),
        "possible": available(),
        "dirs": live,
        "requested_dirs": [str(x) for x in wanted],
    }
    if not out["possible"]:
        out["reason"] = "watchfiles is not installed"
        out["install"] = INSTALL_HINT
    elif not live:
        out["reason"] = "none of the configured skills directories exist yet"
    return out


async def watch_skills(toolkit: Any, bridge: Any = None, *, dirs: list[str] | None = None,
                       stop_after: int | None = None) -> dict[str, Any]:
    """Re-sync the skill-authored tools whenever a skills directory changes.

    Only the delta is announced: a save that touches nothing relevant costs one directory walk,
    and a client that gets `tools/list_changed` after every keystroke learns to ignore it.
    `stop_after` exists for tests and for a one-shot run.
    """
    report = status(toolkit, dirs=dirs)
    if not report["possible"] or not report["dirs"]:
        return {"watching": False, **report}

    from watchfiles import awatch

    reloads = 0
    last: dict[str, Any] = {}
    async for _changes in awatch(*report["dirs"], recursive=True):
        last = toolkit.sync_skills()
        reloads += 1
        if bridge is not None and (last.get("added") or last.get("removed")):
            from ..mcp.adapter import notify_tools_changed

            try:
                await notify_tools_changed(bridge, getattr(bridge, "session", None))
            except Exception:                                     # pragma: no cover - best effort
                pass
        if stop_after is not None and reloads >= stop_after:
            break
    return {"watching": False, "reloads": reloads, "last": last, **report}
