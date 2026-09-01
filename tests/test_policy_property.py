"""Property: a wall means zero writes, for every mutating tool.

For each tool the registry itself declares mutating, a scripted burst attempts
a write under two independent walls - `policy.read_only` and a deny rule that
matches everything. The assertion is at the filesystem level, not the error
level: a snapshot of the workspace (every file's content hash and mode, every
directory) is diffed before and after the burst and must show no change at
all. Error codes can evolve; the disk cannot lie.

A secondary assertion keeps the burst honest: every attempt must be refused
*by the wall itself* (READ_ONLY_MODE / POLICY_BLOCKED / DENY_RULE), not by a
coincidental BAD_ARGS or missing argument that would make the attempt
vacuous. And the burst table must cover exactly the mutating tools, so a
new mutating tool cannot ship without joining the property.
"""

from __future__ import annotations

import hashlib
import os
import stat

from skeletonkey.core.config import Config
from skeletonkey.toolkit import build

# One write attempt per mutating tool. Arguments are shaped so that, absent
# the wall, the call would do its real thing to a file in the workspace.
BURST: dict[str, dict] = {
    "fs.write": {"path": "burst/a.txt", "content": "pwned\n"},
    "fs.patch": {"path": "burst/a.txt", "edits": [{"old_text": "base", "new_text": "pwned"}]},
    "fs.delete": {"path": "burst/a.txt", "recursive": True},
    "fs.move": {"src": "burst/a.txt", "dst": "burst/moved.txt"},
    "fs.mkdir": {"path": "burst/newdir"},
    "fs.chmod": {"path": "burst/a.txt", "mode": "0o777"},
    "fs.undo": {"token": "und_bogus000000"},
    "fs.redo": {},
    "fs.undo_task": {"task_id": "burst"},
    "shell.run": {"script": "echo hi"},
    "shell.job_kill": {"job_id": "no-such-job"},
    "shell.session_reset": {"all": True},
    "skills.install": {"dir": "burst/no-such-pack"},
    "skills.uninstall": {"name": "no-such-skill"},
    "policy.grant": {"tool": "fs.delete", "scope": "task"},
    "pub.store_put": {"id": "burst.x", "kind": "token", "value": "burst"},
    "pub.store_delete": {"id": "burst.x"},
    "pub.inject": {"path": "burst"},
    # live.* mutators: the walls must refuse them before any python executes.
    # (Risk "write" like shell.run; the disk assertion is the real proof.)
    "live.start": {"path": "burst/a.txt"},
    "live.stop": {"program": "ghost"},
    "live.reload": {"program": "ghost"},
    "live.patch": {"name": "run", "code": "def run():\n    pass\n"},
    "live.repl": {"code": "open('burst/pwned.txt', 'w').write('x')"},
    "live.snapshot": {"op": "list"},
    "live.scene": {"op": "clear"},
    "live.serve": {"port": 0},
}

WALL_CODES = {
    "read_only": {"READ_ONLY_MODE", "POLICY_BLOCKED"},
    "deny_rule": {"DENY_RULE"},
}


def _snapshot(root: str) -> dict[str, tuple[str, str]]:
    """relpath -> (content-hash-or-kind, mode). The whole tree, no exceptions."""
    out: dict[str, tuple[str, str]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted(dirnames) + sorted(filenames):
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, root)
            st = os.lstat(p)
            if stat.S_ISREG(st.st_mode):
                with open(p, "rb") as fh:
                    out[rel] = (hashlib.sha256(fh.read()).hexdigest(), oct(stat.S_IMODE(st.st_mode)))
            else:
                out[rel] = ("dir", "")
    return out


def _burst(workspace, state_dir: str, wall: str) -> None:
    (workspace / "burst").mkdir(exist_ok=True)
    (workspace / "burst" / "a.txt").write_text("base\n", encoding="utf-8")
    before = _snapshot(str(workspace))

    policy: dict = {"log_level": "ERROR"}
    if wall == "read_only":
        policy["read_only"] = True
        # everything else permissive: the read-only wall alone must hold the line
        policy.update({"auto_approve": ["none", "read", "write", "destructive"],
                       "confirm_destructive": False})
    else:
        policy.update({"deny": ["**"],
                       "auto_approve": ["none", "read", "write", "destructive"],
                       "confirm_destructive": False})

    cfg = Config.load(cwd=str(workspace), overrides={
        "roots": [str(workspace)],
        "state": {"dir": os.path.join(state_dir, "state")},
        "shell": {"tempdir": os.path.join(state_dir, "shell")},
        "policy": policy,
    })
    tk = build(config=cfg)
    try:
        for tool_id, args in BURST.items():
            res = tk.engine.call(tool_id, dict(args))
            assert not res.ok, f"{tool_id} SUCCEEDED under the {wall} wall"
            assert res.error.code in WALL_CODES[wall], \
                f"{tool_id} was refused with {res.error.code}, not by the {wall} wall " \
                f"(the burst for that tool is vacuous)"
    finally:
        tk.close()

    after = _snapshot(str(workspace))
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(p for p in set(before) & set(after) if before[p] != after[p])
    assert not (added or removed or changed), \
        f"the {wall} wall allowed a write: added={added} removed={removed} changed={changed}"


def test_burst_covers_exactly_the_mutating_tools(workspace):
    """A mutating tool that is not in BURST would sail through the property."""
    cfg = Config.load(cwd=str(workspace), overrides={"roots": [str(workspace)],
                                                     "state": {"dir": str(workspace / ".sk-meta")},
                                                     "log_level": "ERROR"})
    tk = build(config=cfg)
    try:
        mutating = {m.id for m in tk.engine.registry.all() if m.is_mutating}
    finally:
        tk.close()
    assert set(BURST) == mutating, (
        f"burst table out of sync: missing={sorted(mutating - set(BURST))} "
        f"stale={sorted(set(BURST) - mutating)}")


def test_read_only_wall_means_zero_writes(workspace, tmp_path):
    _burst(workspace, str(tmp_path), "read_only")


def test_deny_rule_wall_means_zero_writes(workspace, tmp_path):
    _burst(workspace, str(tmp_path), "deny_rule")
