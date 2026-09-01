"""The `live.*` tool group: Python HMR over a LiveREPL, as first-class tools.

Registers the subsystem described in docs/LIVE-HMR.md with the same contract
as every other group (TOOL-CONTRACT): plain functions, parameter-name
injection, `SkeletonKeyError` for anything the agent can act on, honest risk
metadata. `live.repl` / `live.patch` / `live.start` execute python, so they
carry `risk="write", open_world=True, idempotent=False` - the same envelope
of trust as `shell.run` - and are withholding subjects of policy.deny,
read_only, and approvals exactly like a script.

The handlers themselves are thin: all real behaviour lives in
`live.runtime.LiveManager` so the CLI (`sk live`) and tests exercise the same
path the MCP host does.
"""

from __future__ import annotations

from typing import Any

from ..core.errors import E, SkeletonKeyError
from ..core.manifest import ToolManifest

TOOL_SPECS: list[ToolManifest] = []


def _spec(**kw: Any) -> None:
    TOOL_SPECS.append(ToolManifest(**kw))


_SCENE_TYPES = ["rect", "circle", "ellipse", "line", "text", "poly3d", "cube3d", "point3d",
                "mesh3d"]
_PID = {"type": "string",
        "description": "Live program id (from live.start/status). Omit when only one is running."}

# ------------------------------------------------------------------- specs
_spec(
    id="live.start", title="Start a live program",
    description="Load a python file into a managed live namespace: it executes once, its state "
                "is snapshotted for the 3-way merge, a `canvas` (Scene) is injected for drawing, "
                "and - with watch=true - the file watcher hot-patches it on every save. If the "
                "module defines render(), it runs after every change; errors become the panel's "
                "overlay instead of a crash. This executes the file: same trust class as shell.run.",
    capability="live.start", risk="write", open_world=True, idempotent=False,
    parallel_safe=False, stateful="session", session_scope="live",
    typical_latency_ms=120, timeout_s=60.0,
    tags=["live", "hmr", "hot-reload", "repl", "preview", "watch"],
    anti_patterns=[
        "don't start programs outside the workspace roots - resolve() refuses, declare the root",
        "don't expect top-level side effects to be repeatable; put frame drawing in render()"],
    see_also=["live.repl", "live.reload", "live.serve", "live.status"],
    examples=[{"args": {"path": "examples/live_hmr/orbital.py"}},
              {"args": {"path": "app.py", "watch": False, "keep": ["cache"]}}],
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1,
                     "description": "Python file, inside a workspace root."},
            "program": {"type": "string", "description": "Explicit program id (default: file stem)."},
            "watch": {"type": "boolean", "default": True,
                      "description": "Watch the file (and its same-tree imports) for saves."},
            "auto_render": {"type": "boolean", "default": True,
                            "description": "Call render() after every load/reload/repl/patch."},
            "keep": {"type": "array", "items": {"type": "string"}, "maxItems": 64,
                     "description": "Extra names that always survive reloads (like __live_keep__)."},
            "argv": {"type": "array", "items": {"type": "string"}, "maxItems": 32,
                     "description": "Visible to the program as __argv__."},
            "width": {"type": "integer", "minimum": 64, "maximum": 4096, "default": 420},
            "height": {"type": "integer", "minimum": 64, "maximum": 4096, "default": 320},
        },
        "required": ["path"]})

_spec(
    id="live.stop", title="Stop live programs",
    description="Tear down one live program (by id) or all of them. Stops the watcher when the "
                "last program goes away. In-memory only - the file on disk is never touched.",
    capability="live.stop", risk="write", idempotent=False, stateful="session",
    typical_latency_ms=20, tags=["live", "hmr"],
    see_also=["live.start", "live.status"],
    examples=[{"args": {}}],
    input_schema={"type": "object",
                  "properties": {"program": dict(_PID)}})

_spec(
    id="live.status", title="Live HMR status",
    description="Programs running, watcher backend (poll vs watchfiles), reload/repl counters, "
                "per-program keep-lists and dependency edges, panel URL when serving, and the "
                "live config actually in effect. The place to look before assuming a save was seen.",
    capability="live.status", risk="read", idempotent=True, typical_latency_ms=5,
    tags=["live", "hmr", "status", "watch"],
    examples=[{"args": {}}, {"args": {"history": True}}],
    input_schema={"type": "object",
                  "properties": {
                      "program": dict(_PID),
                      "history": {"type": "boolean", "default": False,
                                  "description": "Include the program's REPL + patch history."},
                      "history_limit": {"type": "integer", "minimum": 1, "maximum": 100,
                                        "default": 20}}})

