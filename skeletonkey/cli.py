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
import time
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

    # P5: Projects command
    pr = sub.add_parser("projects", help="list/describe/enable/disable external projects")
    pr.add_argument("action", choices=["list", "describe", "enable", "disable", "scan", "stats", "watch"],
                   nargs="?", default="list")
    pr.add_argument("name", nargs="?", default="",
                   help="project name (describe, enable, disable)")
    pr.add_argument("--type", default=None,
                   choices=["skill", "tool-pack", "python-package", "mcp-server", "all"],
                   help="filter by project type")
    pr.add_argument("--enabled", action="store_true",
                   help="with list: show only enabled projects")
    pr.add_argument("--disabled", action="store_true",
                   help="with list: show only disabled projects")
    pr.add_argument("--refresh", action="store_true",
                   help="with list: refresh the project index before listing")
    pr.add_argument("--all", action="store_true",
                   help="with list: show all details")
    pr.add_argument("--interval", type=float, default=1.0,
                   help="with watch: polling interval in seconds")
    pr.add_argument("--debounce", type=float, default=0.5,
                   help="with watch: debounce time in seconds")

    # P5.1: Integrations command
    in_ = sub.add_parser("integrations", help="list/describe/enable/disable/load/unload/validate/discover/generate integrations")
    in_.add_argument("action", choices=["list", "describe", "enable", "disable", "load", "unload", "health", "stats", "validate", "discover-clis", "generate-cli"],
                    nargs="?", default="list")
    in_.add_argument("name", nargs="?", default="",
                    help="integration name (describe, enable, disable, load, unload, health) or CLI name for generate-cli")
    in_.add_argument("--cli-names", nargs="+", default=None,
                    help="with discover-clis: specific CLI names to check")
    in_.add_argument("--output-dir", default=None,
                    help="with generate-cli: directory to write integration file")
    in_.add_argument("--subcommands", nargs="+", default=None,
                    help="with generate-cli: specific subcommands to include")
    in_.add_argument("--all-subcommands", action="store_true",
                    help="with generate-cli: include all known subcommands")
    in_.add_argument("--type", default=None,
                    choices=["sdk", "api", "client-library", "api-generator", "framework", "toolchain", "cli", "runtime", "protocol-binding", "codegen", "platform-devkit", "3d-modeling", "billing-payment", "database", "agent-harness", "all"],
                    help="filter by integration type")
    in_.add_argument("--category", default=None,
                    choices=["cloud", "database", "ai-ml", "web", "mobile", "desktop", "serverless", "devtools", "monitoring", "security", "payment", "3d-graphics", "collaboration", "storage", "networking", "messaging", "analytics", "other"],
                    help="filter by integration category")
    in_.add_argument("--platform", default=None,
                    choices=["cross-platform", "aws", "gcp", "azure", "vercel", "supabase", "firebase", "cloudflare", "netlify", "render", "flyio", "railway", "heroku", "digitalocean", "linux", "windows", "macos", "ios", "android", "web-browser", "nodejs", "python", "java", "go", "rust", "csharp", "php", "ruby"],
                    help="filter by target platform")
    in_.add_argument("--enabled", action="store_true",
                    help="with list: show only enabled integrations")
    in_.add_argument("--disabled", action="store_true",
                    help="with list: show only disabled integrations")
    in_.add_argument("--loaded", action="store_true",
                    help="with list: show only loaded integrations")
    in_.add_argument("--all", action="store_true",
                    help="with list: show all details")
    in_.add_argument("--health", action="store_true",
                    help="with list: include health status")

    # P5: Catalog command
    ct = sub.add_parser("catalog", help="tool catalog management")
    ct.add_argument("action", choices=["create", "load", "save", "stats", "list", "describe", "list-integrations", "list-projects", "distribution", "update", "remove", "export", "diff", "publish", "package", "watch"],
                   nargs="?", default="stats")
    ct.add_argument("arg", nargs="?", default="",
                   help="tool ID (describe, update, remove) or path (load, save, export, diff)")
    ct.add_argument("--path", default=None,
                   help="file path (load, save, export, diff)")
    ct.add_argument("--format", default="json",
                   choices=["json", "jsonl", "csv"],
                   help="output format (export)")
    ct.add_argument("--risk", default=None,
                   help="filter by risk level")
    ct.add_argument("--tier", default=None,
                   help="filter by tier")
    ct.add_argument("--integration", default=None,
                   help="filter by integration name")
    ct.add_argument("--tag", default=None,
                   help="filter by tag")
    ct.add_argument("--limit", type=int, default=100,
                   help="limit results")
    ct.add_argument("--offset", type=int, default=0,
                   help="pagination offset")
    ct.add_argument("--token", default=None, help="auth token (publish)")
    ct.add_argument("--interval", type=float, default=1.0, help="polling interval in seconds (watch)")
    ct.add_argument("--debounce", type=float, default=0.5, help="debounce time in seconds (watch)")
    ct.add_argument("--max-iterations", type=int, default=0, help="max iterations 0=infinite (watch)")

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
    e.add_argument("--as-subagent", action="store_true",
                   help="route through a subagent boundary (restricted tier, read-only by default)")
    e.add_argument("--sub-tier", default="task", choices=["core", "task", "full"],
                   help="subagent tier (default: task)")
    e.add_argument("--sub-read-only", action="store_true", default=True,
                   help="subagent read-only mode (default: true)")
    e.add_argument("--sub-writable", action="store_true",
                   help="allow writes in the subagent (overrides --sub-read-only)")
    e.add_argument("--sub-max-calls", type=int, default=50,
                   help="subagent max calls (default: 50)")
    e.add_argument("--sub-max-time", type=float, default=60.0,
                   help="subagent max wall time in seconds (default: 60)")

    m = sub.add_parser("mcp", help="run the MCP server")
    m.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http"])
    m.add_argument("--host", default=None, help="with streamable-http: bind host")
    m.add_argument("--port", type=int, default=None, help="with streamable-http: bind port")

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

    d = sub.add_parser("doctor",
                       help="diagnose this install as one JSON blob (config, roots, probe, live stdio test)")
    d.add_argument("--config", default=None, help="config file to diagnose (default: the layered load)")
    d.add_argument("--fix", action="store_true",
                   help="apply the safe repairs: create state/spill dirs, retire a stale profile cache")
    d.add_argument("--no-probe", action="store_true",
                   help="skip the live stdio server test (everything else still runs)")

    rp = sub.add_parser("replay", help="re-execute a recorded run in a scratch copy and diff envelopes")
    rp.add_argument("ref", help="path to a run recording, or a task id under <state>/runs/")
    ev = sub.add_parser("eval", help="score a suite of scripted tasks")
    ev.add_argument("--suite", action="append", required=True,
                    help="suite file, repeatable (one task per JSON line)")

    ex = sub.add_parser("export", help="export tools to external agent formats (openai-tools, anthropic-tools, mcp-manifest)")
    ex.add_argument("format", choices=["openai-tools", "anthropic-tools", "mcp-manifest",
                                       "viz-html", "list"],
                    help="export format, or 'list' to show available formats")
    ex.add_argument("--tier", default=None, choices=["core", "task", "full"],
                    help="export tools from this tier (default: full)")
    ex.add_argument("--read-only", dest="export_read_only", action="store_true",
                    help="export only read-only tools")
    ex.add_argument("--output", "-o", default=None,
                    help="write output to file instead of stdout")

    dv = sub.add_parser("dev", help="start the whole dev viewport: live panel + tool catalog")
    dv.add_argument("path", nargs="?", default=None,
                    help="a python file to watch (optional; omit for catalog only)")
    dv.add_argument("--host", default=None,
                    help="bind host for both servers (loopback by default; remote binds require auth)")
    dv.add_argument("--allow-remote", action="store_true",
                    help="explicitly permit a non-loopback preview bind (requires --auth-token)")
    dv.add_argument("--auth-token", default=None, help="remote preview bearer token (at least 32 bytes)")
    dv.add_argument("--allowed-origin", action="append", default=None,
                    help="exact browser origin allowed to call the preview; repeatable")
    dv.add_argument("--public-base-url", default=None,
                    help="actual browser URL to advertise for a preview bind")
    dv.add_argument("--panel-port", type=int, default=None, help="live panel port (default 8010)")
    dv.add_argument("--viz-port", type=int, default=0, help="catalog port (0 = ephemeral)")
    dv.add_argument("--two-servers", action="store_true",
                    help="run the catalog as a separate server on --viz-port and "
                         "cross-link it, instead of mounting it at /tools "
                         "(ADR-0017 mode: read-only is then structural)")
    dv.add_argument("--no-panel", action="store_true", help="catalog only")
    dv.add_argument("--no-viz", action="store_true", help="panel only")
    dv.add_argument("--open", dest="dev_open", action="store_true",
                    help="open the panel in the default browser")
    dv.add_argument("--runtime", action="store_true",
                    help="overlay ledger runtime data on the catalog")

    vz = sub.add_parser("viz", help="tool visualizer: the whole toolset as a page or a document")
    vz.add_argument("--json", dest="viz_json", action="store_true",
                    help="emit the model as JSON instead of the HTML page")
    vz.add_argument("--title", default="SkeletonKey toolset",
                    help="page title for the HTML catalog")
    vz.add_argument("--open", dest="viz_open", action="store_true",
                    help="open the page in the default browser (with --serve too)")
    vz.add_argument("--serve", action="store_true",
                    help="serve the catalog and watch for toolset drift")
    vz.add_argument("--port", type=int, default=0,
                    help="port for --serve (default: an unused one)")
    vz.add_argument("--host", default="127.0.0.1",
                    help="bind address for --serve (default: loopback only)")
    vz.add_argument("--allow-remote", action="store_true",
                    help="explicitly permit a non-loopback catalog bind (requires --auth-token)")
    vz.add_argument("--auth-token", default=None, help="remote catalog bearer token (at least 32 bytes)")
    vz.add_argument("--allowed-origin", action="append", default=None,
                    help="exact browser origin allowed to read the catalog; repeatable")
    vz.add_argument("--public-base-url", default=None,
                    help="actual browser URL to advertise for a catalog bind")
    vz.add_argument("--poll-ms", type=int, default=2000,
                    help="how often the served page checks for drift")
    vz.add_argument("--panel", metavar="URL", default=None,
                    help="cross-link the served catalog to a running `sk live serve` "
                         "panel (a link only; the catalog never drives it)")
    vz.add_argument("--explain", metavar="TOOL", default=None,
                    help="print the gate decision chain for one tool and exit")
    vz.add_argument("--why-not", action="store_true",
                    help="print the decision chain for every withheld tool and exit")
    vz.add_argument("--as-host", default=None, choices=["windows", "linux", "darwin"],
                    help="model what a host of this OS would see (derived, never probed)")
    vz.add_argument("--diff", metavar="MODEL.json", default=None,
                    help="diff this host's model against a saved one")
    vz.add_argument("--runtime", action="store_true",
                    help="overlay what the ledger measured, and lint the manifests")
    vz.add_argument("--summary", action="store_true",
                    help="compact, diff-reviewable text summary (one line per tool)")
    vz.add_argument("--check", action="store_true",
                    help="with --summary and -o: exit 1 and print a diff if the file "
                         "is stale, instead of rewriting it (for CI)")
    vz.add_argument("--from", dest="viz_from", metavar="FILE", default=None,
                    help="visualise a tool list from a JSON file (OpenAI functions, "
                         "an MCP tools/list, or a plain list) instead of this registry")
    vz.add_argument("--drift", action="store_true",
                    help="print only the contract-drift list and exit (non-zero if any)")
    vz.add_argument("--tier", default=None, choices=["core", "task", "full"],
                    help="build the model for this advertisement tier (default: active tier)")
    vz.add_argument("--read-only", dest="viz_read_only", action="store_true",
                    help="model the toolset as a read-only host would see it")
    vz.add_argument("--no-schemas", action="store_true",
                    help="omit input/output schemas (smaller document)")
    vz.add_argument("--no-examples", action="store_true", help="omit worked examples")
    vz.add_argument("--output", "-o", default=None,
                    help="write to a file (.json implies --json; .html the page)")

    ag = sub.add_parser("agents", help="purpose-driven agent workspace: map live tools to bounded agents")
    ag.add_argument("--json", dest="agents_json", action="store_true",
                    help="emit the purpose-agent model as JSON")
    ag.add_argument("--serve", action="store_true",
                    help="serve the catalog and agent workspace together")
    ag.add_argument("--port", type=int, default=0,
                    help="port for --serve (default: an unused one)")
    ag.add_argument("--host", default="127.0.0.1",
                    help="bind address for --serve (default: loopback only)")
    ag.add_argument("--tier", default=None, choices=["core", "task", "full"],
                    help="project the agent tools at this advertisement tier")
    ag.add_argument("--read-only", dest="agents_read_only", action="store_true",
                    help="project a read-only host")
    ag.add_argument("--output", "-o", default=None,
                    help="write the standalone agent workspace (.html) or model (.json)")

    args = ap.parse_args(argv)
    if args.cmd == "doctor":
        # Doctor builds its own scratch-state toolkit: the operator's real state dir is
        # examined with os-level checks precisely so a broken one is reported, not
        # silently created by the diagnosis itself.
        from . import diagnostics

        overrides: dict[str, Any] = {}
        if args.root:
            overrides["roots"] = list(args.root)
        if args.read_only:
            overrides["policy"] = {"read_only": True}
        rep = diagnostics.doctor(cwd=args.cwd, config_path=args.config,
                                 overrides=overrides or None, fix=args.fix,
                                 probe=not args.no_probe)
        if args.json:
            print(compact_json(rep))
        else:
            print(pretty_json(rep))
            for c in rep["checks"]:
                if c["ok"]:
                    continue
                print(f"  FAIL {c['id']}: {c.get('error') or c.get('hint') or 'see check above'}",
                      file=sys.stderr)
            if rep["fixes"]:
                for f in rep["fixes"]:
                    print(f"  fixed: {f.get('fix')} -> {f.get('path', '')}", file=sys.stderr)
            print(f"  doctor: {'OK' if rep['ok'] else 'PROBLEMS FOUND'} "
                  f"({sum(1 for c in rep['checks'] if c['ok'])}/{len(rep['checks'])} checks pass)",
                  file=sys.stderr)
        return 0 if rep["ok"] else 1

    if args.cmd == "mcp":
        from .mcp.__main__ import main as mcp_main

        rest = ["--transport", args.transport]
        for r in (args.root or []):
            rest += ["--root", r]
        if args.cwd:
            rest += ["--cwd", args.cwd]
        if args.read_only:
            rest.append("--read-only")
        if getattr(args, "host", None):
            rest += ["--host", args.host]
        if getattr(args, "port", None) is not None:
            rest += ["--port", str(args.port)]
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
        if getattr(args, "as_subagent", False):
            from .core.subagent import SubagentSpec, call_as_subagent, create_subagent
            spec = SubagentSpec(
                name=f"cli-call-{args.tool}",
                tier=args.sub_tier,
                read_only=not args.sub_writable,
                max_calls=args.sub_max_calls,
                max_wall_time_s=args.sub_max_time,
            )
            sub = create_subagent(tk.engine, spec=spec)
            result = call_as_subagent(sub, args.tool, payload)
            summary = sub.close()
            # Wrap the subagent result in a ToolResult-compatible shape
            envelope = {
                "ok": result["ok"],
                "tool": args.tool,
                "data": result.get("data"),
                "subagent": summary,
            }
            if result.get("error"):
                envelope["error"] = result["error"]
            if args.json:
                print(compact_json(envelope))
            else:
                print(pretty_json(envelope))
            return 0 if result["ok"] else 1
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
    if cmd == "export":
        from .core import export as export_mod

        fmt = args.format
        if fmt == "list":
            formats = export_mod.list_formats()
            if args.json:
                print(compact_json({"formats": formats}))
            else:
                print("Available export formats:")
                for f in formats:
                    print(f"  {f}")
            return 0

        result = export_mod.export_from_registry(
            tk.engine.registry,
            fmt,
            tier=args.tier,
            read_only=getattr(args, "export_read_only", False),
        )
        # viz-html is a document, not a payload: emit the page itself so
        # `sk export viz-html -o tools.html` is directly openable.
        output = result["html"] if fmt == "viz-html" else compact_json(result)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as fh:
                fh.write(output)
            print(f"Exported {result['count']} tools to {args.output}", file=sys.stderr)
        else:
            if args.json:
                print(output)
            else:
                print(pretty_json(result))
        return 0
    if cmd == "dev":
        return _cmd_dev(args, tk, cfg)
    if cmd == "agents":
        from .agents import build_agent_model, render_agent_html

        agent_model = build_agent_model(
            tk.engine.registry,
            tier=getattr(args, "tier", None),
            read_only=bool(getattr(args, "agents_read_only", False)),
        )
        if getattr(args, "serve", False):
            from .viz.server import VizServer

            try:
                server = VizServer(
                    tk.engine.registry,
                    host=args.host,
                    port=args.port,
                    tier=args.tier,
                    read_only=args.agents_read_only,
                    title="SkeletonKey toolset",
                )
            except ValueError as exc:
                print(f"agent server refused: {exc}", file=sys.stderr)
                return 2
            server.start()
            print(f"agent workspace serving at {server.url}/agents", file=sys.stderr)
            print("  routes: /agents /api/agents / (tool catalog)", file=sys.stderr)
            print("  ctrl-c to stop", file=sys.stderr)
            try:
                while True:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                print("", file=sys.stderr)
            finally:
                server.stop()
            return 0

        output_path = getattr(args, "output", None)
        as_json = bool(getattr(args, "agents_json", False)) or not output_path
        if output_path and output_path.lower().endswith(".json"):
            as_json = True
        if output_path and output_path.lower().endswith((".html", ".htm")):
            as_json = False
        output = (pretty_json(agent_model.to_json()) if as_json
                  else render_agent_html(agent_model))
        if output_path:
            with open(output_path, "w", encoding="utf-8") as fh:
                fh.write(output if output.endswith("\n") else output + "\n")
            print(f"agent workspace written to {output_path} "
                  f"({agent_model.assigned_tool_count}/{agent_model.tool_count} tools mapped)",
                  file=sys.stderr)
        else:
            print(output)
        return 0
    if cmd == "viz":
        from .viz import build_viz_model, render_html

        # --from lets you point the visualiser at somebody else's tool list:
        # an OpenAI function array, an MCP tools/list, a plain list of dicts.
        # It is adapted into a real Registry (viz/adapt.py) so every downstream
        # surface - explain, diff, serve, summary - works identically rather
        # than through a second, weaker code path.
        viz_registry = tk.engine.registry
        if getattr(args, "viz_from", None):
            from .viz.adapt import as_registry

            try:
                with open(args.viz_from, encoding="utf-8") as fh:
                    raw = json.load(fh)
            except (OSError, ValueError) as exc:
                print(f"cannot read {args.viz_from}: {exc}", file=sys.stderr)
                return 2
            try:
                viz_registry = as_registry(raw)
            except (TypeError, ValueError) as exc:
                print(f"cannot adapt {args.viz_from}: {exc}", file=sys.stderr)
                return 2

        # ---- V4: the why-not surfaces, which answer and exit -----------------
        if args.explain or args.why_not:
            from .viz.explain import explain_model, explain_tool

            # `sk viz --json` sets the subparser's flag; `sk --json viz` sets the
            # global one. Both mean the same thing to a caller, so honour either.
            want_json = args.json or getattr(args, "viz_json", False)
            common = {"tier": args.tier,
                      "read_only": getattr(args, "viz_read_only", False)}
            if args.explain:
                chains = {args.explain: explain_tool(viz_registry,
                                                     args.explain, **common)}
            else:
                chains = {k: v for k, v in
                          explain_model(viz_registry, **common).items()
                          if not v.advertised}
            if want_json:
                print(compact_json({k: v.to_json() for k, v in chains.items()}))
                return 0
            if not chains:
                print("every registered tool is advertised on this host.")
                return 0
            marks = {"pass": "  ok  ", "fail": " FAIL ", "note": " note ", "skip": "  --  "}
            for tool_id, chain in chains.items():
                head = "advertised" if chain.advertised else "WITHHELD"
                print(f"\n{tool_id}  [{head}]")
                print(f"  {chain.verdict}")
                for step in chain.steps:
                    print(f"  {marks.get(step.outcome, '  ?   ')} {step.rule:22} "
                          f"{step.detail}")
                    for line in step.evidence:
                        print(f"           - {line}")
                    if step.fix and step.outcome == "fail":
                        print(f"           fix: {step.fix}")
            return 0

        if args.diff:
            from .viz.explain import diff_models

            with open(args.diff, encoding="utf-8") as fh:
                saved = json.load(fh)
            current = build_viz_model(
                viz_registry, tier=args.tier,
                read_only=getattr(args, "viz_read_only", False))
            report = diff_models(saved, current)
            if args.json or getattr(args, "viz_json", False):
                print(compact_json(report))
                return 0
            if report["same"]:
                print(f"no change ({report['digest']['to']})")
                return 0
            for tool_id in report["added"]:
                print(f"  + {tool_id}")
            for tool_id in report["removed"]:
                print(f"  - {tool_id}")
            for row in report["changed"]:
                bits = ", ".join(f"{k} {v['from']}->{v['to']}"
                                 for k, v in row["changes"].items())
                print(f"  ~ {row['id']}: {bits}")
            for row in report["exposure"]:
                arrow = "now advertised" if row["to"] else f"now withheld ({row['why']})"
                print(f"  ! {row['id']}: {arrow}")
            tok = report["tokens"]
            print(f"  tokens {tok['from']} -> {tok['to']} ({tok['delta']:+d})")
            return 0

        if args.serve:
            from .viz.server import VizServer

            try:
                server = VizServer(viz_registry, host=args.host, port=args.port,
                               title=args.title, tier=args.tier,
                               read_only=getattr(args, "viz_read_only", False),
                               poll_ms=args.poll_ms,
                               ledger=tk.ledger if args.runtime else None,
                               panel_url=getattr(args, "panel", None),
                               allow_remote=getattr(args, "allow_remote", False),
                               auth_token=getattr(args, "auth_token", None) or "",
                               allowed_origins=tuple(getattr(args, "allowed_origin", None) or ()),
                               public_base_url=getattr(args, "public_base_url", None) or "")
            except ValueError as exc:
                print(f"viz server refused: {exc}", file=sys.stderr)
                return 2
            server.start()
            agg = server.model().aggregates
            print(f"viz serving {agg['registered']} tools "
                  f"({agg['advertised']} advertised) at {server.url}", file=sys.stderr)
            print("  routes: / /api/model /api/digest /api/tool/<id> /healthz",
                  file=sys.stderr)
            print("  ctrl-c to stop", file=sys.stderr)
            if getattr(args, "viz_open", False):
                import webbrowser

                webbrowser.open(server.url)
            try:
                while True:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                print("", file=sys.stderr)
            finally:
                server.stop()
            return 0

        if args.as_host:
            from .viz.explain import hypothetical_profile

            profile = hypothetical_profile(viz_registry.profile, args.as_host)
        else:
            profile = None
        model = build_viz_model(
            viz_registry,
            profile=profile,
            tier=args.tier,
            read_only=getattr(args, "viz_read_only", False),
            include_schemas=not args.no_schemas,
            include_examples=not args.no_examples,
        )
        if args.as_host:
            model.host["hypothetical"] = True
            model.host["os"] = args.as_host

        # V5: the ledger overlay. Absent by default and absent-safe: no ledger
        # means an empty overlay and a page identical to the one before it.
        if args.runtime or args.drift:
            from .viz.runtime import attach_runtime

            attach_runtime(model, tk.ledger)

        if args.drift:
            rows = (model.runtime or {}).get("drift", [])
            if args.json or getattr(args, "viz_json", False):
                print(compact_json({"drift": rows, "count": len(rows)}))
            elif not (model.runtime or {}).get("available"):
                print("no ledger activity recorded yet; nothing to lint.")
            elif not rows:
                print("no contract drift: every exercised tool is within its "
                      "declared envelope.")
            else:
                for row in rows:
                    print(f"  {row['kind']:12} {row['id']:28} {row['detail']}")
                print(f"  {len(rows)} tool(s) whose manifest disagrees with reality")
            return 1 if rows else 0
        # The output *shape* follows the flag, or the file extension when a path
        # says what it wants: `-o tools.html` is a page, `-o model.json` is data.
        as_json = bool(getattr(args, "viz_json", False)) or args.json
        if args.summary or (args.output and args.output.lower().endswith((".html", ".htm"))):
            as_json = False
        elif args.output and args.output.lower().endswith(".json"):
            as_json = True
        elif not args.output and not as_json:
            # No file and no format asked for: stdout gets JSON, because a
            # terminal is not a browser. `-o page.html` is the page path.
            as_json = True

        if args.summary:
            from .viz import summary_lines

            output = summary_lines(model)
            # --check turns the snapshot into a CI gate: no rewrite, exit 1
            # and show the diff. The diff is the point - it tells a reviewer
            # the SHAPE of the toolset moved, which is the thing worth
            # noticing, not merely that a file is out of date.
            if args.check:
                if not args.output:
                    print("--check needs -o FILE (the snapshot to compare against)",
                          file=sys.stderr)
                    return 2
                try:
                    with open(args.output, encoding="utf-8") as fh:
                        on_disk = fh.read()
                except OSError:
                    print(f"{args.output} does not exist; generate it with "
                          f"`sk viz --summary -o {args.output}`", file=sys.stderr)
                    return 1
                if on_disk.strip() == output.strip():
                    print(f"{args.output} is current", file=sys.stderr)
                    return 0
                import difflib

                diff = difflib.unified_diff(
                    on_disk.splitlines(), output.splitlines(),
                    fromfile=f"{args.output} (committed)", tofile="live registry",
                    lineterm="", n=2)
                print("\n".join(diff))
                print(f"\n{args.output} is stale. Regenerate with:\n"
                      f"  sk viz --summary -o {args.output}\n"
                      f"Read the diff above before committing it.", file=sys.stderr)
                return 1
        elif as_json:
            doc = model.to_json()
            doc["model_digest"] = model.digest()
            output = compact_json(doc) if args.json else pretty_json(doc)
        else:
            output = render_html(model, title=args.title)

        agg = model.aggregates
        if args.output:
            with open(args.output, "w", encoding="utf-8") as fh:
                fh.write(output if output.endswith("\n") else output + "\n")
            print(f"viz {'model' if as_json else 'page'}: {agg['registered']} tools "
                  f"({agg['advertised']} advertised, {agg['withheld']} withheld), "
                  f"{len(model.edges)} relations -> {args.output}", file=sys.stderr)
            if getattr(args, "viz_open", False):
                import webbrowser

                webbrowser.open(f"file://{os.path.abspath(args.output)}")
        else:
            print(output)
        return 0
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

    # P5: Projects command handling
    if cmd == "projects":
        json_out = getattr(args, "json", False)
        if args.action == "list":
            if args.refresh and tk.projects:
                tk.projects.refresh()
            projects = tk.projects.list_projects(
                type_filter=args.type,
                enabled_only=args.enabled if args.enabled else (None if not args.disabled else False),
                name_pattern=None
            ) if tk.projects else []
            if json_out:
                print(compact_json([p.to_dict() for p in projects]))
            elif args.all:
                for p in projects:
                    print(f"\n{p.name}:")
                    for k, v in p.to_dict().items():
                        print(f"  {k}: {v}")
            else:
                for p in projects:
                    status = "✓" if p.enabled else "○"
                    print(f"  {status} {p.name:20} {p.type:15} {p.description[:40] if p.description else '-'}")
                if projects:
                    print(f"\n  Total: {len(projects)} projects")
            return 0
        if args.action == "describe":
            if not args.name:
                print("usage: sk projects describe <project-name>", file=sys.stderr)
                return 2
            if not tk.projects:
                print("Project indexer not available", file=sys.stderr)
                return 1
            project = tk.projects.get_project(args.name)
            if not project:
                print(f"Project not found: {args.name}", file=sys.stderr)
                return 1
            if json_out:
                print(compact_json(project.to_dict()))
            else:
                print(f"Name: {project.name}")
                print(f"Path: {project.path}")
                print(f"Type: {project.type}")
                print(f"Enabled: {project.enabled}")
                print(f"Description: {project.description}")
                print(f"Version: {project.version}")
                print(f"Tools: {len(project.tools)}")
                if project.tools:
                    print(f"  {', '.join(project.tools[:10])}{'...' if len(project.tools) > 10 else ''}")
                print(f"Health: {project.health}")
                if project.health_message:
                    print(f"Health Message: {project.health_message}")
                if project.metadata:
                    print(f"Metadata: {compact_json(project.metadata)}")
            return 0
        if args.action == "enable":
            if not args.name:
                print("usage: sk projects enable <project-name>", file=sys.stderr)
                return 2
            if not tk.projects:
                print("Project indexer not available", file=sys.stderr)
                return 1
            project = tk.projects.enable_project(args.name)
            if project:
                if json_out:
                    print(compact_json(project.to_dict()))
                else:
                    print(f"Enabled project: {project.name}")
                return 0
            print(f"Project not found: {args.name}", file=sys.stderr)
            return 1
        if args.action == "disable":
            if not args.name:
                print("usage: sk projects disable <project-name>", file=sys.stderr)
                return 2
            if not tk.projects:
                print("Project indexer not available", file=sys.stderr)
                return 1
            project = tk.projects.disable_project(args.name)
            if project:
                if json_out:
                    print(compact_json(project.to_dict()))
                else:
                    print(f"Disabled project: {project.name}")
                return 0
            print(f"Project not found: {args.name}", file=sys.stderr)
            return 1
        if args.action == "scan":
            if not tk.projects:
                print("Project indexer not available", file=sys.stderr)
                return 1
            result = tk.projects.refresh()
            if json_out:
                print(compact_json(result))
            else:
                print(f"Scanned {result['old_count']} -> {result['new_count']} projects")
                if result['delta'] > 0:
                    print(f"  Added: {result['delta']} new projects")
                elif result['delta'] < 0:
                    print(f"  Removed: {-result['delta']} projects")
            return 0
        if args.action == "stats":
            if not tk.projects:
                print("Project indexer not available", file=sys.stderr)
                return 1
            stats = tk.projects.get_statistics()
            if json_out:
                print(compact_json(stats))
            else:
                print(f"Total Projects: {stats['total_projects']}")
                print(f"Enabled: {stats['enabled_projects']}")
                print(f"Disabled: {stats['disabled_projects']}")
                print(f"Total Tools: {stats['total_tools']}")
                print("\nBy Type:")
                for ptype, count in sorted(stats['by_type'].items()):
                    print(f"  {ptype}: {count}")
                print("\nBy Health:")
                for health, count in sorted(stats['by_health'].items()):
                    print(f"  {health}: {count}")
            return 0
        if args.action == "watch":
            if not tk.projects:
                print("Project indexer not available", file=sys.stderr)
                return 1
            interval = getattr(args, "interval", 1.0)
            debounce = getattr(args, "debounce", 0.5)
            print(f"Starting project watch (interval={interval}s, debounce={debounce}s)...")
            print("Press Ctrl+C to stop")
            success = tk.projects.start_watch(interval=interval, debounce=debounce)
            if not success:
                print("Failed to start watch", file=sys.stderr)
                return 1
            try:
                # `time` is imported at module scope; a local re-import here
                # would shadow it for the whole function and make every earlier
                # `time.sleep` in `_dispatch` an unbound local (F823).
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\nStopping watch...")
            finally:
                tk.projects.stop_watch()
            return 0

    # P5.1: Integrations command handling
    if cmd == "integrations":
        json_out = getattr(args, "json", False)
        if args.action == "list":
            if not tk.integrations:
                print("Integration manager not available", file=sys.stderr)
                return 1
            
            # Get filter parameters
            type_filter = getattr(args, "type", None)
            category_filter = getattr(args, "category", None)
            platform_filter = getattr(args, "platform", None)
            
            # Convert string filters to enum values
            from skeletonkey.integrations import (
                IntegrationCategory,
                IntegrationPlatform,
                IntegrationType,
            )
            
            type_enum = None
            if type_filter and type_filter != "all":
                try:
                    type_enum = IntegrationType(type_filter)
                except ValueError:
                    print(f"Invalid integration type: {type_filter}", file=sys.stderr)
                    return 1
            
            category_enum = None
            if category_filter and category_filter != "all":
                try:
                    category_enum = IntegrationCategory(category_filter)
                except ValueError:
                    print(f"Invalid integration category: {category_filter}", file=sys.stderr)
                    return 1
            
            platform_enum = None
            if platform_filter and platform_filter != "all":
                try:
                    platform_enum = IntegrationPlatform(platform_filter)
                except ValueError:
                    print(f"Invalid integration platform: {platform_filter}", file=sys.stderr)
                    return 1
            
            integrations = tk.integrations.list_integrations(
                type_filter=type_enum,
                category_filter=category_enum,
                platform_filter=platform_enum,
                enabled_only=args.enabled if args.enabled else (None if not args.disabled else False),
            )
            
            if json_out:
                print(compact_json([i.to_dict() for i in integrations]))
            elif args.all:
                for i in integrations:
                    print(f"\n{i.name}:")
                    for k, v in i.to_dict().items():
                        print(f"  {k}: {v}")
            else:
                for i in integrations:
                    status = "+" if i.name in tk.integrations._enabled_integrations else "-"
                    loaded = "*" if i.name in tk.integrations._instances else " "
                    print(f"  {status}{loaded} {i.name:25} {i.integration_type.value:20} {i.description[:40] if i.description else '-'}")
                if integrations:
                    print(f"\n  Total: {len(integrations)} integrations")
            return 0
        
        if args.action == "describe":
            if not args.name:
                print("usage: sk integrations describe <integration-name>", file=sys.stderr)
                return 2
            if not tk.integrations:
                print("Integration manager not available", file=sys.stderr)
                return 1
            
            info = tk.integrations.registry.get_integration_info(args.name)
            if not info:
                print(f"Integration not found: {args.name}", file=sys.stderr)
                return 1
            
            if json_out:
                print(compact_json(info.to_dict()))
            else:
                print(f"Name: {info.name}")
                print(f"Title: {info.title}")
                print(f"Version: {info.version}")
                print(f"Type: {info.integration_type.value}")
                print(f"Category: {info.category.value}")
                if info.platform:
                    print(f"Platform: {info.platform.value}")
                print(f"Description: {info.description}")
                if info.author:
                    print(f"Author: {info.author}")
                if info.license:
                    print(f"License: {info.license}")
                if info.keywords:
                    print(f"Keywords: {', '.join(info.keywords)}")
                if info.dependencies:
                    print(f"Dependencies: {len(info.dependencies)}")
                    for dep in info.dependencies:
                        print(f"  - {dep.name}{' (optional)' if dep.optional else ''}")
                if info.capabilities:
                    print(f"Capabilities: {len(info.capabilities)}")
                    for cap in info.capabilities:
                        print(f"  - {cap.name} (required: {cap.required})")
                
                # Check if loaded
                instance = tk.integrations.get_integration(args.name)
                if instance:
                    print(f"\nStatus: {instance.status.value}")
                    if instance.health != "unknown":
                        print(f"Health: {instance.health}")
                        if instance.health_message:
                            print(f"Health Message: {instance.health_message}")
            return 0
        
        if args.action == "enable":
            if not args.name:
                print("usage: sk integrations enable <integration-name>", file=sys.stderr)
                return 2
            if not tk.integrations:
                print("Integration manager not available", file=sys.stderr)
                return 1
            
            success = tk.integrations.enable_integration(args.name)
            if success:
                if json_out:
                    info = tk.integrations.registry.get_integration_info(args.name)
                    print(compact_json(info.to_dict() if info else {}))
                else:
                    print(f"Enabled integration: {args.name}")
                return 0
            print(f"Failed to enable integration: {args.name}", file=sys.stderr)
            return 1
        
        if args.action == "disable":
            if not args.name:
                print("usage: sk integrations disable <integration-name>", file=sys.stderr)
                return 2
            if not tk.integrations:
                print("Integration manager not available", file=sys.stderr)
                return 1
            
            success = tk.integrations.disable_integration(args.name)
            if success:
                if json_out:
                    info = tk.integrations.registry.get_integration_info(args.name)
                    print(compact_json(info.to_dict() if info else {}))
                else:
                    print(f"Disabled integration: {args.name}")
                return 0
            print(f"Failed to disable integration: {args.name}", file=sys.stderr)
            return 1
        
        if args.action == "load":
            if not args.name:
                print("usage: sk integrations load <integration-name>", file=sys.stderr)
                return 2
            if not tk.integrations:
                print("Integration manager not available", file=sys.stderr)
                return 1
            
            instance = tk.integrations.load_integration(args.name)
            if instance:
                if json_out:
                    print(compact_json(instance.to_dict()))
                else:
                    print(f"Loaded integration: {args.name} (v{instance.info.version})")
                return 0
            print(f"Failed to load integration: {args.name}", file=sys.stderr)
            return 1
        
        if args.action == "unload":
            if not args.name:
                print("usage: sk integrations unload <integration-name>", file=sys.stderr)
                return 2
            if not tk.integrations:
                print("Integration manager not available", file=sys.stderr)
                return 1
            
            success = tk.integrations.unload_integration(args.name)
            if success:
                if json_out:
                    print(compact_json({"unloaded": args.name, "success": True}))
                else:
                    print(f"Unloaded integration: {args.name}")
                return 0
            print(f"Failed to unload integration: {args.name}", file=sys.stderr)
            return 1
        
        if args.action == "health":
            if not args.name:
                # Check all integrations
                if not tk.integrations:
                    print("Integration manager not available", file=sys.stderr)
                    return 1
                
                results = tk.integrations.check_all_health()
                if json_out:
                    print(compact_json(results))
                else:
                    for result in results:
                        status = "✓" if result["health"] == "healthy" else "✗"
                        print(f"  {status} {result['name']}: {result['health']}")
                        if result.get("message"):
                            print(f"      {result['message']}")
                return 0
            if not tk.integrations:
                print("Integration manager not available", file=sys.stderr)
                return 1
            
            result = tk.integrations.check_health(args.name)
            if json_out:
                print(compact_json(result))
            else:
                status = "✓" if result["health"] == "healthy" else "✗"
                print(f"  {status} {args.name}: {result['health']}")
                if result.get("message"):
                    print(f"      {result['message']}")
            return 0
        
        if args.action == "stats":
            if not tk.integrations:
                print("Integration manager not available", file=sys.stderr)
                return 1
            
            stats = tk.integrations.get_statistics()
            if json_out:
                print(compact_json(stats))
            else:
                print(f"Total Integrations: {stats['total_integrations']}")
                print(f"Loaded: {stats['loaded_integrations']}")
                print(f"Enabled: {stats['enabled_integrations']}")
                print(f"Disabled: {stats['disabled_integrations']}")
                
                print("\nBy Type:")
                for itype, count in sorted(stats['by_type'].items()):
                    print(f"  {itype}: {count}")
                
                print("\nBy Category:")
                for category, count in sorted(stats['by_category'].items()):
                    print(f"  {category}: {count}")
                
                if stats['by_platform']:
                    print("\nBy Platform:")
                    for platform, count in sorted(stats['by_platform'].items()):
                        print(f"  {platform}: {count}")
            return 0

        if args.action == "validate":
            if not tk.integrations:
                print("Integration manager not available", file=sys.stderr)
                return 1
            
            from skeletonkey.integrations.validation import validate_integration, validate_registry
            
            if args.name:
                rep = validate_integration(args.name, include_health=args.health)
                if json_out:
                    print(compact_json(rep.to_dict()))
                else:
                    print(f"Integration: {rep.name}")
                    print(f"Valid: {rep.valid}")
                    if rep.errors:
                        print("\nErrors:")
                        for e in rep.errors:
                            print(f"  [ERROR] {e.code}: {e.message}")
                            if e.detail:
                                print(f"    {e.detail}")
                    if rep.warnings:
                        print("\nWarnings:")
                        for w in rep.warnings:
                            print(f"  [WARN]  {w.code}: {w.message}")
                            if w.detail:
                                print(f"    {w.detail}")
                    print(f"\nChecks: {rep.checks}")
            else:
                reports = validate_registry()
                if json_out:
                    print(compact_json({
                        "integrations": [r.to_dict() for r in reports],
                        "count": len(reports),
                        "valid": all(r.valid for r in reports),
                        "error_count": sum(len(r.errors) for r in reports),
                        "warning_count": sum(len(r.warnings) for r in reports),
                    }))
                else:
                    for rep in reports:
                        status = "OK" if rep.valid else "FAIL"
                        print(f"  [{status}] {rep.name} (errors: {len(rep.errors)}, warnings: {len(rep.warnings)})")
                    print(f"\nTotal: {len(reports)} integrations")
                    print(f"Valid: {sum(1 for r in reports if r.valid)}")
                    print(f"Errors: {sum(len(r.errors) for r in reports)}")
                    print(f"Warnings: {sum(len(r.warnings) for r in reports)}")
            return 0

        if args.action == "discover-clis":
            from skeletonkey.integrations.cli_discovery import discover_cli_tools
            
            cli_names = getattr(args, "cli_names", None)
            result = discover_cli_tools(names=cli_names)
            
            if json_out:
                print(compact_json(result.to_dict()))
            else:
                if result.detected:
                    print("Detected CLI Tools:")
                    for cli in result.detected:
                        version_str = f" v{cli.version}" if cli.version else ""
                        print(f"  + {cli.name}{version_str} ({cli.spec.title})")
                        print(f"    Path: {cli.path}")
                        if cli.spec.examples:
                            print(f"    Example: {cli.spec.examples[0]}")
                if result.missing:
                    print(f"\nNot found: {', '.join(result.missing)}")
                if result.errors:
                    print(f"\nErrors: {', '.join(result.errors)}")
                print(f"\nTotal: {len(result.detected)} detected, {len(result.missing)} missing")
            return 0

        if args.action == "generate-cli":
            if not args.name:
                print("usage: sk integrations generate-cli <cli-name> [--output-dir <dir>] [--subcommands <cmd>...] [--all-subcommands]", file=sys.stderr)
                return 2
            if not tk.integrations:
                print("Integration manager not available", file=sys.stderr)
                return 1
            
            from skeletonkey.integrations.cli_discovery import generate_cli_integration
            
            subcommands = getattr(args, "subcommands", None)
            all_subcommands = getattr(args, "all_subcommands", False)
            output_dir = getattr(args, "output_dir", None)
            
            result = generate_cli_integration(
                cli_name=args.name,
                output_dir=output_dir,
                custom_subcommands=subcommands,
                include_all_subcommands=all_subcommands,
            )
            
            if "error" in result:
                print(f"Error: {result['error']}", file=sys.stderr)
                return 1
            
            if json_out:
                print(compact_json(result))
            else:
                if "output_path" in result:
                    print(f"Generated integration: {result['output_path']}")
                else:
                    print("Generated integration code (use --output-dir to write to file)")
                    print(result.get("code", ""))
                if result.get("subcommands_included"):
                    print(f"Subcommands included: {', '.join(result['subcommands_included'])}")
            return 0

    # P5: Catalog command handling
    if cmd == "catalog":
        json_out = getattr(args, "json", False)
        if not tk.catalog_manager:
            print("Catalog manager not available", file=sys.stderr)
            return 1
        
        if args.action == "create":
            result = tk.engine.call('catalog.create', {
                'auto_index': True,
                'save': True,
            })
            if json_out:
                print(compact_json(result.data))
            else:
                if result.ok:
                    print(f"Catalog created: {result.data.get('total_tools')} tools, {result.data.get('total_integrations')} integrations")
                    print(f"Path: {result.data.get('path')}")
                else:
                    print(f"Error: {result.error}")
            return 0 if result.ok else 1
        
        if args.action == "load":
            path = args.path or args.arg
            result = tk.engine.call('catalog.load', {'path': path} if path else {})
            if json_out:
                print(compact_json(result.data))
            else:
                if result.ok:
                    print(f"Catalog loaded: {result.data.get('total_tools')} tools")
                else:
                    print(f"Error: {result.error}")
            return 0 if result.ok else 1
        
        if args.action == "save":
            path = args.path or args.arg
            result = tk.engine.call('catalog.save', {'path': path} if path else {})
            if json_out:
                print(compact_json(result.data))
            else:
                if result.ok:
                    print(f"Catalog saved to {result.data.get('path')}")
                else:
                    print(f"Error: {result.error}")
            return 0 if result.ok else 1
        
        if args.action == "stats":
            result = tk.engine.call('catalog.stats', {})
            if json_out:
                print(compact_json(result.data))
            else:
                if result.ok:
                    d = result.data
                    print(f"Total Tools: {d.get('total_tools')}")
                    print(f"Total Integrations: {d.get('total_integrations')}")
                    print(f"Total Projects: {d.get('total_projects')}")
                    print(f"Generated: {d.get('generated_at')}")
                    print(f"Dirty: {d.get('dirty')}")
                    print("\nBy Risk:")
                    for risk, count in sorted(d.get('by_risk', {}).items()):
                        print(f"  {risk}: {count}")
                    print("\nBy Tier:")
                    for tier, count in sorted(d.get('by_tier', {}).items()):
                        print(f"  {tier}: {count}")
                    print("\nBy Integration:")
                    for integ, count in sorted(d.get('by_integration', {}).items()):
                        print(f"  {integ}: {count}")
                else:
                    print(f"Error: {result.error}")
            return 0 if result.ok else 1
        
        if args.action == "list":
            result = tk.engine.call('catalog.list_tools', {
                'risk': args.risk,
                'tier': args.tier,
                'integration': args.integration,
                'tag': args.tag,
                'limit': args.limit,
                'offset': args.offset,
            })
            if json_out:
                print(compact_json(result.data))
            else:
                if result.ok:
                    for t in result.data.get('tools', []):
                        print(f"  {t['id']:45} risk={t['risk']:12} tier={t['tier']:6} integration={t.get('integration') or 'builtin'}")
                    print(f"\nTotal: {result.data.get('total')}, Returned: {result.data.get('returned')}")
                else:
                    print(f"Error: {result.error}")
            return 0 if result.ok else 1
        
        if args.action == "describe":
            if not args.arg:
                print("usage: sk catalog describe <tool-id>", file=sys.stderr)
                return 2
            result = tk.engine.call('catalog.describe', {'tool_id': args.arg})
            if json_out:
                print(compact_json(result.data))
            else:
                if result.ok:
                    t = result.data
                    print(f"ID: {t['id']}")
                    print(f"Title: {t['title']}")
                    print(f"Description: {t['description']}")
                    print(f"Risk: {t['risk']}")
                    print(f"Tier: {t['tier']}")
                    print(f"Capability: {t['capability']}")
                    print(f"Group: {t.get('group') or 'N/A'}")
                    print(f"Provider: {t.get('provider') or 'N/A'}")
                    print(f"Integration: {t.get('integration') or 'builtin'}")
                    print(f"Tags: {', '.join(t.get('tags', []))}")
                    if t.get('secret_args'):
                        print(f"Secret Args: {', '.join(t['secret_args'])}")
                    if t.get('input_schema'):
                        print(f"Input Schema: {compact_json(t['input_schema'])}")
                else:
                    print(f"Error: {result.error}")
            return 0 if result.ok else 1
        
        if args.action == "list-integrations":
            result = tk.engine.call('catalog.list_integrations', {})
            if json_out:
                print(compact_json(result.data))
            else:
                if result.ok:
                    for i in result.data.get('integrations', []):
                        print(f"  {i['name']}: {i['tool_count']} tools ({i['type']}) enabled={i['enabled']} loaded={i['loaded']}")
                    print(f"\nTotal: {result.data.get('total')}")
                else:
                    print(f"Error: {result.error}")
            return 0 if result.ok else 1
        
        if args.action == "list-projects":
            result = tk.engine.call('catalog.list_projects', {})
            if json_out:
                print(compact_json(result.data))
            else:
                if result.ok:
                    for p in result.data.get('projects', []):
                        print(f"  {p['name']}: {p['type']} ({p['tool_count']} tools) {p['path']}")
                    print(f"\nTotal: {result.data.get('total')}")
                else:
                    print(f"Error: {result.error}")
            return 0 if result.ok else 1
        
        if args.action == "distribution":
            result = tk.engine.call('catalog.get_distribution', {})
            if json_out:
                print(compact_json(result.data))
            else:
                if result.ok:
                    print("By Risk:")
                    for risk, count in sorted(result.data.get('by_risk', {}).items()):
                        print(f"  {risk}: {count}")
                    print("\nBy Tier:")
                    for tier, count in sorted(result.data.get('by_tier', {}).items()):
                        print(f"  {tier}: {count}")
                    print("\nBy Integration:")
                    for integ, count in sorted(result.data.get('by_integration', {}).items()):
                        print(f"  {integ}: {count}")
                else:
                    print(f"Error: {result.error}")
            return 0 if result.ok else 1
        
        if args.action == "update":
            if not args.arg:
                print("usage: sk catalog update <tool-id> [fields...]", file=sys.stderr)
                return 2
            fields = {}
            for f in sys.argv[3:]:
                if '=' in f:
                    k, v = f.split('=', 1)
                    fields[k] = v
            result = tk.engine.call('catalog.update_tool', {'tool_id': args.arg, **fields})
            if json_out:
                print(compact_json(result.data))
            else:
                if result.ok:
                    print(f"Tool {args.arg} updated")
                else:
                    print(f"Error: {result.error}")
            return 0 if result.ok else 1
        
        if args.action == "remove":
            if not args.arg:
                print("usage: sk catalog remove <tool-id>", file=sys.stderr)
                return 2
            result = tk.engine.call('catalog.remove_tool', {'tool_id': args.arg})
            if json_out:
                print(compact_json(result.data))
            else:
                if result.ok:
                    print(f"Tool {args.arg} removed")
                else:
                    print(f"Error: {result.error}")
            return 0 if result.ok else 1
        
        if args.action == "export":
            path = args.path
            result = tk.engine.call('catalog.export', {
                'format': args.format,
                'path': path,
                'filter_risk': args.risk,
                'filter_tier': args.tier,
                'filter_integration': args.integration,
            })
            if json_out:
                print(compact_json(result.data))
            else:
                if result.ok:
                    if path:
                        print(f"Exported to {path} ({result.data.get('tool_count')} tools)")
                    else:
                        print(result.data.get('content', ''))
                else:
                    print(f"Error: {result.error}")
            return 0 if result.ok else 1
        
        if args.action == "diff":
            if not args.arg:
                print("usage: sk catalog diff <old-path> [new-path]", file=sys.stderr)
                return 2
            new_path = args.path
            result = tk.engine.call('catalog.diff', {
                'old_path': args.arg,
                'new_path': new_path,
            })
            if json_out:
                print(compact_json(result.data))
            else:
                if result.ok:
                    d = result.data
                    print(f"Added: {d['summary']['added']}")
                    print(f"Removed: {d['summary']['removed']}")
                    print(f"Modified: {d['summary']['modified']}")
                    if d['added']:
                        print("\nAdded tools:")
                        for t in d['added']:
                            print(f"  + {t['id']}")
                    if d['removed']:
                        print("\nRemoved tools:")
                        for t in d['removed']:
                            print(f"  - {t['id']}")
                    if d['modified']:
                        print("\nModified tools:")
                        for m in d['modified']:
                            print(f"  ~ {m['tool_id']}: {len(m['changes'])} changes")
                else:
                    print(f"Error: {result.error}")
            return 0 if result.ok else 1
        
        if args.action == "publish":
            if not args.arg:
                print("usage: sk catalog publish <registry-url> [--token <auth-token>]", file=sys.stderr)
                return 2
            result = tk.engine.call('catalog.publish', {
                'registry_url': args.arg,
                'auth_token': getattr(args, 'token', None),
            })
            if json_out:
                print(compact_json(result.data))
            else:
                if result.ok:
                    print(f"Catalog published to {args.arg}")
                    print(f"Tools: {result.data.get('tool_count')}")
                else:
                    print(f"Error: {result.error}")
            return 0 if result.ok else 1
        
        if args.action == "package":
            path = args.path
            result = tk.engine.call('catalog.package', {
                'path': path,
                'format': args.format,
            })
            if json_out:
                print(compact_json(result.data))
            else:
                if result.ok:
                    if path:
                        print(f"Package created at {path}")
                    else:
                        print(f"Package created: {result.data.get('path')}")
                else:
                    print(f"Error: {result.error}")
            return 0 if result.ok else 1
        
        if args.action == "watch":
            interval = getattr(args, 'interval', 1.0)
            debounce = getattr(args, 'debounce', 0.5)
            max_iter = getattr(args, 'max_iterations', 0)
            result = tk.engine.call('catalog.watch', {
                'interval': interval,
                'debounce': debounce,
                'max_iterations': max_iter,
            })
            if json_out:
                print(compact_json(result.data))
            else:
                if result.ok:
                    print(f"Watch stopped: {result.data.get('iterations')} iterations")
                else:
                    print(f"Error: {result.error}")
            return 0 if result.ok else 1
        
        print(f"Unknown catalog action: {args.action}", file=sys.stderr)
        return 1
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


