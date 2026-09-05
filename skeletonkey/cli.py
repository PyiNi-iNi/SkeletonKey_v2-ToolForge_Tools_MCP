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


def _host_alias(name: str) -> str:
    """Accept the names people actually type: an id, a lowercase title, or a prefix."""
    from . import wire as wire_mod

    n = name.strip().lower().replace(" ", "-")
    if n in wire_mod.HOSTS_BY_ID:
        return n
    for spec in wire_mod.hosts():
        if spec.title.lower().replace(" ", "-").replace("/", "-").startswith(n) or \
           n in (spec.id, spec.title.lower()):
            return spec.id
    return n  # unknown; wire.wire reports it


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
    t.add_argument("what", nargs="?", default="list",
                   choices=["list", "search", "route", "expand", "describe", "stats"])
    t.add_argument("arg", nargs="?", default="")
    t.add_argument("--gated", action="store_true")
    t.add_argument("--tier", default=None, choices=["core", "task", "full"],
                   help="with list: query this tier without switching to it")
    t.add_argument("--k", type=int, default=8, help="with route: top-k results")
    t.add_argument("--semantic", action="store_true",
                   help="with route: use a registered semantic backend if one is installed")

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

    lv = sub.add_parser("live", help="Python HMR: live.start/repl/patch/reload + preview panel")
    lv.add_argument("action", choices=["start", "stop", "status", "reload", "patch", "repl",
                                       "state", "snapshot", "restore", "render", "scene",
                                       "serve", "demo"])
    lv.add_argument("arg", nargs="?", default="",
                    help="path (start/demo target dir), program id (stop), code (repl/patch), "
                         "snapshot name, or a JSON object (scene)")
    lv.add_argument("--program", default=None)
    lv.add_argument("--pid", dest="program", help=argparse.SUPPRESS)   # alias
    lv.add_argument("--name", default=None, help="patch: the def/class name the code defines")
    lv.add_argument("--file", default=None, help="patch: read code from file ('-' for stdin)")
    lv.add_argument("--mode", default="auto", choices=["auto", "eval", "exec"])
    lv.add_argument("--no-watch", action="store_true")
    lv.add_argument("--no-render", action="store_true")
    lv.add_argument("--keys", default=None, help="state: comma-separated names")
    lv.add_argument("--force-source", default=None,
                    help="reload: comma-separated names that take the file's value")
    lv.add_argument("--svg", action="store_true", help="render: print the SVG frame")
    lv.add_argument("--host", default=None, help="serve/demo: bind host (0.0.0.0 for sandboxes)")
    lv.add_argument("--port", type=int, default=None, help="serve/demo: port (0 = ephemeral)")
    lv.add_argument("--via-panel", action="store_true",
                    help="drive the already-running panel process over HTTP instead of this "
                         "short-lived process (live state is per-process; the panel owns it)")
    lv.add_argument("--wait", action="store_true",
                    help="serve/demo: keep this process (and the panel+watcher) alive")

    sub.add_parser("describe", help="full toolkit/assembly report")
    e = sub.add_parser("call", help="call any tool directly: sk call <tool> '<json args>'")
    e.add_argument("tool")
    e.add_argument("args", nargs="?", default="{}")

    m = sub.add_parser("mcp", help="run the MCP server")
    m.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http"])

    w = sub.add_parser("wire",
                       help="auto-wire the MCP server into host apps (claude-desktop, cursor, ...)")
    w.add_argument("hosts", nargs="*", help="host ids (default: every detected host)")
    w.add_argument("--project", action="store_true",
                   help="write the project-scope config in --cwd (e.g. .mcp.json) instead of the user's")
    w.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http", "http"],
                   help="stdio writes a command stanza; streamable-http writes a url stanza")
    w.add_argument("--port", type=int, default=None,
                   help="with --transport streamable-http (default: mcp.port, 8765)")
    w.add_argument("--bind", default="127.0.0.1", help="host part of the url stanza")
    w.add_argument("--url", default=None, help="full url override for streamable-http")
    w.add_argument("--python", default=None, help="interpreter for the command stanza")
    w.add_argument("--root", action="append", help="pin a filesystem root (repeatable)")
    w.add_argument("--read-only", action="store_true", help="wire the read-only surface")
    w.add_argument("--name", default="skeletonkey", help="entry name in the host's server map")
    w.add_argument("--remove", action="store_true", help="remove our entries (never a hand-written one)")
    w.add_argument("--check", action="store_true", help="report what would change; write nothing")
    w.add_argument("--dry-run", action="store_true", help="same plan as a real run, writes nothing")
    w.add_argument("--allow-jsonc", action="store_true",
                   help="permit rewriting a comments-bearing (JSONC) config as plain JSON")

    rp = sub.add_parser("replay", help="re-execute a recorded run in a scratch copy and diff envelopes")
    rp.add_argument("ref", help="path to a run recording, or a task id under <state>/runs/")
    ev = sub.add_parser("eval", help="score a suite of scripted tasks")
    ev.add_argument("--suite", action="append", required=True,
                    help="suite file, repeatable (one task per JSON line)")

    args = ap.parse_args(argv)
    if args.cmd == "mcp":
        from .mcp.__main__ import main as mcp_main

        rest = ["--transport", "streamable-http" if args.transport == "http" else args.transport]
        for r in (args.root or []):
            rest += ["--root", r]
        if args.cwd:
            rest += ["--cwd", args.cwd]
        if args.read_only:
            rest.append("--read-only")
        return mcp_main(rest)

    if args.cmd == "wire":
        # Deliberately before any Toolkit build: wiring is an operator action over host
        # config files, stdlib-only, instant - even before the [mcp] extra is installed.
        from . import wire as wire_mod

        if args.port is not None:
            port = args.port
        else:
            try:
                port = Config.load(cwd=args.cwd).mcp.port
            except Exception:
                port = 8765
        report = wire_mod.wire(
            host_ids=[_host_alias(h) for h in args.hosts] or None,
            scope="project" if args.project else "user", cwd=args.cwd,
            transport="http" if args.transport in ("http", "streamable-http") else "stdio",
            port=port, bind=args.bind, python=args.python,
            roots=(list(args.root) if args.root else None), read_only=args.read_only,
            name=args.name,
            url=args.url, remove=args.remove, check_only=args.check, dry_run=args.dry_run,
            allow_jsonc=args.allow_jsonc)
        if args.json:
            print(compact_json(report))
        else:
            for r in report["hosts"]:
                mark = {"wired": "wired  ", "updated": "updated", "already": "already",
                        "removed": "removed", "dry-run": "plan   ", "checked": "plan   ",
                        "skipped": "skip   ", "needs-manual": "MANUAL ", "error": "ERROR  ",
                        }.get(r.get("status"), r.get("status", "?") + " " * 7)
                line = f"  {mark} {r['host']:<15} {r.get('path', '')}"
                extra = r.get("reason") or r.get("error") or ""
                if extra:
                    line += f"  - {extra}"
                print(line)
                if r.get("status") == "needs-manual" and r.get("stanza"):
                    print("    paste:", pretty_json(r["stanza"]))
            n = sum(1 for r in report["hosts"] if r.get("status") in ("wired", "updated", "removed"))
            print(f"  {n} host(s) changed; "
                  f"{'restart the host to pick up the new tools' if n else 'nothing written'}")
        return 0 if report["ok"] else 1

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
    if cmd == "live":
        return _live(args, tk, call, cfg)
    if cmd == "profile":
        return _emit(call("profile.probe", {"force": bool(args.force),
                                            "include_receipts": bool(args.receipts)}), json_out=args.json)
    if cmd == "tools":
        what = args.what
        if what == "list":
            payload: dict[str, Any] = {"include_gated": args.gated,
                                       "limit": 400 if args.tier else 100,
                                       "tier": args.tier}
            return _emit(call("registry.list", payload), json_out=args.json)
        if what == "route":
            if not args.arg:
                print("usage: sk tools route '<task text>' [--k 8] [--semantic] [--gated]",
                      file=sys.stderr)
                return 2
            return _emit(call("registry.route", {"task": args.arg, "k": args.k,
                                                 "semantic": args.semantic,
                                                 "include_gated": args.gated}), json_out=args.json)
        if what == "expand":
            if args.arg not in ("core", "task", "full"):
                print("usage: sk tools expand core|task|full", file=sys.stderr)
                return 2
            return _emit(call("registry.expand", {"tier": args.arg}), json_out=args.json)
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