_spec(
    id="live.reload", title="Hot-reload a live program",
    description="Re-read the program file and merge it into the running namespace now: functions "
                "and class methods swap code in place (held references and live instances see the "
                "new behaviour), new names bind, source-owned state tracks the edit, and REPL-made "
                "mutations survive (they are reported under 'preserved'). A broken file changes "
                "nothing and returns the error with line/offset - the running code is untouched.",
    capability="live.reload", risk="write", idempotent=False, parallel_safe=False,
    stateful="session", session_scope="live", typical_latency_ms=40,
    tags=["live", "hmr", "hot-reload", "reload", "patch"],
    anti_patterns=["don't reload in a loop to animate; mutate state through live.repl instead"],
    see_also=["live.patch", "live.repl", "live.snapshot"],
    examples=[{"args": {}}, {"args": {"force_source": ["color"]}}],
    input_schema={"type": "object",
                  "properties": {
                      "program": dict(_PID),
                      "force_source": {"type": "array", "items": {"type": "string"},
                                       "maxItems": 64,
                                       "description": "Names that take the file's value this "
                                                      "cycle even if the REPL moved them."}}})

_spec(
    id="live.patch", title="Hot-swap a function or class",
    description="Compile one def/class from `code` and merge it into the running program IN "
                "PLACE - every held reference and every existing instance sees the new code "
                "immediately (React Fast Refresh's move, for python objects). `name` may be "
                "dotted ('Ship.thrust') to patch one method of a live class. This is how you "
                "change behaviour without touching the file or losing state.",
    capability="live.patch", risk="write", idempotent=False, parallel_safe=False,
    stateful="session", session_scope="live", typical_latency_ms=15,
    tags=["live", "hmr", "hotpatch", "monkey-patch", "swizzle"],
    anti_patterns=["don't patch what the file defines then wonder why a save reverts it - "
                   "the file owns its own defs; edit the file or keep the patch REPL-side"],
    see_also=["live.reload", "live.repl"],
    examples=[{"args": {"name": "render",
                        "code": "def render():\n    canvas.clear()\n    canvas.circle(200, 150, 40, fill='blue')"}},
              {"args": {"name": "Ship.thrust", "code": "def thrust(self, dv):\n    self.v += dv * 2"}}],
    input_schema={
        "type": "object",
        "properties": {
            "program": dict(_PID),
            "name": {"type": "string", "minLength": 1,
                     "description": "Name the code defines ('render', 'Ship', 'Ship.thrust')."},
            "code": {"type": "string", "minLength": 1,
                     "description": "A full def/class body. Compiled, then swapped in place."},
            "render": {"type": "boolean", "default": True,
                       "description": "Re-render the frame after patching."},
        },
        "required": ["name", "code"]})

_spec(
    id="live.repl", title="LiveREPL eval/exec",
    description="Run one expression or statement against the live program namespace - inspect "
                "state (`color`), mutate it (`color = 'blue'`), call things (`canvas.orbit(theta=1.2)`), "
                "define helpers (they are keep-listed and survive file saves). The last value is "
                "`_`. Mutations re-render the frame by default, which is the HMR-preview feel: "
                "no restart, state intact, UI moves. Executes arbitrary python in-process; the "
                "same trust class as shell.run.",
    capability="live.repl", risk="write", open_world=True, idempotent=False,
    parallel_safe=False, stateful="session", session_scope="live",
    typical_latency_ms=10, timeout_s=60.0,
    tags=["live", "repl", "eval", "hmr", "debug", "inspect"],
    anti_patterns=[
        "don't edit files through the repl - fs.patch gives you a diff and undo; repl is for state"],
    see_also=["live.state", "live.patch", "live.reload", "shell.run"],
    examples=[{"args": {"code": "color = '#f2cc60'"}},
              {"args": {"code": "[k for k, v in canvas.to_dict().items()]", "mode": "eval"}},
              {"args": {"code": "import math; canvas.cube3d(0, 0, 0, 60, id='cube', spin=30)"}}],
    input_schema={
        "type": "object",
        "properties": {
            "program": dict(_PID),
            "code": {"type": "string", "minLength": 1},
            "mode": {"type": "string", "enum": ["auto", "eval", "exec"], "default": "auto",
                     "description": "eval captures a result; exec runs statements. auto tries "
                                    "eval first, like a human prompt."},
            "render": {"type": "boolean", "default": True,
                       "description": "Re-render after the code runs (the live-preview move)."},
        },
        "required": ["code"]})