def _cmd_dev(args: argparse.Namespace, tk: Any, cfg: Config) -> int:
    """`sk dev`: the whole viewport, one command.

    Default is the merged server (ADR-0018): the live panel serves the
    read-only tool catalog at /tools, so there is one process, one port and
    one URL. `--two-servers` restores the ADR-0017 arrangement, where the
    catalog runs standalone and its read-only-ness is structural rather than
    guarded - which is the stronger property, and still available to anyone
    who wants it.
    """
    import time

    host = args.host or cfg.live.host
    remote_on = bool(args.allow_remote or cfg.live.allow_remote)
    preview_token = args.auth_token if args.auth_token is not None else cfg.live.auth_token
    preview_origins = args.allowed_origin if args.allowed_origin is not None else cfg.live.allowed_origins
    preview_base = args.public_base_url if args.public_base_url is not None else cfg.live.public_base_url
    panel_url = viz_url = None
    viz_server = None
    mounted = not args.two_servers and not args.no_viz and not args.no_panel
    call = tk.engine.call

    # Separate-server mode: the catalog comes up first so its real URL can be
    # handed to live.serve at construction time.
    if args.two_servers and not args.no_viz:
        from .viz.server import VizServer

        viz_server = VizServer(tk.engine.registry, host=host, port=args.viz_port,
                               title="SkeletonKey toolset",
                               ledger=tk.ledger if args.runtime else None,
                               allow_remote=remote_on,
                               auth_token=preview_token or "",
                               allowed_origins=tuple(preview_origins or ()),
                               public_base_url=preview_base or "")
        viz_server.start()
        viz_url = viz_server.url
    elif args.no_panel and not args.no_viz:
        # No panel to mount into, so the catalog must stand alone regardless.
        from .viz.server import VizServer

        viz_server = VizServer(tk.engine.registry, host=host, port=args.viz_port,
                               title="SkeletonKey toolset",
                               ledger=tk.ledger if args.runtime else None,
                               allow_remote=remote_on,
                               auth_token=preview_token or "",
                               allowed_origins=tuple(preview_origins or ()),
                               public_base_url=preview_base or "")
        viz_server.start()
        viz_url = viz_server.url

    try:
        if not args.no_panel:
            if args.path:
                started = call("live.start", {"path": args.path})
                if not started.ok:
                    print(f"live.start failed: {started.error}", file=sys.stderr)
                    return 1
            payload: dict[str, Any] = {"host": host}
            if args.allow_remote:
                payload["allow_remote"] = True
            if args.auth_token is not None:
                payload["auth_token"] = args.auth_token
            if args.allowed_origin:
                payload["allowed_origins"] = args.allowed_origin
            if args.public_base_url is not None:
                payload["public_base_url"] = args.public_base_url
            if args.panel_port is not None:
                payload["port"] = args.panel_port
            if viz_url:
                payload["viz_url"] = viz_url
            if mounted:
                payload["mount_tools"] = True
            served = call("live.serve", payload)
            if not served.ok:
                print(f"live.serve failed: {served.error}", file=sys.stderr)
                return 1
            panel_url = (served.to_dict(max_bytes=None).get("data") or {}).get("url")

            if viz_server is not None and panel_url:
                viz_server.panel_url = panel_url

        if viz_server is None and panel_url is None:
            print("nothing to serve: --no-panel and --no-viz cancel out", file=sys.stderr)
            return 2

        print("  dev viewport up:", file=sys.stderr)
        if panel_url:
            print(f"    panel   : {panel_url}   (live HMR, REPL - executes code)",
                  file=sys.stderr)
        if mounted and panel_url:
            reg = tk.engine.registry
            print(f"    catalog : {panel_url}tools   "
                  f"({len(reg.all())} tools, read-only, same server)", file=sys.stderr)
        elif viz_url:
            agg = viz_server.model().aggregates
            print(f"    catalog : {viz_url}   ({agg['registered']} tools, "
                  f"{agg['advertised']} advertised, separate read-only server)",
                  file=sys.stderr)
            if panel_url:
                print("    two servers: the catalog cannot write, structurally "
                      "(ADR-0017)", file=sys.stderr)
        if args.path:
            print(f"    watching: {args.path}", file=sys.stderr)
        print("  Ctrl-C to stop", file=sys.stderr)

        if getattr(args, "dev_open", False):
            import webbrowser

            webbrowser.open(panel_url or viz_url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            print("", file=sys.stderr)
            return 0
    finally:
        if viz_server is not None:
            viz_server.stop()
        if not args.no_panel:
            call("live.serve", {"op": "stop"})


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
