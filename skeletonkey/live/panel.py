"""The preview panel: a tiny HTTP server that turns renders into something
you can watch.

Routes (all JSON except the frame and the page):

  GET  /           the panel page (frame viewer, error overlay, REPL console)
  GET  /version    {version, error, programs} - the page polls this at ~300ms
  GET  /frame.svg  current frame for ?program=
  GET  /scene.json the scene graph as data (the hook a heavier 3D frontend -
                   react-three-fiber-style - would consume instead of the SVG)
  GET  /events     server-sent events: a bump on every render (fast path the
                   page uses when EventSource survives the network path)
  POST /repl       {program, code, mode} -> LiveREPL result (the in-page
                   console; only when [live] panel_repl is true)

Threading model: `ThreadingHTTPServer` on a daemon thread; every render bumps
a condition variable that SSE subscribers wait on. The polling route alone is
sufficient for the full experience - SSE just lowers latency when available.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class Panel:
    """Serves the live preview for a LiveManager. One panel per manager."""

    def __init__(self, manager: Any, host: str = "127.0.0.1", port: int = 8010,
                 *, repl_enabled: bool = True) -> None:
        self.manager = manager
        self.host = host
        self.repl_enabled = repl_enabled
        self._cond = threading.Condition()
        self._pin: str | None = None          # program the page is showing
        self.started_at = time.time()
        self.requests = 0
        self.sse_clients = 0
        self._server = ThreadingHTTPServer((host, int(port)), self._handler_class())
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ state
    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def url(self) -> str:
        shown_host = "127.0.0.1" if self.host in ("0.0.0.0", "", "::") else self.host
        return f"http://{shown_host}:{self.port}/"

    def start(self) -> dict[str, Any]:
        if self._thread and self._thread.is_alive():
            return self.status()
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="sk-live-panel", daemon=True,
                                        kwargs={"poll_interval": 0.4})
        self._thread.start()
        self.manager.add_frame_listener(self.bump)
        return self.status()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)
        self._thread = None
        with self._cond:
            self._cond.notify_all()

    def status(self) -> dict[str, Any]:
        return {"serving": bool(self._thread and self._thread.is_alive()),
                "host": self.host, "port": self.port, "url": self.url,
                "repl_enabled": self.repl_enabled, "requests": self.requests,
                "sse_clients": self.sse_clients}

    def bump(self) -> None:
        """A render happened somewhere; wake SSE clients."""
        with self._cond:
            self._cond.notify_all()

    # --------------------------------------------------------------- snapshots
    def _default_program(self) -> Any:
        programs = self.manager.programs
        if self._pin in programs:
            return programs[self._pin]
        if len(programs) == 1:
            return next(iter(programs.values()))
        return None

    # ------------------------------------------------------ agent debugger data
    def _registries(self, prog: Any) -> dict[str, list[str]]:
        """Dicts-of-callables in a program namespace ARE its agent/handler
        registries (the blueprint's AGENT_REGISTRY / TOOL_REGISTRY pattern).
        Surfaced, never invoked, until the REPL is used on purpose."""
        import inspect as _inspect

        out: dict[str, list[str]] = {}
        with prog.lock:
            for name, value in prog.ns.items():
                if name.startswith("__") or not isinstance(value, dict) or not value:
                    continue
                if all(callable(v) or _inspect.isclass(v) for v in value.values()):
                    entries = []
                    for k, v in sorted(value.items(), key=lambda kv: str(kv[0])):
                        entries.append(f"{k} -> {getattr(v, '__qualname__', type(v).__name__)}")
                    out[name] = entries
        return out

    def _engine_activity(self, limit: int = 30) -> list[dict[str, Any]]:
        """Recent tool calls from the ledger tail - the engine's own activity
        feed. Best-effort JSONL read; the debugger shows an empty rail rather
        than an error when no engine/ledger is attached."""
        eng = getattr(self.manager, "_engine", None)
        ledger = getattr(eng, "_ledger", None)
        path = getattr(ledger, "path", None)
        if not path or not os.path.isfile(path):
            return []
        try:
            rows: list[dict[str, Any]] = []
            with open(path, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 64_000))
                tail = fh.read().decode("utf-8", "replace")
            for line in tail.splitlines()[1 if size > 64_000 else 0:]:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                rows.append({"tool": row.get("tool", row.get("tool_name", "?")),
                             "ok": row.get("ok"),
                             "ms": row.get("duration_ms"),
                             "at": row.get("at", row.get("ts", ""))})
            return rows[-limit:][::-1]
        except OSError:
            return []

    def _state_payload(self, prog: Any) -> dict[str, Any]:
        try:
            base = prog.state_view()
        except Exception:                       # pragma: no cover - defensive
            base = {"names": {}}
        with prog.lock:
            return {**base,
                    "frame": {"version": prog.frame_version, "error": prog.frame_error,
                              "render_ms": prog.render_ms},
                    "hooks": [h for h in ("render", "tick", "setup") if callable(prog.ns.get(h))],
                    "registries": self._registries(prog)}

    # ------------------------------------------------------------------ routes
    def _pick(self, pid: str | None) -> Any:
        programs = self.manager.programs
        if pid:
            return programs.get(pid)
        return self._default_program()

    def _version_payload(self) -> dict[str, Any]:
        programs = self.manager.programs
        total = sum(p.frame_version for p in programs.values())
        current = self._default_program()
        return {
            "version": total,
            "count": len(programs),
            "programs": [{"id": p.id, "file": p.display, "frame_version": p.frame_version,
                          "reloads": p.reloads, "failed_reloads": p.failed_reloads,
                          "repl_count": p.repl_count, "render_ms": p.render_ms,
                          "error": bool(p.frame_error)} for p in programs.values()],
            "current": current.id if current else None,
            "error": current.frame_error if current else None,
            "watch": (self.manager.watch_status() or {}),
            "uptime_s": round(time.time() - self.started_at, 1),
        }

    # ----------------------------------------------------------------- server
    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        panel = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "SkeletonKeyLive/1"
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:  # quiet by design
                return

            # ------------------------------------------------------------ util
            def _send(self, status: int, body: bytes, ctype: str,
                      extra: dict[str, str] | None = None) -> None:
                panel.requests += 1
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _json(self, status: int, obj: Any) -> None:
                data = json.dumps(obj, default=str).encode("utf-8", "replace")
                self._send(status, data, "application/json; charset=utf-8")

            def _param(self, name: str) -> str | None:
                from urllib.parse import parse_qs, urlparse

                qs = parse_qs(urlparse(self.path).query)
                vals = qs.get(name)
                return vals[0] if vals else None

            # -------------------------------------------------------------- GET
            def do_GET(self) -> None:
                from urllib.parse import urlparse

                route = urlparse(self.path).path.rstrip("/") or "/"
                if route == "/":
                    panel._pin = None
                    self._send(200, _PAGE.encode("utf-8"), "text/html; charset=utf-8")
                elif route == "/view3d":
                    self._send(200, _PAGE_3D.encode("utf-8"), "text/html; charset=utf-8")
                elif route == "/agents":
                    self._send(200, _PAGE_AGENTS.encode("utf-8"), "text/html; charset=utf-8")
                elif route == "/version":
                    self._json(200, panel._version_payload())
                elif route == "/frame.svg":
                    prog = panel._pick(self._param("program"))
                    if prog is None:
                        self._send(200, _EMPTY_SVG, "image/svg+xml")
                        return
                    with prog.lock:
                        svg = prog.frame_svg or _EMPTY_SVG.decode()
                    self._send(200, svg.encode("utf-8", "replace"), "image/svg+xml")
                elif route == "/scene.json":
                    prog = panel._pick(self._param("program"))
                    obj = prog.canvas.to_dict() if prog else {"nodes": [], "version": 0}
                    self._json(200, obj)
                elif route == "/state":
                    prog = panel._pick(self._param("program"))
                    self._json(200, panel._state_payload(prog) if prog else {"names": {}})
                elif route == "/api/program":
                    prog = panel._pick(self._param("program"))
                    if prog is None:
                        self._json(404, {"error": "no live program",
                                         "programs": sorted(panel.manager.programs)})
                        return
                    self._json(200, {**prog.status(),
                                     "registries": panel._registries(prog)})
                elif route == "/api/history":
                    pid = self._param("program")
                    try:
                        self._json(200, panel.manager.history(pid, limit=40))
                    except Exception as exc:
                        self._json(404, {"error": str(exc)})
                elif route == "/api/activity":
                    self._json(200, {"engine": panel._engine_activity()})
                elif route == "/events":
                    self._sse()
                else:
                    self._json(404, {"error": "no such route", "route": route})

            def _sse(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                panel.sse_clients += 1
                last = -1
                try:
                    while True:
                        with panel._cond:
                            panel._cond.wait(timeout=15.0)
                        payload = panel._version_payload()
                        if payload["version"] == last:
                            # heartbeat keeps proxies from reaping the stream
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                            continue
                        last = payload["version"]
                        data = json.dumps(payload, default=str)
                        self.wfile.write(f"event: frame\ndata: {data}\n\n".encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    panel.sse_clients -= 1

            # ------------------------------------------------------------- POST
            def do_POST(self) -> None:
                from urllib.parse import urlparse

                route = urlparse(self.path).path.rstrip("/")
                if route == "/api/control":
                    self._control()
                    return
                if route != "/repl":
                    self._json(404, {"error": "no such route"})
                    return
                if not panel.repl_enabled:
                    self._json(403, {"error": "panel REPL disabled ([live] panel_repl = false)"})
                    return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                if length > 64_000:
                    self._json(413, {"error": "repl payload too large"})
                    return
                try:
                    body = json.loads(self.rfile.read(max(0, length)) or b"{}")
                except ValueError:
                    self._json(400, {"error": "body must be JSON"})
                    return
                code = str(body.get("code", ""))
                if not code.strip():
                    self._json(400, {"error": "code is required"})
                    return
                prog = panel._pick(body.get("program"))
                if prog is None:
                    self._json(404, {"error": "no live program",
                                     "programs": sorted(panel.manager.programs)})
                    return
                guard = float(panel.manager._cfg("exec_guard_s", 10.0))
                max_out = int(panel.manager._cfg("repl_max_output_bytes", 16_000))
                result = prog.repl(code, mode=str(body.get("mode", "auto")),
                                   render=bool(body.get("render", True)),
                                   guard_s=guard, max_out=max_out,
                                   frame_cb=panel.manager.fanout_frame)
                panel.bump()
                self._json(200, result)

            def _control(self) -> None:
                """HMR controls for the debugger page: reload/stop a program,
                force_source passed through. Same code path as live.reload /
                live.stop - the panel is a client, not a back door."""
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(max(0, length)) or b"{}")
                except (ValueError, TypeError):
                    self._json(400, {"error": "body must be JSON"})
                    return
                action = str(body.get("action", ""))
                pid = body.get("program")
                try:
                    if action == "reload":
                        prog = panel.manager.get(pid)
                        rep = prog.reload(reason="panel",
                                          force_source=body.get("force_source") or None)
                        panel.bump()
                        self._json(200, {**rep.to_dict(), "program": prog.id})
                    elif action == "stop":
                        panel.manager.stop(pid)
                        panel.bump()
                        self._json(200, {"stopped": True, "program": pid})
                    else:
                        self._json(400, {"error": f"unknown action {action!r}",
                                         "actions": ["reload", "stop"]})
                except Exception as exc:
                    code = getattr(exc, "code", None)
                    self._json(400 if code == "BAD_ARGS" else 404, {"error": str(exc)})

        return Handler


_EMPTY_SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" width="420" height="320">'
              b'<rect width="420" height="320" fill="#0d1117"/>'
              b'<text x="210" y="160" fill="#8b949e" font-size="13" text-anchor="middle" '
              b'font-family="monospace">no live program - sk live start path/to/app.py</text></svg>')


# ---------------------------------------------------------------- the page
# Deliberately dependency-free: polling /version at ~300ms IS the transport
# (reliable through any proxy), EventSource upgrades to push when it can.
_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LiveREPL &middot; SkeletonKey HMR</title>
<style>
  :root { --bg:#0d1117; --panel:#161b22; --line:#30363d; --fg:#e6edf3; --dim:#8b949e;
          --accent:#58a6ff; --good:#3fb950; --bad:#f85149; --warn:#d29922; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
  header { display:flex; gap:14px; align-items:center; padding:8px 14px;
           background:var(--panel); border-bottom:1px solid var(--line); flex-wrap:wrap; }
  .badge { padding:1px 8px; border-radius:10px; font-size:11px; border:1px solid var(--line); }
  .live  { color:var(--good); border-color:var(--good); }
  .dead  { color:var(--dim); }
  .err   { color:var(--bad);  border-color:var(--bad); }
  .meta  { color:var(--dim); }
  select,button,input { background:#0d1117; color:var(--fg); border:1px solid var(--line);
           border-radius:6px; font:inherit; padding:4px 8px; }
  button:hover { border-color:var(--accent); cursor:pointer; }
  #overlay { display:none; margin:0; padding:10px 14px; background:rgba(248,81,73,.12);
             border-bottom:1px solid var(--bad); color:#ffa198; white-space:pre-wrap; }
  #stage { display:flex; justify-content:center; padding:18px 10px 6px; }
  #frame { background:#000; border:1px solid var(--line); border-radius:8px;
           overflow:hidden; box-shadow:0 8px 30px rgba(0,0,0,.5); }
  #frame svg { display:block; }
  #repl { margin:8px auto 0; width:min(760px,94vw); }
  #log { height:170px; overflow-y:auto; background:var(--panel); border:1px solid var(--line);
         border-radius:8px 8px 0 0; padding:8px 10px; }
  #log .in  { color:var(--accent); }
  #log .out { color:var(--fg); white-space:pre-wrap; }
  #log .e   { color:var(--bad); white-space:pre-wrap; }
  #log .sys { color:var(--dim); font-style:italic; }
  #bar { display:flex; gap:6px; }
  #code { flex:1; border-radius:0 0 0 8px; border-top:none; }
  #run  { border-radius:0 0 8px 0; border-top:none; border-left:none; }
  #hint { color:var(--dim); padding:6px 2px; font-size:11px; }
</style>
</head>
<body>
<header>
  <strong>&#9635; LiveREPL</strong>
  <span id="lamp" class="badge dead">&bull; connecting</span>
  <select id="prog" title="program"></select>
  <span id="file" class="meta"></span>
  <span id="stats" class="meta"></span>
  <span id="watch" class="meta"></span>
  <span style="flex:1"></span>
  <a href="/view3d" style="color:var(--accent)">3D</a>
  <a href="/agents" style="color:var(--accent)">debugger</a>
</header>
<pre id="overlay"></pre>
<div id="stage"><div id="frame"></div></div>
<div id="repl">
  <div id="log"></div>
  <div id="bar">
    <input id="code" placeholder="live repl &mdash; try:  color = &quot;#f2cc60&quot;   or   canvas.orbit(theta=1.2)" autofocus>
    <button id="run">run &#9166;</button>
  </div>
  <div id="hint">state mutates in place &middot; edits to the file hot-patch through the watcher &middot;
    &uarr; recalls history</div>
</div>
<script>
const frame = document.getElementById('frame'), log = document.getElementById('log');
const code = document.getElementById('code'), lamp = document.getElementById('lamp');
const overlay = document.getElementById('overlay'), progSel = document.getElementById('prog');
let version = -1, current = null, hist = [], hi = 0;

function metaLine(v) {
  const p = (v.programs||[]).find(p => p.id === current) || {};
  document.getElementById('file').textContent  = p.file || '(no program)';
  document.getElementById('stats').textContent =
    p.id ? `reloads ${p.reloads}${p.failed_reloads? ' failed:'+p.failed_reloads : ''} · repl ${p.repl_count} · frame v${p.frame_version} · ${p.render_ms}ms` : '';
  const w = v.watch || {};
  document.getElementById('watch').textContent = w.watching ? `watch:${w.backend}` : 'watch:off';
  const anyErr = v.error && v.error.message;
  if (anyErr) {
    overlay.style.display = 'block';
    overlay.textContent = '⚠ ' + (v.error.message || '') + (v.error.trace ? '\n\n' + v.error.trace : '');
  } else overlay.style.display = 'none';
}
async function refreshFrame() {
  const r = await fetch('/frame.svg' + (current ? '?program='+current : ''));
  frame.innerHTML = await r.text();
}
async function poll() {
  try {
    const v = await (await fetch('/version')).json();
    lamp.className = 'badge live'; lamp.innerHTML = '&bull; live';
    const ids = (v.programs||[]).map(p => p.id);
    if (JSON.stringify(ids) !== progSel.dataset.ids) {
      progSel.dataset.ids = JSON.stringify(ids);
      progSel.innerHTML = ids.map(i => `<option ${i===v.current?'selected':''}>${i}</option>`).join('');
    }
    if (!current) current = v.current;
    metaLine(v);
    if (v.version !== version) { version = v.version; await refreshFrame(); }
  } catch (e) {
    lamp.className = 'badge dead'; lamp.innerHTML = '&bull; offline';
  }
  setTimeout(poll, 300);
}
progSel.onchange = () => { current = progSel.value; version = -1; pollOnce(); };
async function pollOnce(){ const v = await (await fetch('/version')).json(); version = -1; current=current||v.current; }

function put(cls, text) {
  const div = document.createElement('div');
  div.className = cls; div.textContent = text;
  log.appendChild(div); log.scrollTop = log.scrollHeight;
}
async function run(codeText) {
  put('in', '» ' + codeText);
  hist.push(codeText); hi = hist.length;
  try {
    const body = {code: codeText}; if (current) body.program = current;
    const r = await (await fetch('/repl', {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)})).json();
    if (r.error) put('e', (r.error.code ? r.error.code+': ' : '') + r.error.message);
    if (r.stdout) put('out', r.stdout.replace(/\n$/,''));
    if (r.value !== undefined) put('out', r.value);
    if (r.frame && r.frame.error) put('e', 'render: ' + r.frame.error.message);
    version = -1;  // next poll redraws immediately
  } catch (e) { put('e', String(e)); }
}
document.getElementById('run').onclick = () => { const t = code.value; code.value=''; if(t.trim()) run(t); };
code.addEventListener('keydown', e => {
  if (e.key === 'Enter') { const t = code.value; code.value=''; if(t.trim()) run(t); }
  else if (e.key === 'ArrowUp') { if (hi>0) { hi--; code.value = hist[hi]||''; e.preventDefault(); } }
  else if (e.key === 'ArrowDown') { if (hi<hist.length) { hi++; code.value = hist[hi]||''; e.preventDefault(); } }
});
// push upgrade when SSE works; polling stays the fallback
try {
  const es = new EventSource('/events');
  es.addEventListener('frame', ev => { const v = JSON.parse(ev.data); metaLine(v);
    if (v.version !== version) { version = v.version; refreshFrame(); } });
} catch (e) { /* polling covers it */ }
put('sys', 'panel ready · edit the file or talk to it here · `help()` in the repl for names');
poll();
</script>
</body>
</html>
"""


# ------------------------------------------------------------- the 3D page
# Blueprint Option C: a persistent render loop over a registry of view data,
# hot-swapped by version polling. Zero-dependency soft renderer (perspective,
# painter's sort, key-light lambert, pointer orbit) - offline by design.
_PAGE_3D = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>LiveREPL &middot; 3D preview</title>
<style>
  :root { --bg:#0d1117; --panel:#161b22; --line:#30363d; --fg:#e6edf3; --dim:#8b949e;
          --accent:#58a6ff; --good:#3fb950; --bad:#f85149; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; overflow:hidden; }
  header { display:flex; gap:14px; align-items:center; padding:8px 14px;
           background:var(--panel); border-bottom:1px solid var(--line); }
  a { color:var(--accent); text-decoration:none; }
  .badge { padding:1px 8px; border-radius:10px; font-size:11px; border:1px solid var(--line); }
  .live { color:var(--good); border-color:var(--good); } .dead { color:var(--dim); }
  .meta { color:var(--dim); }
  #cv { display:block; width:100vw; height:calc(100vh - 42px); cursor:grab; }
  #cv:active { cursor:grabbing; }
  #hint { position:fixed; left:12px; bottom:10px; color:var(--dim); font-size:11px; }
  #overlay { display:none; position:fixed; top:42px; left:0; right:0; padding:10px 14px;
             background:rgba(248,81,73,.14); border-bottom:1px solid var(--bad); color:#ffa198;
             white-space:pre-wrap; }
</style>
</head>
<body>
<header>
  <strong>&#9635; Live 3D</strong>
  <span id="lamp" class="badge dead">&bull; connecting</span>
  <span id="file" class="meta"></span><span id="stats" class="meta"></span>
  <span style="flex:1"></span>
  <label class="meta"><input type="checkbox" id="spin"> auto-spin</label>
  <a href="/">2D frame</a> <a href="/agents">debugger</a>
</header>
<pre id="overlay"></pre>
<canvas id="cv"></canvas>
<div id="hint">drag to orbit &middot; wheel to zoom &middot; scene hot-swaps as the program renders (mesh3d/cube3d/poly3d/point3d)</div>
<script>
const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
const overlay = document.getElementById('overlay'), lamp = document.getElementById('lamp');
let W = 0, H = 0;
function fit(){ W = cv.width = cv.clientWidth * devicePixelRatio; H = cv.height = cv.clientHeight * devicePixelRatio; }
addEventListener('resize', fit); fit();

// ---- camera state (LOCAL view control; the program's own camera stays canonical)
let cam = { theta: .65, phi: .62, dist: 4.2, fov: 520 };
drag: { let dragging=false, px=0, py=0;
  cv.addEventListener('pointerdown', e=>{dragging=true;px=e.clientX;py=e.clientY;cv.setPointerCapture(e.pointerId);});
  cv.addEventListener('pointermove', e=>{ if(!dragging) return;
    cam.theta += (e.clientX-px)*.008; cam.phi = Math.max(-1.45, Math.min(1.45, cam.phi+(e.clientY-py)*.008));
    px=e.clientX; py=e.clientY; });
  cv.addEventListener('pointerup',   ()=>dragging=false);
  cv.addEventListener('wheel', e=>{ e.preventDefault(); cam.dist = Math.max(1.2, Math.min(20, cam.dist*(1+Math.sign(e.deltaY)*.08))); });
}
document.getElementById('spin').onchange = e => autoSpin = e.target.checked;
let autoSpin = false;

let scene = {nodes:[]}, sceneVer = -1;
const norm = v => { const m = Math.hypot(v[0],v[1],v[2])||1; return [v[0]/m,v[1]/m,v[2]/m]; };
const cross = (a,b,c) => norm([(b[1]-a[1])*(c[2]-a[2])-(b[2]-a[2])*(c[1]-a[1]),
                               (b[2]-a[2])*(c[0]-a[0])-(b[0]-a[0])*(c[2]-a[2]),
                               (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])]);