_spec(
    id="live.state", title="Inspect live program state",
    description="Read the live namespace as data: every visible name with type, clipped repr, "
                "and ownership (source | repl | keep-list) - the exact state of the 3-way merge. "
                "This is the answer to 'did my save actually take?'",
    capability="live.state.inspect", risk="read", idempotent=True, stateful="session",
    typical_latency_ms=5, tags=["live", "hmr", "state", "inspect", "debug"],
    see_also=["live.snapshot", "live.status"],
    examples=[{"args": {}}, {"args": {"keys": ["color", "ticks"]}}],
    input_schema={
        "type": "object",
        "properties": {
            "program": dict(_PID),
            "keys": {"type": "array", "items": {"type": "string"}, "maxItems": 128,
                     "description": "Only these names (default: everything visible)."},
        }})

_spec(
    id="live.snapshot", title="Snapshot / restore live state",
    description="Save the program's data namespace under a name (deep-copied; locks/sockets are "
                "reported as skipped), or restore one later. Restore hands ownership of each "
                "name back to the source merge base, so file edits track it again afterwards.",
    capability="live.state.snapshot", risk="write", idempotent=False, stateful="session",
    session_scope="live", typical_latency_ms=10,
    tags=["live", "hmr", "snapshot", "checkpoint", "undo"],
    see_also=["live.state", "live.reload"],
    examples=[{"args": {"op": "save", "name": "before-experiment"}},
              {"args": {"op": "restore", "name": "before-experiment"}},
              {"args": {"op": "list"}}],
    input_schema={
        "type": "object",
        "properties": {
            "program": dict(_PID),
            "op": {"type": "string", "enum": ["save", "restore", "list"], "default": "list"},
            "name": {"type": "string", "description": "Required for save/restore."},
        },
        "required": ["op"]})

_spec(
    id="live.render", title="Render the current frame",
    description="Force a render and return frame statistics - or the SVG itself with svg=true. "
                "The render hook (if the program defines one) runs against a cleared canvas; "
                "otherwise the retained scene is re-serialised. Render errors land in "
                "data.frame.error and on the panel overlay; they never fail the envelope.",
    capability="live.render", risk="read", idempotent=False, stateful="session",
    typical_latency_ms=15, tags=["live", "hmr", "render", "preview", "svg"],
    see_also=["live.scene", "live.serve"],
    examples=[{"args": {}}, {"args": {"svg": True}}],
    input_schema={
        "type": "object",
        "properties": {
            "program": dict(_PID),
            "svg": {"type": "boolean", "default": False,
                    "description": "Inline the frame's SVG in data.svg."},
        }})

_spec(
    id="live.scene", title="Drive the retained scene",
    description="Mutate the program's scene graph directly - the scene for programs without a "
                "render() hook, or surgical node edits alongside one. Nodes are typed dicts with "
                "stable ids; upsert replaces by id (edit, don't flicker). 3D wireframes "
                "(cube3d/poly3d/point3d) are orbit-projected; live.orbit via repl moves the camera.",
    capability="live.scene", risk="write", idempotent=False, parallel_safe=False,
    stateful="session", session_scope="live", typical_latency_ms=5,
    tags=["live", "scene", "svg", "preview", "3d", "canvas"],
    see_also=["live.render", "live.repl"],
    examples=[{"args": {"op": "upsert", "node": {"type": "circle", "id": "sun", "cx": 200,
                                                  "cy": 150, "r": 30, "fill": "#f2cc60"}}},
              {"args": {"op": "orbit", "theta": 0.9}},
              {"args": {"op": "remove", "id": "sun"}}],
    input_schema={
        "type": "object",
        "properties": {
            "program": dict(_PID),
            "op": {"type": "string", "enum": ["list", "upsert", "remove", "clear", "orbit"],
                   "default": "list"},
            "node": {"type": "object", "additionalProperties": True,
                     "description": "For upsert: {type, id?, ...props}. Types: rect circle "
                                    "ellipse line text poly3d cube3d point3d."},
            "id": {"type": "string", "description": "For remove."},
            "theta": {"type": "number"}, "phi": {"type": "number"}, "scale": {"type": "number"},
        },
        "required": ["op"]})

