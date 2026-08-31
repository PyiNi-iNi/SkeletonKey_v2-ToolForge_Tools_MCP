"""`sk` - local CLI over the same Engine the autopilot and MCP host use.

Nothing here is a second implementation: every subcommand either calls
engine.call() or prints engine/registry internals. That is deliberate - if the
CLI can do it, the agent can do it, with identical policy and journaling.
"""

from __future__ import annotations

import argparse
import json
import os
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

    j = sub.add_parser("jobs", help="list/wait/watch/kill background jobs")
    j.add_argument("action", choices=["list", "wait", "watch", "kill"], nargs="?", default="list")
    j.add_argument("job_id", nargs="?", default="")
    j.add_argument("--until", default="", help="regex to wait for on the job's stdout (watch)")
    j.add_argument("--timeout-s", type=float, default=30.0, help="cap for wait/watch")

    f = sub.add_parser("fs", help="ls/cat/write/patch/search/glob/stat/rm/mv/mkdir/chmod")
    f.add_argument("action", choices=["ls", "cat", "write", "patch", "search", "glob", "stat",
                                      "rm", "mv", "mkdir", "chmod", "undo", "undo-task", "journal"])
    f.add_argument("args", nargs="*")
    f.add_argument("--pattern", default=None)
    f.add_argument("--regex", action="store_true")
    f.add_argument("--edits-file", default=None, help="JSON file with fs.patch edits")
    f.add_argument("-R", "--recursive", action="store_true",
                   help="with chmod: walk a directory (symlinks are never followed)")

    sk = sub.add_parser("skills", help="list/load/match/install/uninstall skill packs")
    sk.add_argument("action", choices=["list", "load", "match", "install", "uninstall"],
                    nargs="?", default="list")
    sk.add_argument("arg", nargs="?", default="",
                    help="skill name (load, uninstall) or a skill directory (install)")
    sk.add_argument("--dry-run", action="store_true",
                    help="with install/uninstall: validate and report the plan, change nothing")
    sk.add_argument("--name", default=None, help="with install: install under this skill name")
    sk.add_argument("--keep-files", action="store_true",
                    help="with uninstall: unregister the tools but leave the directory")

    pb = sub.add_parser("pub", help="publishing: credential store, placeholder injection, platform knowledge")
    pb.add_argument("action", choices=["list", "put", "delete", "placeholders", "inject",
                                       "platforms", "payments", "packaging", "testers"])
    pb.add_argument("arg", nargs="?", default="",
                    help="id (put/delete), kind (list), name (platforms/payments/packaging) or path (placeholders/inject)")
    pb.add_argument("--kind", default="token",
                    help="with put: credential kind (token, api_key, oauth_token, password, ...)")
    pb.add_argument("--value", default="", help="with put: the secret itself (never echoed back)")
    pb.add_argument("--note", default="", help="with put: human note (what it is, where it came from)")
    pb.add_argument("--dry-run", action="store_true", help="with inject: report the plan, change nothing")
    pb.add_argument("--platform", default="", help="with testers: platform key (from pub.platforms)")
    pb.add_argument("--packaging", default="", help="with testers: packaging key (from pub.packaging)")
    pb.add_argument("--version", default="", help="with testers: version label for the plan")

    sub.add_parser("describe", help="full toolkit/assembly report")
    e = sub.add_parser("call", help="call any tool directly: sk call <tool> '<json args>'")
    e.add_argument("tool")
    e.add_argument("args", nargs="?", default="{}")

    m = sub.add_parser("mcp", help="run the MCP server")
    m.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http"])

    rp = sub.add_parser("replay", help="re-execute a recorded run in a scratch copy and diff envelopes")
    rp.add_argument("ref", help="path to a run recording, or a task id under <state>/runs/")
    ev = sub.add_parser("eval", help="score a suite of scripted tasks")
    ev.add_argument("--suite", action="append", required=True,
                    help="suite file, repeatable (one task per JSON line)")

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
    if cmd == "replay":
        from .core import replay as replay_mod

        ref = args.ref
        path = ref if os.path.isfile(ref) else os.path.join(cfg.state.dir, "runs", ref + ".jsonl")
        if not os.path.isfile(path):
            print(f"no recording at {path}", file=sys.stderr)
            return 2
        report = replay_mod.replay(path)
        if args.json:
            print(compact_json(report))
        else:
            for row in report["steps"]:
                mark = "ok  " if row["match"] else "DIFF"
                flag = " [stateful]" if row["stateful"] else ""
                print(f"  {mark} {row['seq']:>2}  {row['tool']}{flag}")
                for d in row["diffs"]:
                    print(f"        {d}")
            if "error" in report:
                print(f"error: {report['error']}", file=sys.stderr)
            print(f"  {report.get('calls', 0)} calls, "
                  f"{report.get('ledger_rows', 0)} ledger rows "
                  f"({'one per call' if report.get('ledger_one_row_per_call') else 'MISMATCH'}), "
                  f"verdict: {'REPRODUCED' if report['ok'] else 'DIVERGED'}")
            print(f"  scratch copy kept at {report.get('scratch', '')}")
        return 0 if report["ok"] else 1
    if cmd == "eval":
        from .core import replay as replay_mod

        report = replay_mod.eval_suite(args.suite)
        if args.json:
            print(compact_json(report))
        else:
            for row in report["results"]:
                mark = "ok  " if row["ok"] else "FAIL"
                extra = f" (refused then recovered: {', '.join(row['refused'])})" if row["recovered"] else ""
                print(f"  {mark} {row['id']}: {row['calls']} calls, {row['tokens']} tokens{extra}")
                for f in row["fail"]:
                    print(f"        failed assertion: {f}")
            print(f"  {report['passed']}/{report['tasks']} tasks passed, "
                  f"median {report['median_calls_per_task']} calls/task, "
                  f"mean {report['mean_tokens_per_task']} tokens/task, "
                  f"{report['refusal_then_recovery']} refusal-then-recovery of {report['refusals']} refusals")
        return 0 if report["passed"] == report["tasks"] else 1
    if cmd == "skills":
        if args.action == "list":
            return _emit(call("skills.list", {}), json_out=args.json)
        if args.action == "match":
            return _emit(call("skills.match", {"task": args.arg or ""}), json_out=args.json)
        if args.action == "install":
            if not args.arg:
                print("sk skills install needs a directory: sk skills install ./my-skill --dry-run",
                      file=sys.stderr)
                return 2
            payload: dict[str, Any] = {"dir": args.arg, "dry_run": args.dry_run}
            if args.name:
                payload["name"] = args.name
            return _emit(call("skills.install", payload), json_out=args.json)
        if args.action == "uninstall":
            if not args.arg:
                print("sk skills uninstall needs a skill name", file=sys.stderr)
                return 2
            return _emit(call("skills.uninstall", {"name": args.arg, "dry_run": args.dry_run,
                                                   "remove_files": not args.keep_files}),
                         json_out=args.json)
        return _emit(call("skills.load", {"name": args.arg or ""}), json_out=args.json, raw_key="injection")
    if cmd == "pub":
        a, arg = args.action, (args.arg or "")
        if a == "list":
            return _emit(call("pub.store_list", {"kind": arg} if arg else {}), json_out=args.json)
        if a == "put":
            if not arg or not args.value:
                print("usage: sk pub put <id> --kind <kind> --value <secret> [--note ...]", file=sys.stderr)
                return 2
            return _emit(call("pub.store_put", {"id": arg, "kind": args.kind, "value": args.value,
                                                "note": args.note}), json_out=args.json)
        if a == "delete":
            if not arg:
                print("usage: sk pub delete <id>", file=sys.stderr)
                return 2
            return _emit(call("pub.store_delete", {"id": arg}), json_out=args.json)
        if a == "placeholders":
            return _emit(call("pub.placeholders", {"path": arg or "."}), json_out=args.json)
        if a == "inject":
            return _emit(call("pub.inject", {"path": arg or ".", "dry_run": bool(args.dry_run)}),
                         json_out=args.json)
        if a == "platforms":
            return _emit(call("pub.platforms", {"name": arg} if arg else {}), json_out=args.json)
        if a == "payments":
            return _emit(call("pub.payments", {"provider": arg} if arg else {}), json_out=args.json)
        if a == "packaging":
            return _emit(call("pub.packaging", {"target": arg} if arg else {}), json_out=args.json)
        if a == "testers":
            payload: dict[str, Any] = {}
            if args.platform:
                payload["platform"] = args.platform
            if args.packaging:
                payload["packaging"] = args.packaging
            if args.version:
                payload["version"] = args.version
            return _emit(call("pub.testers", payload), json_out=args.json)
        return 2
    if cmd == "jobs":
        if args.action == "list":
            return _emit(call("shell.jobs", {}), json_out=args.json)
        if args.action == "kill":
            return _emit(call("shell.job_kill", {"job_id": args.job_id}), json_out=args.json)
        if args.action == "watch":
            return _emit(call("shell.job_watch", {"job_id": args.job_id, "until": args.until,
                                                  "timeout_s": args.timeout_s}), json_out=args.json)
        return _emit(call("shell.job_wait", {"job_id": args.job_id,
                                             "timeout_s": args.timeout_s}), json_out=args.json)
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
    if act == "chmod":
        # a real flag, not a token in the positional list: a stray "-R" silently treated as
        # a path name would be the one thing this tool refuses to do on its own
        return _emit(call("fs.chmod", {"path": rest[0], "mode": rest[1],
                                       "recursive": bool(getattr(args, "recursive", False))}),
                     json_out=args.json)
    if act == "undo":
        return _emit(call("fs.undo", {"token": rest[0]}), json_out=args.json)
    if act == "undo-task":
        return _emit(call("fs.undo_task", {"task_id": rest[0]}), json_out=args.json)
    return _emit(call("fs.journal_list", {}), json_out=args.json)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
