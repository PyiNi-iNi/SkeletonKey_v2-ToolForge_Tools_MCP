"""`sk doctor` - the whole install, answered as one pasteable JSON blob (P6).

An operator who cannot get the toolkit to answer has exactly one question: *what does
this host actually see?* Doctor answers it in the order the failure would happen -
config layers, roots, state dirs, capability probe, tool advertisement (with the gated
and the load errors), skills, journal/ledger integrity, a **live end-to-end probe of the
real MCP server** (`initialize` -> `tools/list` -> a read-only `fs.stat` call over a real
stdio subprocess), and a read-only scan of which host applications are wired to us.

Two rules shape the implementation:

* **Doctor is read-only by default.** The introspection build uses a *scratch* state dir,
  so diagnosing a broken state dir cannot "fix" it as a side effect and then report
  healthy. `--fix` performs the small safe repairs explicitly (create the state/spill
  dirs, retire a stale profile cache) and says so per fix.
* **Stable schema.** Same check ids, same key sets, in the same order, every run - the
  output is diffable and documentable (`docs/CONNECT-A-HOST.md` publishes the shape).
  No wall-clock timestamp; the meta check carries version/identity instead.

Stdlib only. The live probe speaks line-delimited JSON-RPC with reader *threads*, not
`select`, because `select` on pipes is POSIX-only and this must run wherever `sk` runs.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from .core.config import Config
from .core.ledger import Ledger
from .version import __version__

__all__ = ["SCHEMA", "doctor", "probe_stdio"]

SCHEMA = "sk.doctor/1"
PROTOCOL = "2025-06-18"


def doctor(*, cwd: str | None = None, config_path: str | None = None,
           overrides: dict[str, Any] | None = None, fix: bool = False, probe: bool = True,
           probe_timeout: float = 60.0, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Run every check; returns the report dict. The CLI adds only argparse + printing."""
    env = dict(os.environ) if env is None else dict(env)
    cfg = Config.load(path=config_path, cwd=cwd, overrides=overrides, env=env)

    checks: list[dict[str, Any]] = []
    fixes: list[dict[str, Any]] = []
    scratch = tempfile.mkdtemp(prefix="sk-doctor-")

    def add(check_id: str, ok: bool, **data: Any) -> dict[str, Any]:
        row: dict[str, Any] = {"id": check_id, "ok": bool(ok)}
        row.update(data)
        checks.append(row)
        return row

    try:
        # ---------------------------------------------------------------- meta + config
        add("meta", True, schema=SCHEMA, version=__version__, python=sys.version.split()[0],
            platform=sys.platform)
        add("config", not any(w.startswith("config file") for w in cfg.warnings),
            sources=cfg.source_files, overrides=cfg.overrides_applied, warnings=cfg.warnings)

        # ---------------------------------------------------------------- roots (os-level, pre-build)
        root_rows = []
        for r in cfg.roots:
            exists = os.path.isdir(r)
            writable = False
            if exists:
                try:
                    fd, p = tempfile.mkstemp(dir=r, prefix=".sk-doctor-")
                    os.close(fd)
                    os.unlink(p)
                    writable = True
                except OSError:
                    writable = False
            root_rows.append({"path": r, "exists": exists, "writable": writable})
        add("roots", all(r["exists"] and r["writable"] for r in root_rows), roots=root_rows)

        # ---------------------------------------------------------------- real state dirs (pre-build)
        state_dir = cfg.state.dir
        spill_dir = cfg.budget.spill_dir or os.path.join(state_dir, "spill")
        dirs = {"state": state_dir, "spill": spill_dir}
        before = {k: os.path.isdir(v) for k, v in dirs.items()}
        fresh = not before["state"]          # never ran here: the empty shape is healthy
        if fix:
            for k, v in dirs.items():
                if not before[k]:
                    try:
                        os.makedirs(v, exist_ok=True)
                        fixes.append({"fix": f"created {k} dir", "path": v})
                    except OSError as exc:
                        fixes.append({"fix": f"create {k} dir FAILED", "path": v,
                                      "error": str(exc)})
        journal_dir = os.path.join(state_dir, "journal")
        if fix and cfg.state.journal and not os.path.isdir(journal_dir):
            try:
                os.makedirs(journal_dir, exist_ok=True)
                fixes.append({"fix": "created journal dir", "path": journal_dir})
            except OSError as exc:
                fixes.append({"fix": "create journal dir FAILED", "path": journal_dir,
                              "error": str(exc)})
        profile_cache = os.path.join(state_dir, "profile.json")
        if fix and os.path.isfile(profile_cache):
            # a stale cache is the classic "probe receipts say one thing, host another" cause
            try:
                os.unlink(profile_cache)
                fixes.append({"fix": "retired profile cache (next start re-probes)",
                              "path": profile_cache})
            except OSError as exc:
                fixes.append({"fix": "retire profile cache FAILED", "path": profile_cache,
                              "error": str(exc)})
        now = {k: os.path.isdir(v) for k, v in dirs.items()}
        if fresh:
            state_ok, state_hint = True, "no state yet (never ran here); --fix pre-creates it"
        else:
            # partial state is the real smell: the toolkit ran, then something vanished
            state_ok, state_hint = all(now.values()), None
        add("state", state_ok, dir=state_dir, dirs=now, existed_before=before,
            profile_cache=os.path.isfile(profile_cache),
            hint=state_hint)

        # ---------------------------------------------------------------- introspection (scratch state)
        t0 = time.monotonic()
        tk = _scratch_toolkit(cfg, scratch, env)
        snap = tk.engine.advertise()
        gated = [{"id": m.id, "reason": m.meta.get("gate_reason") or m.meta.get("gate") or
                  "not advertised"}
                 for m in tk.registry.all() if not m.advertised]
        add("tools", True,
            registered=len(tk.registry.all()), advertised=len(snap.tools),
            tier=snap.tier, tokens=snap.tokens, digest=snap.digest,
            gated=gated, load_errors=list(tk.registry.load_errors)[:10],
            seconds=round(time.monotonic() - t0, 3))

        skills = tk.skills.discover()
        add("skills", not tk.skills.errors, discovered=[s.name for s in skills],
            errors=list(tk.skills.errors)[:10])

        prof = tk.profile
        add("profile", True, source=tk.build_report.get("profile_source", "probed"),
            os=prof.os, arch=prof.arch, dialects=prof.available_dialects(),
            preferred_dialect=prof.preferred_dialect(), fingerprint=prof.fingerprint,
            warnings=list(prof.warnings)[:10])
        tk.close()

        # ---------------------------------------------------------------- journal + ledger (real state)
        journal_dir = os.path.join(state_dir, "journal")
        jentries = 0
        if os.path.isdir(journal_dir):
            jentries = sum(len(fs) for _, _, fs in os.walk(journal_dir))
        add("journal", (not cfg.state.journal) or os.path.isdir(journal_dir) or fresh,
            enabled=cfg.state.journal, dir=journal_dir, entries=jentries)
        ledger_path = os.path.join(state_dir, "ledger.ndjson")
        led = {"enabled": cfg.state.ledger, "path": ledger_path,
               "exists": os.path.isfile(ledger_path)}
        if led["enabled"] and led["exists"]:
            ver = Ledger(ledger_path, enabled=True).verify()
            led.update(lines=ver.get("lines"), valid=ver.get("valid"),
                       broken_at=ver.get("broken_at"))
        add("ledger", (not led["enabled"]) or (not led["exists"]) or led.get("valid", True),
            **led)

        # ---------------------------------------------------------------- live stdio probe
        if probe:
            rep = probe_stdio(python=sys.executable, timeout=probe_timeout, env=env)
            error = rep.get("error")
            row = {k: v for k, v in rep.items() if k != "ok"}
            add("mcp.stdio", bool(rep.get("ok")), error=error, **row)
        else:
            add("mcp.stdio", True, skipped=True, reason="--no-probe")

        # ---------------------------------------------------------------- host wiring scan
        from . import wire as wire_mod

        rows = []
        for scope in ("user", "project"):
            for r in wire_mod.status_rows(scope=scope, cwd=cfg.workspace, env=env):
                r["scope"] = scope
                rows.append(r)
        wired = [r for r in rows if r.get("wired")]
        add("wire", True, hosts=rows, wired_anywhere=bool(wired),
            hint=None if wired else "no host is wired to this install yet - run `sk wire`")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    ok = all(c["ok"] for c in checks)
    return {"schema": SCHEMA, "ok": ok, "checks": checks, "fixes": fixes}