_spec(
    id="live.serve", title="Serve the live preview panel",
    description="Start (or stop) the local HTTP preview panel: the frame as SVG, the scene graph "
                "as JSON (the feed a react-three-fiber-style 3D frontend would use), a Vite-style "
                "error overlay, and an in-page LiveREPL console. Binds host/port from [live] "
                "(default 127.0.0.1:8010); pass 0.0.0.0 inside sandboxes that proxy previews.",
    capability="live.serve", risk="write", open_world=True, idempotent=False,
    stateful="session", typical_latency_ms=30,
    tags=["live", "hmr", "preview", "server", "panel", "http"],
    anti_patterns=["don't expose the panel beyond the dev sandbox: POST /repl executes code - "
                   "set panel_repl=false or keep the bind host loopback"],
    see_also=["live.start", "live.render", "live.status"],
    examples=[{"args": {}}, {"args": {"port": 0}},
              {"args": {"op": "stop"}}],
    input_schema={
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": ["start", "stop"], "default": "start"},
            "host": {"type": "string", "description": "Bind host (default from [live] host)."},
            "port": {"type": "integer", "minimum": 0, "maximum": 65535,
                     "description": "0 = ephemeral; the chosen port is returned."},
        }})


# ---------------------------------------------------------------- register


def register(reg: Any, *, manager: Any, engine: Any = None) -> dict[str, Any]:
    """Bind handlers to the manifests above. `manager` is a live.LiveManager
    owned by the toolkit (so `sk`, MCP and tests all share one runtime)."""
    from .panel import Panel

    report = {"registered": 0, "skipped": []}
    # the panel's agent-debugger page reads the engine's ledger tail for its
    # activity rail; attached here, read best-effort (never required).
    manager._engine = engine

    def add(tool_id: str, handler: Any) -> None:
        try:
            man = next(m for m in TOOL_SPECS if m.id == tool_id)
        except StopIteration:
            report["skipped"].append(f"{tool_id}: no manifest")
            return
        if not manager._cfg("enabled", True):
            # Same shape as tools.disable: the tool stays visible, calls get a
            # coded, acted-upon refusal instead of a missing-tool surprise.
            def refusing(*_a: Any, _id: str = tool_id, **_kw: Any) -> None:
                raise SkeletonKeyError(
                    E.TOOL_NOT_ADVERTISED, f"tool {_id} is disabled by configuration",
                    details={"disabled_by": "live.enabled",
                             "advice": "set [live] enabled = true (or SKELETONKEY_LIVE__ENABLED=true)"})
            refusing.__signature__ = None
            handler = refusing
        handler.__name__ = tool_id.replace(".", "_")
        reg.register(man, handler, replace=True)
        report["registered"] += 1

    def _guard_s() -> float:
        return float(manager._cfg("exec_guard_s", 10.0))

    def _max_out() -> int:
        return int(manager._cfg("repl_max_output_bytes", 16_000))

    # ------------------------------------------------------------------ tools
    def live_start(path: str, program: str | None = None, watch: bool = True,
                   auto_render: bool = True, keep: list[str] | None = None,
                   argv: list[str] | None = None, width: int = 420, height: int = 320,
                   ctx: Any = None) -> dict[str, Any]:
        return manager.start(path, pid=program, watch=watch, auto_render=auto_render,
                             keep=keep, argv=argv or [],
                             canvas_size=(int(width), int(height)), guard_s=_guard_s())

    def live_stop(program: str | None = None) -> dict[str, Any]:
        return manager.stop(program)

    def live_status(program: str | None = None, history: bool = False,
                    history_limit: int = 20) -> dict[str, Any]:
        out = manager.status()
        if program:
            out["program_detail"] = manager.get(program).status()
        if history:
            out["history"] = manager.history(program, limit=int(history_limit))
        return out

    def live_reload(program: str | None = None,
                    force_source: list[str] | None = None) -> dict[str, Any]:
        prog = manager.get(program)
        rep = prog.reload(guard_s=_guard_s(), reason="manual", force_source=force_source)
        data = rep.to_dict()
        data["program"] = prog.id
        data["frame_version"] = prog.frame_version
        if rep.ok:
            data["reloads"] = prog.reloads
        return data

    def live_patch(name: str, code: str, program: str | None = None,
                   render: bool = True) -> dict[str, Any]:
        prog = manager.get(program)
        rep = prog.patch_def(name, code, guard_s=_guard_s(), render=render)
        data = rep.to_dict()
        data["program"] = prog.id
        data["frame_version"] = prog.frame_version
        if rep.ok:
            manager.fanout_frame(prog)
        return data

    def live_repl(code: str, program: str | None = None, mode: str = "auto",
                  render: bool = True) -> dict[str, Any]:
        prog = manager.get(program)
        return prog.repl(code, mode=mode, render=render,
                         guard_s=_guard_s(), max_out=_max_out(),
                         frame_cb=manager.fanout_frame)

    def live_state(program: str | None = None, keys: list[str] | None = None) -> dict[str, Any]:
        prog = manager.get(program)
        return prog.state_view(keys, max_repr=int(manager._cfg("state_value_max_repr", 400)))

    def live_snapshot(op: str, program: str | None = None, name: str | None = None) -> dict[str, Any]:
        prog = manager.get(program)
        if op == "list":
            return {"program": prog.id, "snapshots": sorted(prog.snapshots)}
        if not name:
            raise SkeletonKeyError(E.MISSING_ARG, f"op={op} needs `name`",
                                   details={"missing": "name"})
        if op == "save":
            return prog.snapshot(name, max_snapshots=int(manager._cfg("snapshots_max", 16)))
        return prog.restore(name)

    def live_render(program: str | None = None, svg: bool = False) -> dict[str, Any]:
        prog = manager.get(program)
        frame = prog.render(guard_s=_guard_s())
        data: dict[str, Any] = {"program": prog.id, "frame": frame}
        if svg:
            with prog.lock:
                data["svg"] = prog.frame_svg
        manager.fanout_frame(prog)
        return data

    def live_scene(op: str, program: str | None = None, node: dict[str, Any] | None = None,
                   id: str | None = None, theta: float | None = None,
                   phi: float | None = None, scale: float | None = None) -> dict[str, Any]:
        prog = manager.get(program)
        canvas = prog.canvas
        if op == "list":
            return {"program": prog.id, "scene": canvas.to_dict()}
        if op == "clear":
            canvas.clear()
        elif op == "remove":
            if not id:
                raise SkeletonKeyError(E.MISSING_ARG, "op=remove needs `id`",
                                       details={"missing": "id"})
            if not canvas.remove(id):
                raise SkeletonKeyError(E.ENOENT, f"no scene node {id!r}",
                                       details={"nodes": [n["id"] for n in canvas.nodes()]})
        elif op == "orbit":
            camera = canvas.orbit(theta, phi, scale)
            frame = prog.render(guard_s=_guard_s())
            manager.fanout_frame(prog)
            return {"program": prog.id, "camera": camera, "frame": frame}
        elif op == "upsert":
            if not node or not isinstance(node, dict):
                raise SkeletonKeyError(E.MISSING_ARG, "op=upsert needs a `node` object",
                                       details={"missing": "node"})
            kind = str(node.get("type", ""))
            if kind not in _SCENE_TYPES:
                raise SkeletonKeyError(E.BAD_ARGS, f"unknown node type {kind!r}",
                                       details={"types": _SCENE_TYPES})
            props = {k: v for k, v in node.items() if k not in ("type", "id")}
            canvas.upsert(kind, id=node.get("id"), **props)
        frame = prog.render(guard_s=_guard_s())
        manager.fanout_frame(prog)
        return {"program": prog.id, "op": op, "scene_version": canvas.version, "frame": frame}

    def live_serve(op: str = "start", host: str | None = None,
                   port: int | None = None) -> dict[str, Any]:
        if op == "stop":
            panel, manager.panel = manager.panel, None
            if panel is None:
                return {"serving": False, "note": "no panel was running"}
            panel.stop()
            return {"serving": False, "was": {"host": panel.host, "port": panel.port}}
        host = host or str(manager._cfg("host", "127.0.0.1"))
        if port is None:
            port = int(manager._cfg("port", 8010))
        repl_on = bool(manager._cfg("panel_repl", True))
        if manager.panel is not None:
            panel = manager.panel
            if panel.host == host and panel.port == int(port) and panel._thread and panel._thread.is_alive():
                st = panel.status()
                st["note"] = "already serving"
                return st
            manager.panel.stop()
        manager.panel = Panel(manager, host=host, port=int(port), repl_enabled=repl_on)
        return manager.panel.start()

    add("live.start", live_start)
    add("live.stop", live_stop)
    add("live.status", live_status)
    add("live.reload", live_reload)
    add("live.patch", live_patch)
    add("live.repl", live_repl)
    add("live.state", live_state)
    add("live.snapshot", live_snapshot)
    add("live.render", live_render)
    add("live.scene", live_scene)
    add("live.serve", live_serve)
    return report