def _live(args: argparse.Namespace, tk: Any, call: Any, cfg: Config) -> int:
    """`sk live` - one CLI surface over the live.* tools (nothing the engine
    cannot do; the CLI just picks the envelopes).

    Live state is per-process: `sk live start` then `sk live repl` as separate
    invocations are two processes. For one-shot control of a persistent live
    session, use `--via-panel` (talks to a served panel's HTTP controls), or
    keep a host alive (`sk live demo`, `sk mcp`)."""
    act = args.action
    pid = args.program
    if args.via_panel:
        return _live_via_panel(args, cfg)

    if act == "demo":
        return _live_demo(args, tk, call, cfg)
    if act == "status":
        return _emit(call("live.status", {"history": True}), json_out=args.json)
    if act == "start":
        if not args.arg:
            print("usage: sk live start <path> [--no-watch]", file=sys.stderr)
            return 2
        a = {"path": args.arg, "watch": not args.no_watch,
             "auto_render": not args.no_render}
        if pid:
            a["program"] = pid
        res = call("live.start", a)
        _maybe_serve_hint(res, args, call)
        return _emit(res, json_out=args.json)
    if act == "stop":
        return _emit(call("live.stop", ({"program": pid or args.arg} if (pid or args.arg) else {})),
                     json_out=args.json)
    if act == "reload":
        a: dict[str, Any] = {}
        if pid:
            a["program"] = pid
        if args.force_source:
            a["force_source"] = [s.strip() for s in args.force_source.split(",") if s.strip()]
        return _emit(call("live.reload", a), json_out=args.json)
    if act == "patch":
        code = args.arg or ""
        if args.file:
            if args.file == "-":
                code = sys.stdin.read()
            else:
                with open(args.file, encoding="utf-8") as fh:
                    code = fh.read()
        if not (args.name and code):
            print("usage: sk live patch <code> --name render   (or --file patch.py)", file=sys.stderr)
            return 2
        a = {"name": args.name, "code": code, "render": not args.no_render}
        if pid:
            a["program"] = pid
        return _emit(call("live.patch", a), json_out=args.json)
    if act == "repl":
        code = args.arg if args.arg else sys.stdin.read()
        if not code.strip():
            print("usage: sk live repl '<code>'", file=sys.stderr)
            return 2
        a = {"code": code, "mode": args.mode, "render": not args.no_render}
        if pid:
            a["program"] = pid
        return _emit(call("live.repl", a), json_out=args.json)
    if act == "state":
        a = {"program": pid} if pid else {}
        if args.keys:
            a["keys"] = [s.strip() for s in args.keys.split(",") if s.strip()]
        return _emit(call("live.state", a), json_out=args.json)
    if act == "snapshot":
        if not args.arg:
            print("usage: sk live snapshot <name>", file=sys.stderr)
            return 2
        a = {"op": "save", "name": args.arg}
        if pid:
            a["program"] = pid
        return _emit(call("live.snapshot", a), json_out=args.json)
    if act == "restore":
        if not args.arg:
            print("usage: sk live restore <name>", file=sys.stderr)
            return 2
        a = {"op": "restore", "name": args.arg}
        if pid:
            a["program"] = pid
        return _emit(call("live.snapshot", a), json_out=args.json)
    if act == "render":
        a = {"svg": bool(args.svg)}
        if pid:
            a["program"] = pid
        res = call("live.render", a)
        if args.svg and not args.json:
            d = res.to_dict(max_bytes=None)
            if res.ok and isinstance(d.get("data"), dict) and "svg" in d["data"]:
                sys.stdout.write(d["data"]["svg"] + "\n")
                return 0
        return _emit(res, json_out=args.json)
    if act == "scene":
        try:
            payload = json.loads(args.arg) if args.arg.strip() else {"op": "list"}
        except ValueError as exc:
            print(f"invalid json: {exc}", file=sys.stderr)
            return 2
        if pid:
            payload["program"] = pid
        return _emit(call("live.scene", payload), json_out=args.json)
    if act == "serve":
        if args.arg == "stop":
            return _emit(call("live.serve", {"op": "stop"}), json_out=args.json)
        a = {}
        if args.host:
            a["host"] = args.host
        if args.port is not None:
            a["port"] = args.port
        res = call("live.serve", a)
        if args.wait:
            return _live_wait(res, args)
        return _emit(res, json_out=args.json)
    print(f"unknown live action {act!r}", file=sys.stderr)  # pragma: no cover
    return 2