def _scratch_toolkit(cfg: Config, scratch: str, env: dict[str, str]):
    """A toolkit for introspection only: its state (journal, ledger, profile cache,
    shell scratch, publish store) is redirected into `scratch` so the doctor never
    creates or mutates the operator's real state as a side effect of looking at it."""
    from .toolkit import build

    build_cfg = Config.load(path=None, cwd=cfg.cwd, env=env,
                            overrides={"state": {"dir": scratch, "profile_cache": True},
                                       "publish": {"store_path": os.path.join(scratch,
                                                                              "publish.json")}})
    return build(config=build_cfg)


# --------------------------------------------------------------------------- live probe
def probe_stdio(*, python: str | None = None, timeout: float = 60.0,
                env: dict[str, str] | None = None) -> dict[str, Any]:
    """Drive the real stdio server end to end in a self-contained temp workspace.

    This is the "exposed and callable" proof, executed, not asserted: if the server
    cannot start, cannot list tools, or cannot answer a read-only call, the doctor
    fails `mcp.stdio` with the stderr tail that says why."""
    python = python or sys.executable
    env = dict(os.environ) if env is None else dict(env)
    env.setdefault("PYTHONUNBUFFERED", "1")
    ws = tempfile.mkdtemp(prefix="sk-doctor-probe-")
    hello = os.path.join(ws, "hello.txt")
    with open(hello, "w", encoding="utf-8") as fh:
        fh.write("doctor probe\n")
    rep: dict[str, Any] = {"workspace": ws}
    t0 = time.monotonic()
    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen([python, "-m", "skeletonkey.mcp", "--cwd", ws],
                                cwd=ws, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                env=env, bufsize=1)
        lines: queue.Queue[str] = queue.Queue()
        err_q: list[str] = []
        threading.Thread(target=_pump, args=(proc.stdout, lines.put), daemon=True).start()
        threading.Thread(target=_pump, args=(proc.stderr, err_q.append), daemon=True).start()

        _request(proc, lines, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                               "params": {"protocolVersion": PROTOCOL, "capabilities": {},
                                          "clientInfo": {"name": "sk-doctor", "version": "1"}}},
                 deadline=t0 + timeout)
        _notify(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        listed = _request(proc, lines,
                          {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                          deadline=t0 + timeout)
        names = [t.get("name") for t in listed.get("tools", [])]
        rep["tools"] = len(names)
        meta = listed.get("_meta") or {}
        rep["digest"] = meta.get("sk.digest")
        rep["tier"] = meta.get("sk.tier")

        if "fs.stat" not in names:
            rep["ok"] = False
            rep["error"] = "fs.stat missing from tools/list"
            return rep
        call_t = time.monotonic()
        res = _request(proc, lines,
                       {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "fs.stat", "arguments": {"path": "hello.txt"}}},
                       deadline=t0 + timeout)
        rep["call_ms"] = round((time.monotonic() - call_t) * 1000)
        sc = res.get("structuredContent") or {}
        envelope = sc.get("envelope") or sc
        rep["call_ok"] = bool(envelope.get("ok", False))
        if not rep["call_ok"]:
            rep["error"] = f"fs.stat answered not-ok: {json.dumps(envelope)[:300]}"
        rep["ok"] = bool(rep["call_ok"])
        return rep
    except _ProbeFailure as exc:
        rep["ok"] = False
        rep["error"] = str(exc)
        if err_q:
            rep["stderr_tail"] = "".join(err_q)[-800:]
        return rep
    except Exception as exc:
        rep["ok"] = False
        rep["error"] = f"{type(exc).__name__}: {exc}"
        if err_q:
            rep["stderr_tail"] = "".join(err_q)[-800:]
        return rep
    finally:
        if proc is not None:
            try:
                if proc.stdin:
                    proc.stdin.close()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        if rep.get("ok"):
            shutil.rmtree(ws, ignore_errors=True)


class _ProbeFailure(Exception):
    pass


def _pump(fh: Any, sink: Any) -> None:
    """Reader thread: pipes + blocking readline, so no select() - portable to Windows."""
    try:
        for line in fh:
            sink(line)
    except (OSError, ValueError):
        pass


def _request(proc: subprocess.Popen, lines: Any, msg: dict, *, deadline: float) -> dict:
    """Send one JSON-RPC request; return the result for OUR id. Replies for other ids
    (server log notifications etc.) are dropped - the probe asserts only its own
    conversation, like any real host session."""
    import queue as _q

    if proc.stdin is None:
        raise _ProbeFailure("server stdin is closed")
    want = msg["id"]
    try:
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
    except (OSError, ValueError) as exc:
        raise _ProbeFailure(f"cannot write to server: {exc}") from exc
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            raise _ProbeFailure(f"timed out waiting for reply id={want}")
        try:
            line = lines.get(timeout=min(left, 0.5))
        except _q.Empty:
            if proc.poll() is not None:
                raise _ProbeFailure(f"server exited early (code {proc.returncode})") from None
            continue
        if not line.strip():
            continue
        try:
            reply = json.loads(line)
        except ValueError:
            continue                                    # a log line that leaked; ignore
        if reply.get("id") != want:
            continue
        if "error" in reply:
            raise _ProbeFailure(f"JSON-RPC error: {json.dumps(reply['error'])[:300]}")
        return reply.get("result") or {}


def _notify(proc: subprocess.Popen, msg: dict) -> None:
    if proc.stdin is not None:
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