const shade = (hex, k) => { const h = (hex||'').replace('#',''); if (h.length!==6) return hex||'#58a6ff';
  const r=parseInt(h.slice(0,2),16)*k|0, g=parseInt(h.slice(2,4),16)*k|0, b=parseInt(h.slice(4,6),16)*k|0;
  return `rgb(${r},${g},${b})`; };
const scale = 0.012;          // scene units (program coordinates) -> view units
const cubeVerts = (cx,cy,cz,s,spinDeg) => {
  const h = s/2, sp = (spinDeg||0)*Math.PI/180, co=Math.cos(sp), si=Math.sin(sp), out=[];
  for (const dx of [-h,h]) for (const dy of [-h,h]) for (const dz of [-h,h])
    out.push([(cx + dx*co + dz*si)*scale, (cy+dy)*scale, (cz - dx*si + dz*co)*scale]);
  return out; };
const CUBE_FACES = [[0,1,3,2],[4,6,7,5],[0,4,5,1],[2,3,7,6],[0,2,6,4],[1,5,7,3]];

function collect() {
  const items = [];
  for (const n of scene.nodes||[]) {
    if (n.type === 'cube3d') {
      ready(cubeVerts(n.cx, n.cy, n.cz, n.size, n.spin), CUBE_FACES, n.fill||'#58a6ff', n);
    } else if (n.type === 'mesh3d') {
      ready(n.vertices.map(v=>[v[0]*scale, v[1]*scale, v[2]*scale]), n.faces, n.fill||'#58a6ff', n);
    } else if (n.type === 'poly3d') {
      items.push({line: n.points.map(p=>[p[0]*scale,p[1]*scale,p[2]*scale]),
                  close: !!n.close, stroke: n.stroke||'#58a6ff', width: n.stroke_width||1.2});
    } else if (n.type === 'point3d') {
      items.push({dot: [n.x*scale, n.y*scale, n.z*scale], r: n.r||3, fill: n.fill||'#f2cc60'});
    }
  }
  function ready(verts, faces, fill, n) {
    items.push({verts, faces: faces||[], fill, stroke: n.stroke, sw: n.stroke_width||0, shadeOn: n.shade!==false});
  }
  return items;
}

