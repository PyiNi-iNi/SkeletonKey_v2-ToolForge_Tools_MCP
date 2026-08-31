"""`sk` - local CLI over the same Engine the autopilot and MCP host use.

Nothing here is a second implementation: every subcommand either calls
engine.call() or prints engine/registry internals. That is deliberate - if the
CLI can do it, the agent can do it, with identical policy and journaling.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import __version__
from .core.config import Config
from .core.util import compact_json, pretty_json


def _emit(result: Any, *, json_out: bool, raw_key: str | None = None) -> int:
    if json_out:
        print(compact_json(result.to_dict(max_bytes=None)))
    else:
        d = result.to_dict(max_bytes=None)
        if raw_key and result.ok and isinstance(d.get("data"), dict) and raw_key in d["data"]:
            sys.stdout.write(d["data"][raw_key])
        elif result.ok:
            data = d.get("data")
            if isinstance(data, dict) and "stdout" in data:
                sys.stdout.write(data["stdout"] if data["stdout"].endswith("\n") or not data["stdout"]
                                 else data["stdout"] + "\n")
                if data.get("stderr_tail"):
                    sys.stderr.write(f"[stderr] {data['stderr_tail']}\n")
            elif isinstance(data, str):
                sys.stdout.write(data if data.endswith("\n") else data + "\n")
            else:
                print(pretty_json(data))
        if not result.ok:
            err = d.get("error") or {}
            print(f"error[{err.get('code')}] {err.get('message')}", file=sys.stderr)
            if err.get("hint"):
                print(f"  hint: {err['hint']}", file=sys.stderr)
            if err.get("details"):
                print(f"  details: {compact_json(err['details'])[:600]}", file=sys.stderr)
        for h in (result.to_dict(max_bytes=None).get("hints") or []):
            if result.ok:
                print(f"  note: {h}", file=sys.stderr)
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sk", description="SkeletonKey / ToolForge toolkit")
    ap.add_argument("--root", action="append", help="filesystem root (repeatable)")
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--read-only", action="store_true")
    ap.add_argument("--json", action="store_true", help="machine output (envelope as-is)")
    ap.add_argument("--auto-approve", action="store_true", help="grant every policy ask (autopilot mode)")
    ap.add_argument("--task", default="", help="task id for journal/budget grouping")
    ap.add_argument("--version", action="version", version=f"skeletonkey {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("profile", help="show/probe host capabilities")
    p.add_argument("--force", action="store_true")
    p.add_argument("--receipts", action="store_true")

    t = sub.add_parser("tools", help="inspect the toolset")
    t.add_argument("what", nargs="?", default="list", choices=["list", "search", "describe", "stats"])
    t.add_argument("arg", nargs="?", default="")
    t.add_argument("--gated", action="store_true")

    s = sub.add_parser("shell", help="run a script")
    s.add_argument("script", nargs="?", default="")
    s.add_argument("--dialect", default=None, choices=["bash", "pwsh", "powershell", "python", "sh", "zsh"])
    s.add_argument("--file", default=None, help="read script from file ('-' for stdin)")
    s.add_argument("--cwd", dest="runcwd", default=None)
    s.add_argument("--timeout", type=float, default=120.0)
    s.add_argument("--session", default=None)
    s.add_argument("--background", action="store_true")
    s.add_argument("--env", action="append", default=[])

    j = sub.add_parser("jobs", help="list/wait/kill background jobs")
    j.add_argument("action", choices=["list", "wait", "kill"], nargs="?", default="list")
    j.add_argument("job_id", nargs="?", default="")

    f = sub.add_parser("fs", help="ls/cat/write/patch/search/glob/stat/rm/mv/mkdir")
    f.add_argument("action", choices=["ls", "cat", "write", "patch", "search", "glob", "stat",
                                      "rm", "mv", "mkdir", "undo", "undo-task", "journal"])
    f.add_argument("args", nargs="*")
    f.add_argument("--pattern", default=None)
    f.add_argument("--regex", action="store_true")
    f.add_argument("--edits-file", default=None, help="JSON file with fs.patch edits")

    sk = sub.add_parser("skills", help="list/load/match skills")
    sk.add_argument("action", choices=["list", "load", "match"], nargs="?", default="list")
    sk.add_argument("arg", nargs="?", default="")

    sub.add_parser("describe", help="full toolkit/assembly report")
    e = sub.add_parser("call", help="call any tool directly: sk call <tool> '<json args>'")
    e.add_argument("tool")
    e.add_argument("args", nargs="?", default="{}")

    m = sub.add_parser("mcp", help="run the MCP server")
    m.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http"])

    args = ap.parse_args(argv)
    if args.cmd == "mcp":
        from .mcp.__main__ import main as mcp_main

        rest = ["--transport", args.transport]
        for r in (args.root or []):
            rest += ["--root", r]
        if args.cwd:
            rest += ["--cwd", args.cwd]
        if args.read_only:
            rest.append("--read-only")
        return mcp_main(rest)

    overrides: dict[str, Any] = {}
    if args.root:
        overrides["roots"] = list(args.root)
    if args.read_only:
        overrides["policy"] = {"read_only": True}
    cfg = Config.load(cwd=args.cwd, overrides=overrides)
    from .toolkit import build

    approver = (lambda req: True) if args.auto_approve else None
    tk = build(config=cfg, approver=approver)
    eng = tk.engine
    ctx = None
    if args.task:
        from .core.engine import CallContext

        ctx = CallContext.from_config(cfg, task_id=args.task)

    def call(tool: str, a: dict[str, Any]) -> Any:
        return eng.call(tool, a, ctx=ctx)

    try:
        return _dispatch(args, tk, call, cfg)
    finally:
        tk.close()


def _dispatch(args: argparse.Namespace, tk: Any, call: Any, cfg: Config) -> int:
    cmd = args.cmd
    if cmd == "describe":
        print(pretty_json(tk.describe()))
        return 0
    if cmd == "profile":
        return _emit(call("profile.probe", {"force": bool(args.force),
                                            "include_receipts": bool(args.receipts)}), json_out=args.json)
    if cmd == "tools":
        what = args.what
        if what == "list":
            return _emit(call("registry.list", {"include_gated": args.gated}), json_out=args.json)
        if what == "stats":
            return _emit(call("registry.stats", {}), json_out=args.json)
        if what == "describe":
            if not args.arg:
                print("usage: sk tools describe <tool.id>", file=sys.stderr)
                return 2
            return _emit(call("registry.describe", {"tool": args.arg}), json_out=args.json)
        return _emit(call("registry.search", {"query": args.arg or "", "include_gated": args.gated,
                                             "limit": 15}), json_out=args.json)
    if cmd == "call":
        try:
            payload = json.loads(args.args) if args.args.strip() else {}
        except ValueError as exc:
            print(f"invalid json args: {exc}", file=sys.stderr)
            return 2
        return _emit(call(args.tool, payload), json_out=True)
    if cmd == "skills":
        if args.action == "list":
            return _emit(call("skills.list", {}), json_out=args.json)
        if args.action == "match":
            return _emit(call("skills.match", {"task": args.arg or ""}), json_out=args.json)
        return _emit(call("skills.load", {"name": args.arg or ""}), json_out=args.json, raw_key="injection")
    if cmd == "jobs":
        if args.action == "list":
            return _emit(call("shell.jobs", {}), json_out=args.json)
        if args.action == "kill":
            return _emit(call("shell.job_kill", {"job_id": args.job_id}), json_out=args.json)
        return _emit(call("shell.job_wait", {"job_id": args.job_id}), json_out=args.json)
    if cmd == "shell":
        script = args.script
        if args.file:
            if args.file == "-":
                script = sys.stdin.read()
            else:
                with open(args.file, encoding="utf-8") as fh:
                    script = fh.read()
        if not script:
            print("nothing to run: pass a script or --file", file=sys.stderr)
            return 2
        a: dict[str, Any] = {"script": script, "timeout_s": args.timeout, "background": args.background}
        if args.dialect:
            a["dialect"] = args.dialect
        if args.runcwd:
            a["cwd"] = args.runcwd
        if args.session:
            a["session"] = args.session
        if args.env:
            env = {}
            for pair in args.env:
                k, _, v = pair.partition("=")
                env[k] = v or None
            a["env"] = env
        return _emit(call("shell.run", a), json_out=args.json)
    # ---- fs
    act, rest = args.action, list(args.args)
    if act == "ls":
        return _emit(call("fs.list", {"path": rest[0] if rest else "."}), json_out=args.json)
    if act == "cat":
        return _emit(call("fs.read", {"path": rest[0]}), json_out=args.json, raw_key="content")
    if act == "write":
        body = sys.stdin.read()
        return _emit(call("fs.write", {"path": rest[0], "content": body}), json_out=args.json)
    if act == "patch":
        edits = []
        if args.edits_file:
            with open(args.edits_file, encoding="utf-8") as fh:
                edits = json.load(fh)
        if not edits:
            print("pass --edits-file with a JSON array of {old_text,new_text}", file=sys.stderr)
            return 2
        return _emit(call("fs.patch", {"path": rest[0], "edits": edits}), json_out=args.json)
    if act == "search":
        return _emit(call("fs.search", {"pattern": args.pattern or (rest[0] if rest else ""),
                                       "path": rest[1] if len(rest) > 1 else ".", "regex": args.regex}),
                     json_out=args.json)
    if act == "glob":
        return _emit(call("fs.glob", {"pattern": rest[0]}), json_out=args.json)
    if act == "stat":
        return _emit(call("fs.stat", {"path": rest[0]}), json_out=args.json)
    if act == "rm":
        return _emit(call("fs.delete", {"path": rest[0], "recursive": "-r" in rest or "-rf" in rest}),
                     json_out=args.json)
    if act == "mv":
        return _emit(call("fs.move", {"src": rest[0], "dst": rest[1]}), json_out=args.json)
    if act == "mkdir":
        return _emit(call("fs.mkdir", {"path": rest[0]}), json_out=args.json)
    if act == "undo":
        return _emit(call("fs.undo", {"token": rest[0]}), json_out=args.json)
    if act == "undo-task":
        return _emit(call("fs.undo_task", {"task_id": rest[0]}), json_out=args.json)
    return _emit(call("fs.journal_list", {}), json_out=args.json)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