def _maybe_serve_hint(res: Any, args: argparse.Namespace, call: Any) -> None:
    """`sk live start` on a tty points at the panel so the loop is visible."""
    if not args.json and res.ok:
        d = res.to_dict(max_bytes=None).get("data") or {}
        st = (d.get("status") or {})
        if st.get("abs_path"):
            print(f"  live: {st['id']} watching {st['path']} "
                  f"({st.get('reloads', 0)} reloads)  -  panel: `sk live serve --wait`",
                  file=sys.stderr)


def _live_wait(res: Any, args: argparse.Namespace) -> int:
    """Keep the process (watcher + panel threads are daemons) alive."""
    import time

    d = res.to_dict(max_bytes=None).get("data") or {}
    if not res.ok:
        return _emit(res, json_out=args.json)
    print(f"  panel: {d.get('url')}  (Ctrl-C to stop)", file=sys.stderr)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


def _live_via_panel(args: argparse.Namespace, cfg: Config) -> int:
    """One-shot commands against the running panel's HTTP controls.

    The panel process is the one that owns live programs; this client just
    speaks its JSON routes. Any answer decodes to stdout as-is."""
    import urllib.error
    import urllib.request

    host = args.host or cfg.live.host
    port = args.port if args.port is not None else cfg.live.port
    base = f"http://{host}:{port}"
    pid = args.program

    def _post(route: str, payload: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(base + route, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read())
            except Exception:
                return {"ok": False, "error": f"panel answered HTTP {e.code}"}
        except OSError as e:
            return {"ok": False,
                    "error": f"panel unreachable at {base} ({e}) - is `sk live serve "
                             "--wait` or `sk live demo` running?"}

    def _get(route: str) -> Any:
        try:
            with urllib.request.urlopen(base + route, timeout=30) as r:
                return json.loads(r.read())
        except OSError as e:
            return {"ok": False, "error": f"panel unreachable at {base} ({e})"}

    def _emit_panel(obj: Any) -> int:
        ok = bool(obj.get("ok", True)) if isinstance(obj, dict) else True
        if args.json:
            print(compact_json(obj))
        else:
            print(pretty_json(obj))
        return 0 if ok else 1

    def _ctl(action: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"action": action, **(extra or {})}
        if pid:
            body["program"] = pid
        return _post("/api/control", body)

    act = args.action
    if act == "status":
        return _emit_panel(_get("/version"))
    if act == "start":
        if not args.arg:
            print("usage: sk live start <path> --via-panel", file=sys.stderr)
            return 2
        return _emit_panel(_ctl("start", {"path": args.arg, "watch": not args.no_watch,
                                          "auto_render": not args.no_render}))
    if act == "stop":
        body: dict[str, Any] = {"action": "stop"}
        if pid or args.arg:
            body["program"] = pid or args.arg
        return _emit_panel(_post("/api/control", body))
    if act == "reload":
        extra: dict[str, Any] = {}
        if args.force_source:
            extra["force_source"] = [s.strip() for s in args.force_source.split(",") if s.strip()]
        return _emit_panel(_ctl("reload", extra))
    if act == "patch":
        code = args.arg or ""
        if args.file:
            if args.file == "-":
                code = sys.stdin.read()
            else:
                with open(args.file, encoding="utf-8") as fh:
                    code = fh.read()
        if not (args.name and code):
            print("usage: sk live patch <code> --name render --via-panel", file=sys.stderr)
            return 2
        return _emit_panel(_ctl("patch", {"name": args.name, "code": code}))
    if act == "repl":
        code = args.arg if args.arg else sys.stdin.read()
        if not code.strip():
            print("usage: sk live repl '<code>' --via-panel", file=sys.stderr)
            return 2
        body = {"code": code, "mode": args.mode, "render": not args.no_render}
        if pid:
            body["program"] = pid
        return _emit_panel(_post("/repl", body))
    if act == "state":
        route = "/state" + (f"?program={pid}" if pid else "")
        out = _get(route)
        if args.keys and isinstance(out, dict) and "names" in out:
            wanted = {s.strip() for s in args.keys.split(",") if s.strip()}
            out["names"] = {k: v for k, v in out["names"].items() if k in wanted}
        return _emit_panel(out)
    if act == "snapshot":
        return _emit_panel(_ctl("save_snapshot", {"name": args.arg}))
    if act == "restore":
        return _emit_panel(_ctl("restore_snapshot", {"name": args.arg}))
    if act == "render":
        if args.svg:
            try:
                with urllib.request.urlopen(base + "/frame.svg", timeout=30) as r:
                    sys.stdout.write(r.read().decode("utf-8", "replace") + "\n")
                return 0
            except OSError as e:
                print(f"panel unreachable at {base} ({e})", file=sys.stderr)
                return 1
        return _emit_panel(_ctl("render", {}))
    print(f"--via-panel is not meaningful for `sk live {act}`", file=sys.stderr)
    return 2


def _live_demo(args: argparse.Namespace, tk: Any, call: Any, cfg: Config) -> int:
    """`sk live demo`: materialise the orbital playground into the workspace
    (journaled fs.write - undoable), start it watched, and serve the panel.
    One command = the whole HMR loop visible in a browser."""
    from .live.demos import ORBITAL_SRC

    target_dir = args.arg or os.path.join(cfg.workspace, "live_playground")
    cfg_abs = os.path.abspath(target_dir)
    mk = call("fs.mkdir", {"path": cfg_abs})
    if not mk.ok:
        return _emit(mk, json_out=args.json)
    prog_path = os.path.join(cfg_abs, "orbital.py")
    wr = call("fs.write", {"path": prog_path, "content": ORBITAL_SRC})
    if not wr.ok:
        return _emit(wr, json_out=args.json)
    start = call("live.start", {"path": prog_path, "program": "demo"})
    if not start.ok:
        return _emit(start, json_out=args.json)
    a: dict[str, Any] = {}
    if args.host:
        a["host"] = args.host
    if args.port is not None:
        a["port"] = args.port
    panel = call("live.serve", a)
    if not panel.ok:
        return _emit(panel, json_out=args.json)
    url = (panel.to_dict(max_bytes=None).get("data") or {}).get("url")
    print("  LiveREPL HMR demo is up:", file=sys.stderr)
    print(f"    program : {prog_path}", file=sys.stderr)
    print(f"    panel   : {url}", file=sys.stderr)
    print("    try     : edit the file and save, or in the panel REPL:  hue = \"#f2cc60\"",
          file=sys.stderr)
    return _live_wait(panel, args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