function rot(p){ const {theta, phi} = cam;
  const xr = p[0]*Math.cos(theta)+p[2]*Math.sin(theta), zr = -p[0]*Math.sin(theta)+p[2]*Math.cos(theta);
  const yr = p[1]*Math.cos(phi)-zr*Math.sin(phi),      zv = p[1]*Math.sin(phi)+zr*Math.cos(phi);
  return [xr, yr, zv]; }
function proj(p){ const [x,y,z] = rot(p);
  const f = cam.fov*devicePixelRatio / (cam.dist + z);
  return [W/2 + x*f, H/2 + y*f, z, f]; }

const LIGHT = norm([-0.4,-0.7,-0.6]);
function draw(){
  requestAnimationFrame(draw);
  if (autoSpin) cam.theta += .006;
  ctx.fillStyle = '#0d1117'; ctx.fillRect(0,0,W,H);
  // reference grid + axes
  ctx.strokeStyle = '#21262d'; ctx.lineWidth = 1;
  for (let i=-8;i<=8;i++){
    let a=proj([i*.5, 1.2, -4]), b=proj([i*.5, 1.2, 4]);
    ctx.beginPath(); ctx.moveTo(a[0],a[1]); ctx.lineTo(b[0],b[1]); ctx.stroke();
    a=proj([-4, 1.2, i*.5]); b=proj([4, 1.2, i*.5]);
    ctx.beginPath(); ctx.moveTo(a[0],a[1]); ctx.lineTo(b[0],b[1]); ctx.stroke();
  }
  const solids = [], wires = [];
  for (const it of collect()) {
    if (it.verts) {
      const v = it.verts.map(rot);
      for (const f of it.faces) {
        if (f.length < 2) continue;
        const vv = f.map(i=>it.verts[i]);
        const depth = f.reduce((s,i)=>s+v[i][2],0)/f.length;
        const nrm = f.length>=3 ? cross(vv[0],vv[1],vv[2]) : [0,0,-1];
        const inten = Math.max(.18, Math.min(1, -(nrm[0]*LIGHT[0]+nrm[1]*LIGHT[1]+nrm[2]*LIGHT[2])));
        solids.push({depth, f, pts: f.map(i=>proj(it.verts[i])), inten, it});
      }
    } else wires.push(it);
  }
  solids.sort((a,b)=>b.depth-a.depth);
  for (const s of solids) {
    ctx.beginPath();
    s.pts.forEach((p,i)=> i? ctx.lineTo(p[0],p[1]) : ctx.moveTo(p[0],p[1]));
    ctx.closePath();
    ctx.fillStyle = s.it.shadeOn ? shade(s.it.fill, s.inten) : s.it.fill;
    ctx.fill();
    if (s.it.stroke){ ctx.strokeStyle = s.it.stroke; ctx.lineWidth = (s.it.sw||.8)*devicePixelRatio*.8; ctx.stroke(); }
  }
  for (const w of wires) {
    if (w.line) { const pts = w.line.map(proj); if (w.close) pts.push(pts[0]);
      ctx.strokeStyle = w.stroke; ctx.lineWidth = (w.width||1.2)*devicePixelRatio*.8;
      ctx.beginPath(); pts.forEach((p,i)=> i? ctx.lineTo(p[0],p[1]) : ctx.moveTo(p[0],p[1])); ctx.stroke(); }
    if (w.dot) { const p = proj(w.dot);
      ctx.fillStyle = w.fill; ctx.beginPath();
      ctx.arc(p[0], p[1], Math.max(1.5, (w.r*devicePixelRatio*p[3])/900), 0, 7); ctx.fill(); }
  }
}
async function poll(){
  try {
    const v = await (await fetch('/version')).json();
    lamp.className='badge live'; lamp.innerHTML='&bull; live';
    const cur = v.programs && v.programs.find(p=>p.id===v.current);
    document.getElementById('file').textContent = cur ? cur.file : '(no program)';
    document.getElementById('stats').textContent = cur ? `frame v${cur.frame_version} · scene v${sceneVer}` : '';
    const anyErr = v.error && v.error.message;
    overlay.style.display = anyErr ? 'block' : 'none';
    if (anyErr) overlay.textContent = '⚠ ' + v.error.message;
    const sc = await (await fetch('/scene.json')).json();
    if (sc.version !== sceneVer) { scene = sc; sceneVer = sc.version; }
  } catch(e){ lamp.className='badge dead'; lamp.innerHTML='&bull; offline'; }
  setTimeout(poll, 250);
}
poll(); draw();
</script>
</body>
</html>
"""


# ------------------------------------------------------- the debugger page
# The LiveREPL-powered agent debugger: live state table, agent/tool
# registries (dict-of-callables), HMR controls, in-page REPL, and the
# engine's own activity rail (ledger tail). Polling-driven, zero-dependency.
_PAGE_AGENTS = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>LiveREPL &middot; agent debugger</title>
<style>
  :root { --bg:#0d1117; --panel:#161b22; --line:#30363d; --fg:#e6edf3; --dim:#8b949e;
          --accent:#58a6ff; --good:#3fb950; --bad:#f85149; --warn:#d29922; --purp:#bc8cff; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; height:100vh;
         display:flex; flex-direction:column; }
  header { display:flex; gap:14px; align-items:center; padding:8px 14px;
           background:var(--panel); border-bottom:1px solid var(--line); }
  a { color:var(--accent); text-decoration:none; }
  .badge { padding:1px 8px; border-radius:10px; font-size:11px; border:1px solid var(--line); }
  .live { color:var(--good); border-color:var(--good); } .dead { color:var(--dim); }
  .meta { color:var(--dim); }
  main { flex:1; display:grid; grid-template-columns:260px 1fr 340px; min-height:0; }
  .col { border-right:1px solid var(--line); overflow-y:auto; padding:8px 10px; }
  .col:last-child { border-right:none; }
  h3 { font-size:11px; text-transform:uppercase; letter-spacing:.12em; color:var(--dim);
       margin:10px 0 6px; }
  .pcard { background:var(--panel); border:1px solid var(--line); border-radius:8px;
           padding:8px 10px; margin-bottom:8px; }
  .pcard.sel { border-color:var(--accent); }
  .pcard .pname { color:var(--fg); font-weight:600; cursor:pointer; }
  .pcard .row { color:var(--dim); font-size:12px; margin-top:2px; }
  button { background:#0d1117; color:var(--fg); border:1px solid var(--line); border-radius:6px;
           font:inherit; font-size:11.5px; padding:2px 9px; cursor:pointer; margin-right:4px; }
  button:hover { border-color:var(--accent); }
  button.danger:hover { border-color:var(--bad); color:var(--bad); }
  table.state { width:100%; border-collapse:collapse; font-size:12.5px; }
  table.state td { padding:2.5px 6px; border-bottom:1px solid #21262d; vertical-align:top; }
  td.name { color:var(--accent); white-space:nowrap; }
  td.typ  { color:var(--purp); }
  td.val  { color:var(--fg); word-break:break-all; }
  .own { font-size:10px; padding:0 6px; border-radius:8px; border:1px solid var(--line);
         color:var(--dim); white-space:nowrap; }
  .own.repl { color:var(--warn); border-color:var(--warn); }
  .own.keep-list { color:var(--purp); border-color:var(--purp); }
  .own.source { color:var(--dim); }
  .reg { margin-bottom:8px; }
  .reg .rname { color:var(--good); }
  .reg .entry { padding:1px 0 1px 12px; color:var(--fg); font-size:12.5px; cursor:pointer; }
  .reg .entry:hover { color:var(--accent); }
  #log { height:220px; overflow-y:auto; background:var(--panel); border:1px solid var(--line);
         border-radius:8px 8px 0 0; padding:6px 10px; font-size:12.5px; }
  #log .in { color:var(--accent); } #log .out { white-space:pre-wrap; }
  #log .e { color:var(--bad); white-space:pre-wrap; } #log .sys { color:var(--dim); font-style:italic; }
  #bar { display:flex; }
  #code { flex:1; background:#0d1117; color:var(--fg); border:1px solid var(--line);
          border-top:none; border-radius:0 0 0 8px; font:inherit; padding:5px 9px; }
  #run { border-top:none; border-left:none; border-radius:0 0 8px 0; }
  #activity .arow { padding:2px 2px; font-size:12px; color:var(--dim); border-bottom:1px solid #161b22; }
  #activity .arow b { color:var(--fg); font-weight:500; }
  #activity .arow.fail b { color:var(--bad); }
  .hist { color:var(--dim); font-size:12px; padding:1px 2px; }
  .hist.bad { color:var(--bad); }
  #overlay { display:none; padding:8px 14px; background:rgba(248,81,73,.14);
             border-bottom:1px solid var(--bad); color:#ffa198; white-space:pre-wrap; }
</style>
</head>
<body>
<header>
  <strong>&#9635; agent debugger</strong>
  <span id="lamp" class="badge dead">&bull; connecting</span>
  <span id="watch" class="meta"></span>
  <span style="flex:1"></span>
  <a href="/">2D frame</a> <a href="/view3d">3D</a>
</header>
<pre id="overlay"></pre>
<main>
  <div class="col" id="programs"><h3>programs</h3><div id="plist"></div>
    <h3>watcher</h3><div id="winfo" class="meta"></div></div>
  <div class="col"><h3>live state <span class="meta" id="sprog"></span></h3>
    <div id="state"></div>
    <h3>registries <span class="meta">(agents / tools / handlers)</span></h3>
    <div id="regs"></div>
    <h3>patch log</h3><div id="plog"></div></div>
  <div class="col">
    <h3>repl</h3>
    <div id="log"></div>
    <div id="bar"><input id="code" placeholder="mutate the live program&hellip; (Enter runs, &uarr; history)"><button id="run">&#9166;</button></div>
    <h3>engine activity <span class="meta">(ledger tail)</span></h3>
    <div id="activity"></div>
  </div>
</main>
<script>
const $ = id => document.getElementById(id);
let current = null, hist = [], hi = 0, replN = -1;
const esc = s => String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

function onerr(e){ overlay.style.display='block'; overlay.textContent='⚠ '+e; }
overlayOn = false;

async function pollPrograms(){
  try {
    const v = await (await fetch('/version')).json();
    $('lamp').className='badge live'; $('lamp').innerHTML='&bull; live';
    const w = v.watch||{}; $('watch').textContent = w.watching ? `watcher:${w.backend} · ${ (w.targets||[]).length } targets` : 'watcher off';
    $('plist').innerHTML = (v.programs||[]).map(p => `
      <div class="pcard ${p.id===current?'sel':''}" data-p="${p.id}">
        <div class="pname" onclick="sel('${p.id}')">${esc(p.id)} ${p.error?'<span style="color:var(--bad)">●</span>':''}</div>
        <div class="row">${esc(p.file)}</div>
        <div class="row">reloads ${p.reloads}${p.failed_reloads?` <span style="color:var(--bad)">+${p.failed_reloads} failed</span>`:''} · repl ${p.repl_count} · ${p.render_ms}ms</div>
        <div class="row" style="margin-top:5px">
          <button onclick="ctl('reload','${p.id}')">reload</button>
          <button class="danger" onclick="ctl('stop','${p.id}')">stop</button>
        </div>
      </div>`).join('') || '<div class="meta">no live programs<br><br><code>sk live start app.py</code></div>';
    if (!current && v.current) current = v.current;
    if (v.error && v.error.message) onerr(v.error.message + (v.error.trace ? '\n\n'+v.error.trace : ''));
    else $('overlay').style.display='none';
  } catch(e){ $('lamp').className='badge dead'; $('lamp').innerHTML='&bull; offline'; }
  setTimeout(pollPrograms, 700);
}
function sel(p){ current = p; dirtyState(); }

async function ctl(action, p){
  await fetch('/api/control', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({action, program:p})});
  dirtyState();
}

async function pollState(){
  if (current) {
    try {
      const s = await (await fetch('/state?program='+current)).json();
      $('sprog').textContent = '· '+current;
      const rows = Object.entries(s.names||{}).map(([k,r]) => r.missing ? '' :
        `<tr><td class="name">${esc(k)}</td><td class="typ">${esc(r.type||'')}</td>` +
        `<td class="val">${esc(r.repr||'')}</td>` +
        `<td><span class="own ${r.owned_by||''}">${r.owned_by||''}</span></td></tr>`).join('');
      $('state').innerHTML = '<table class="state">'+rows+'</table>';
      $('regs').innerHTML = Object.entries(s.registries||{}).map(([name, entries]) =>
        `<div class="reg"><div class="rname">${esc(name)}</div>` +
        entries.map(e => {
          const m = e.match(/^([\w.-]+) -> (.+)$/); const key = m? m[1] : e;
          return `<div class="entry" title="invoke via repl" onclick="invoke('${name}','${key}')">&#9656; ${esc(e)}</div>`;
        }).join('') + '</div>').join('') || '<div class="meta">no dict-of-callable registries</div>';
    } catch(e){}
  }
  try {
    const h = await (await fetch('/api/history'+(current?'?program='+current:''))).json();
    $('plog').innerHTML = (h.patches||[]).slice(-8).reverse().map(p =>
      `<div class="hist ${p.ok===false?'bad':''}">#${p.at?new Date(p.at*1000).toLocaleTimeString():''} ${p.reason||''} ` +
      `${p.ok===false?'FAIL '+(p.error&&p.error.message||''):(p.patched_functions||p.patched_methods||p.added||p.preserved)? JSON.stringify({patched:p.patched_functions, added:p.added, preserved:p.preserved, updated:p.data_updated}) : 'no changes'}</div>`).join('') || '<div class="meta">no patches yet</div>';
    if (h.repl && h.repl.length !== replN) {
      replN = h.repl.length;
    }
  } catch(e){}
  setTimeout(pollState, 800);
}
let dirtyT = null;
function dirtyState(){ clearTimeout(dirtyT); dirtyT = setTimeout(()=>{}, 0); }

async function pollActivity(){
  try {
    const a = await (await fetch('/api/activity')).json();
    $('activity').innerHTML = (a.engine||[]).map(r =>
      `<div class="arow ${r.ok===false?'fail':''}"><b>${esc(r.tool)}</b> ${r.ms!=null?esc(r.ms)+'ms':''}</div>`
    ).join('') || '<div class="meta">(no ledger attached)</div>';
  } catch(e){}
  setTimeout(pollActivity, 1500);
}

function put(cls, text){ const d=document.createElement('div'); d.className=cls; d.textContent=text;
  $('log').appendChild(d); $('log').scrollTop=$('log').scrollHeight; }
async function invoke(reg, key){
  run(`${reg}[${JSON.stringify(key)}]()`);
}
async function run(t){
  put('in','» '+t); hist.push(t); hi=hist.length;
  try {
    const body={code:t}; if(current) body.program=current;
    const r = await (await fetch('/repl',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)})).json();
    if (r.error) put('e',(r.error.code?r.error.code+': ':'')+r.error.message);
    if (r.stdout) put('out', r.stdout.replace(/\n$/,''));
    if (r.value!==undefined) put('out', r.value);
    if (r.frame && r.frame.error) put('e','render: '+r.frame.error.message);
  } catch(e){ put('e', String(e)); }
}
$('run').onclick = ()=>{ const t=$('code').value; $('code').value=''; if(t.trim()) run(t); };
$('code').addEventListener('keydown', e => {
  if (e.key==='Enter'){ const t=$('code').value; $('code').value=''; if(t.trim()) run(t); }
  else if (e.key==='ArrowUp'){ if(hi>0){hi--; $('code').value=hist[hi]||''; e.preventDefault();} }
});
put('sys','debugger ready - click a registry entry to invoke it live');
pollPrograms(); pollState(); pollActivity();
</script>
</body>
</html>
"""
