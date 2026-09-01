"""The runtime: state-preserving live program hosting + the LiveREPL.

A `LiveProgram` is one python file exec'd into a managed namespace, kept
alive across file saves by the patcher, and given a `canvas` (Scene) to draw
into. The shape the user's program conforms to is deliberately tiny::

    color = "red"          # live state: survives reloads (3-way merge)
    ticks = 0

    def render():          # optional render hook: a frame is a function call
        global ticks
        ticks += 1
        canvas.clear()
        canvas.rect(10, 10, 80, 40, fill=color)
        canvas.text(50, 90, f"ticks={ticks}")

Edit the file and the watcher pushes the save through the patcher; talk to
the same namespace through `live.repl` ("color = 'blue'") and the frame moves
*now*. That loop - mutate state without restart, see the frame - is exactly
the HMR-shaped behaviour LiveREPL is here to simulate.

Safety/lineage notes:

* Imports inside a program resolve like normal python (the program's
  directory goes on sys.path); a watched dependency file is hot-reloaded as
  its *module object* in place (same patcher), and `from dep import f`
  bindings inside the program are re-pointed at the module's refreshed
  functions after the patch.
* REPL/patch/render run under a wall-clock trace guard (settrace-based) so a
  `while True:` in the REPL burns seconds, not the process. It is a leash,
  not a sandbox: C-level blocking calls are documented best-effort.
* A broken save never wedges the last good frame: compile/scratch-exec
  failures land on the program as the panel's error overlay, and the old
  code keeps serving renders.
* Names the runtime injects (`canvas`, `__argv__`) and names the REPL
  defined get keep-listed: a file save is never allowed to delete what the
  file does not own.

Explicit contracts honoured around the automatic merge (opt-in, all dunders,
never removed by reloads):

    __live_keep__ = ["cache"]            # these names always belong to the runtime
    __live_registries__ = ["HANDLERS"]   # dicts of callables re-pointed after reload
    def __hmr_export_state__(): ...      # runs on the OLD code before patching
    def __hmr_import_state__(state): ... # runs on the NEW code after patching
    def __live_on_reload__(report): ...  # observer: the PatchReport as a dict

Export runs pre-patch, so an abort there leaves the running program fully
untouched; import runs post-patch on the freshly merged namespace, so it can
reshape whatever the 3-way merge produced.
"""

from __future__ import annotations

import ast
import contextlib
import io
import os
import sys
import threading
import time
import traceback
from collections import deque
from typing import Any

from ..core.errors import E, SkeletonKeyError
from .patcher import PatchReport, _same_value, deepcopy_safe, patch_namespace, source_data_defaults
from .scene import Scene
from .watcher import FileWatcher, watchfiles_available

_HOOKS = ("render", "tick", "setup")
# runtime-injected names a file save must never treat as deletable user data
_INJECTED_KEEP = ("canvas",)
_INJECTED_SET = set(_INJECTED_KEEP)


class LiveTimeout(Exception):
    """Raised by the trace guard when user code outruns its wall budget."""


def _trace_guard(deadline: float):
    """A per-thread sys.settrace guard: pure-python loops are interrupted at
    the next line event once the deadline passes."""
    def tracer(frame: Any, event: str, arg: Any) -> Any:
        if event == "line" and time.monotonic() > deadline:
            raise LiveTimeout("exceeded the execution guard (wall-clock deadline)")
        return tracer
    return tracer


class _Guarded:
    """Context manager: install the trace guard, restore the old one after."""

    def __init__(self, seconds: float | None) -> None:
        self.seconds = seconds if seconds and seconds > 0 else None
        self._prev: Any = None

    def __enter__(self) -> _Guarded:
        self._prev = sys.gettrace()
        if self.seconds:
            sys.settrace(_trace_guard(time.monotonic() + self.seconds))
        return self

    def __exit__(self, *exc: Any) -> bool:
        sys.settrace(self._prev)
        return False


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 40)] + f"\n… <clipped {len(text) - limit + 40} chars>"


def _visible(name: str) -> bool:
    return not (name.startswith("__") and name.endswith("__"))


def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def _import_roots(tree: ast.AST) -> list[str]:
    """Top-level import roots from a module body: `import a.b` -> 'a.b',
    `from x.y import z` -> 'x.y'. Relative imports are the caller's package
    and resolved against the program's own directory."""
    roots: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            roots.append(node.module)
    seen: dict[str, None] = {}
    for r in roots:
        seen.setdefault(r)
    return list(seen)


_MISSING = object()


# ------------------------------------------------------------------- program


class LiveProgram:
    """One managed namespace + its render surface + its state ledger."""

    def __init__(self, pid: str, path: str, *, display: str | None = None,
                 canvas_size: tuple[int, int] = (420, 320), keep: list[str] | None = None,
                 argv: list[str] | None = None) -> None:
        self.id = pid
        self.path = os.path.abspath(path)
        self.display = display or os.path.basename(path)
        self.canvas = Scene(*canvas_size)
        self.keep: set[str] = set(keep or ()) | set(_INJECTED_KEEP)
        self.ns: dict[str, Any] = {
            "__name__": pid,                      # program defs get __module__ == pid
            "__file__": self.path,
            "__package__": "",
            "__builtins__": __builtins__,
            "__live__": True,
            "__argv__": list(argv or []),
            "canvas": self.canvas,
        }
        self.lock = threading.RLock()
        self.base_state: dict[str, Any] = {}
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.history: deque[dict[str, Any]] = deque(maxlen=100)
        self.patch_log: deque[dict[str, Any]] = deque(maxlen=50)
        # dependency graph: module_name -> {"path", "base"} for watched imports
        self.deps: dict[str, dict[str, Any]] = {}
        self.created_at = time.time()
        self.reloads = 0
        self.failed_reloads = 0
        self.repl_count = 0
        self.frame_version = 0
        self.frame_svg = ""
        self.frame_error: dict[str, Any] | None = None
        self.frame_at: float | None = None
        self.render_ms = 0.0
        self.last_source_sha = ""
        self.auto_render = True

    # ------------------------------------------------------------------ load
    def _read_source(self) -> str:
        with open(self.path, encoding="utf-8") as fh:
            return fh.read()

    def _exec_source(self, source: str, ns: dict[str, Any]) -> tuple[list[str], str]:
        """Exec the module body in `ns`; returns (import_roots, captured output).
        Import roots feed the dependency tracker."""
        tree = ast.parse(source, filename=self.path)
        roots = _import_roots(tree)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = compile(tree, self.path, "exec")
            exec(code, ns)
        return roots, out.getvalue()

    def load_initial(self, *, guard_s: float | None = 10.0) -> dict[str, Any]:
        """First exec: populate the namespace, capture the state base, scan
        dependencies, take the first frame."""
        with self.lock:
            source = self._read_source()
            self._prepare_import_path()
            prog_dir = os.path.dirname(self.path)
            with _Guarded(guard_s):
                roots, stdout = self._exec_source(source, self.ns)
            self.keep |= set(self.ns.get("__live_keep__") or ())
            self._track_deps(roots, source_dir=prog_dir)
            self._rebase_state()
            self.last_source_sha = _sha(source)
            frame = self.render(guard_s=guard_s) if self.auto_render else None
            data: dict[str, Any] = {"loaded": True, "path": self.display,
                                    "names": sorted(k for k in self.ns if _visible(k)),
                                    "deps": sorted(self.deps)}
            if stdout.strip():
                data["startup_stdout"] = _clip(stdout, 4_000)
            if frame is not None:
                data["frame"] = frame
            return data

    def _prepare_import_path(self) -> None:
        """The program's dir must import like a script's dir would."""
        d = os.path.dirname(self.path)
        if d and d not in sys.path:
            sys.path.insert(0, d)

    def _rebase_state(self) -> None:
        """Snapshot the source-side arm of the 3-way merge."""
        self.base_state = {}
        for name, value in source_data_defaults(self.ns).items():
            if name in self.keep:
                continue
            ok, cloned = deepcopy_safe(value)
            if ok:
                self.base_state[name] = cloned

    def _fresh_scratch(self) -> dict[str, Any]:
        # same __name__ as the live namespace: defs from the scratch exec get
        # the same __module__ marker as the originals, so ownership checks in
        # the patcher line up across both sides. The scratch canvas means a
        # module body that draws at import time works during a reload too -
        # its strokes are discarded (the render hook owns steady-state frames)
        # instead of double-applying onto the live frame.
        scratch_canvas = Scene(self.canvas.width, self.canvas.height,
                               self.canvas.background, dict(self.canvas.camera))
        return {"__name__": self.id, "__file__": self.path, "__package__": "",
                "__builtins__": __builtins__, "__live__": True,
                "__argv__": list(self.ns.get("__argv__") or []),
                "canvas": scratch_canvas}

    # ---------------------------------------------------------------- reload
    def reload(self, *, guard_s: float | None = 10.0, reason: str = "manual",
               force_source: list[str] | None = None) -> PatchReport:
        """Compile the current file in scratch, then merge into the live
        namespace. Broken source = report-only; the old code keeps running.
        `force_source` names hand ownership back to the file for this cycle
        (the "the REPL moved it, I want the file's value after all" hatch)."""
        with self.lock:
            report = PatchReport()
            t0 = time.monotonic()
            try:
                source = self._read_source()
            except OSError as exc:
                report.ok = False
                report.error = {"code": E.ENOENT.code, "message": str(exc)}
                self._remember(report, reason, t0)
                self.failed_reloads += 1
                return report
            scratch = self._fresh_scratch()
            try:
                with _Guarded(guard_s):
                    roots, stdout = self._exec_source(source, scratch)
            except SyntaxError as exc:
                report.ok = False
                report.error = {"code": E.PARSE.code,
                                "message": f"{exc.msg} (line {exc.lineno})",
                                "line": exc.lineno, "offset": exc.offset,
                                "text": (exc.text or "").strip()}
                self._failed(report, reason, t0)
                return report
            except LiveTimeout as exc:
                report.ok = False
                report.error = {"code": E.TIMEOUT.code, "message": str(exc)}
                self._failed(report, reason, t0)
                return report
            except Exception as exc:
                report.ok = False
                report.error = {"code": E.INTERNAL.code,
                                "message": f"module body raised: {type(exc).__name__}: {exc}",
                                "trace": _clip(traceback.format_exc(), 2_000)}
                self._failed(report, reason, t0)
                return report
            if stdout.strip():
                report.notes.append("module body printed on reload: " + _clip(stdout.strip(), 800))

            # -- explicit state contract: export on the OLD code, pre-patch, so
            # a failing export aborts the reload with nothing yet applied.
            exported: Any = _MISSING
            export_hook = self.ns.get("__hmr_export_state__")
            if callable(export_hook):
                try:
                    with _Guarded(guard_s):
                        exported = export_hook()
                    report.notes.append("state exported via __hmr_export_state__")
                except Exception as exc:
                    report.ok = False
                    report.error = {"code": E.INTERNAL.code,
                                    "message": f"__hmr_export_state__ raised "
                                               f"{type(exc).__name__}: {exc} - reload aborted, "
                                               f"nothing was patched",
                                    "trace": _clip(traceback.format_exc(), 2_000)}
                    self._failed(report, reason, t0)
                    return report

            # the file can extend its own keep-list in the very edit being
            # saved, so union BEFORE this cycle's merge decisions.
            self.keep |= set(scratch.get("__live_keep__") or ())
            keep = set(self.keep) - set(force_source or ())
            # hooks and registries are plumbing, not mergeable state
            ignore = _INJECTED_SET | {"__live_registries__"}
            pre_notes = list(report.notes)          # export-hook notes survive the swap
            report, new_base = patch_namespace(self.ns, scratch, base=self.base_state,
                                               keep=keep, module_marker=self.id,
                                               ignore=ignore,
                                               force=set(force_source or ()))
            report.notes = pre_notes + report.notes
            self._track_deps(roots, source_dir=os.path.dirname(self.path))
            # keep-listed names live outside the merge base by definition.
            self.base_state = {k: v for k, v in new_base.items() if k not in self.keep}

            # -- explicit contract, import side: runs on the NEW code AFTER the
            # merge, so it sees (and can reshape) whatever the merge produced.
            import_hook = self.ns.get("__hmr_import_state__")
            if callable(import_hook) and exported is not _MISSING:
                try:
                    with _Guarded(guard_s):
                        import_hook(exported)
                    report.notes.append("state imported via __hmr_import_state__")
                except Exception as exc:
                    report.ok = False
                    report.error = {"code": E.INTERNAL.code,
                                    "message": f"__hmr_import_state__ raised "
                                               f"{type(exc).__name__}: {exc} - code WAS patched; "
                                               "state handoff failed",
                                    "trace": _clip(traceback.format_exc(), 2_000)}
                    self._remember(report, reason, t0)
                    self.reloads += 1
                    self.frame_error = dict(report.error)
                    self.frame_version += 1
                    return report

            # -- registry rebinding: module-held dicts of callables (agent
            # registries, handler tables) get re-pointed at freshly defined
            # same-named objects. Usually a no-op - the in-place swap already
            # updated the shared object - but it is THE fix for entries that
            # captured a value the reload rebound (freevars change, new def).
            self._rebind_registries(scratch, report)

            observer = self.ns.get("__live_on_reload__")
            if callable(observer):
                try:
                    with _Guarded(guard_s):
                        observer(report.to_dict())
                except Exception as exc:
                    report.notes.append(f"__live_on_reload__ observer raised "
                                        f"{type(exc).__name__}: {exc} (ignored)")

            self.last_source_sha = _sha(source)
            self.reloads += 1
            self._remember(report, reason, t0)
            if self.auto_render:
                self.render(guard_s=guard_s)
            return report

    def _failed(self, report: PatchReport, reason: str, t0: float) -> None:
        report.ok = False
        self.failed_reloads += 1
        self._remember(report, reason, t0)
        # the overlay: the frame stays up but carries the error, like Vite.
        self.frame_error = dict(report.error or {})
        self.frame_version += 1

    def _rebind_registries(self, scratch: dict[str, Any], report: PatchReport) -> None:
        """`__live_registries__ = ["HANDLERS"]`: after a reload, re-point
        entries of those module dicts at the reload's freshly defined objects
        of the same `__name__`. Entries whose object was patched in place are
        already live (identity unchanged) and are skipped silently."""
        names = self.ns.get("__live_registries__") or scratch.get("__live_registries__") or ()
        rebound: list[str] = []
        for reg_name in names:
            reg = self.ns.get(reg_name)
            if not isinstance(reg, dict):
                if reg_name in self.ns:
                    report.notes.append(f"__live_registries__: {reg_name!r} is not a dict; ignored")
                continue
            for key, val in list(reg.items()):
                vn = getattr(val, "__name__", None)
                fresh = self.ns.get(vn) if vn and isinstance(vn, str) else None
                if callable(fresh) and fresh is not val:
                    reg[key] = fresh
                    rebound.append(f"{reg_name}[{key!r}] -> {vn}")
        if rebound:
            report.notes.append("registry rebind: " + ", ".join(rebound))
            report.data_updated.append(f"__live_registries__ ({len(rebound)} entries)")

    def _remember(self, report: PatchReport, reason: str, t0: float) -> None:
        entry = report.to_dict()
        entry.update({"reason": reason, "at": round(time.time(), 3),
                      "ms": round((time.monotonic() - t0) * 1000, 1)})
        self.patch_log.append(entry)

    # --------------------------------------------------------- dependencies
    def _track_deps(self, roots: list[str], *, source_dir: str) -> None:
        """Resolve the program's imports to watched files we can also patch.
        Only same-tree modules participate: stdlib/site-packages imports are
        left to the interpreter (hot-reloading those has no HMR upside and a
        large blast radius)."""
        for root in roots:
            cand = os.path.join(source_dir, *root.split("."))
            path = cand + ".py"
            pkg_init = os.path.join(cand, "__init__.py")
            found = path if os.path.isfile(path) else (pkg_init if os.path.isfile(pkg_init) else None)
            if not found:
                continue
            found = os.path.abspath(found)
            if found == self.path:
                continue
            entry = self.deps.get(root)
            if entry is None:
                self.deps[root] = {"path": found, "base": {}}
                module = sys.modules.get(root)
                if module is not None:
                    try:
                        self.deps[root]["base"] = _module_base(module)
                    except Exception:
                        pass
            else:
                entry["path"] = found

    def dep_paths(self) -> list[str]:
        return sorted(e["path"] for e in self.deps.values())

    def reload_dependency(self, mod_name: str, *, guard_s: float | None = 10.0) -> PatchReport:
        """A watched import changed: patch its sys.modules object in place,
        then re-point the program namespace's `from dep import f` bindings at
        the module's refreshed names (plain `import dep` users are already
        live; from-imports hold the object identity and need the re-point -
        functions still land on the same objects thanks to the in-place swap,
        but rebound/new names must migrate)."""
        with self.lock:
            entry = self.deps[mod_name]
            report = PatchReport()
            module = sys.modules.get(mod_name)
            if module is None:
                report.ok = False
                report.error = {"code": E.ENOENT.code,
                                "message": f"{mod_name!r} is not in sys.modules (never imported?)"}
                return report
            try:
                with open(entry["path"], encoding="utf-8") as fh:
                    source = fh.read()
            except OSError as exc:
                report.ok = False
                report.error = {"code": E.ENOENT.code, "message": str(exc)}
                return report
            scratch: dict[str, Any] = {"__name__": mod_name, "__file__": entry["path"],
                                       "__package__": mod_name.rpartition(".")[0],
                                       "__builtins__": __builtins__}
            try:
                with _Guarded(guard_s):
                    self._exec_source(source, scratch)
            except Exception as exc:
                report.ok = False
                code = E.PARSE.code if isinstance(exc, SyntaxError) else (
                    E.TIMEOUT.code if isinstance(exc, LiveTimeout) else E.INTERNAL.code)
                report.error = {"code": code, "message": f"{type(exc).__name__}: {exc}"}
                self.frame_error = dict(report.error)
                self.frame_version += 1
                return report
            report, new_base = patch_namespace(module.__dict__, scratch,
                                               base=entry.get("base") or {},
                                               keep=set(), module_marker=mod_name,
                                               ignore=_INJECTED_SET)
            # refresh `from dep import name` bindings in the program namespace.
            # Collision rule: only a name we can PROVE came from this module
            # (its current value is owned by mod_name), or a data name that
            # still equals the dep's old base (untouched since import), gets
            # re-pointed. A `helper` the program defined itself is never
            # clobbered by a dep that happens to define `helper` too.
            old_base = entry.get("base") or {}
            entry["base"] = new_base
            repointed: list[str] = []
            for attr, val in vars(module).items():
                if attr.startswith("__") and attr.endswith("__"):
                    continue
                cur = self.ns.get(attr, _MISSING)
                if cur is _MISSING or cur is val:
                    continue
                if callable(cur) or isinstance(cur, type):
                    if getattr(cur, "__module__", None) != mod_name:
                        continue
                else:
                    if attr not in old_base or not _same_value(cur, old_base[attr]):
                        continue
                self.ns[attr] = val
                repointed.append(attr)
            if repointed:
                report.notes.append(f"re-pointed from-imports after {mod_name} patch: "
                                    + ", ".join(sorted(repointed)))
            if self.auto_render:
                self.render(guard_s=guard_s)
            return report

    # ---------------------------------------------------------------- render
    def render(self, *, guard_s: float | None = 5.0, force_clear: bool = True) -> dict[str, Any]:
        """Take a frame. The render hook gets a cleared canvas (it owns the
        frame); without a hook, the retained scene as mutated by tools/REPL is
        the frame. Render failures become the overlay, never propagate."""
        with self.lock:
            t0 = time.monotonic()
            hook = self.ns.get("render")
            try:
                if callable(hook):
                    if force_clear:
                        self.canvas.clear()
                    with _Guarded(guard_s):
                        out = hook()
                    if isinstance(out, str) and out.lstrip().startswith("<svg"):
                        self.frame_svg = out
                    else:
                        self.frame_svg = self.canvas.to_svg()
                else:
                    self.frame_svg = self.canvas.to_svg()
                self.frame_error = None
            except LiveTimeout as exc:
                self.frame_error = {"code": E.TIMEOUT.code, "message": str(exc),
                                    "during": "render"}
            except Exception as exc:
                self.frame_error = {"code": E.INTERNAL.code,
                                    "message": f"render() raised {type(exc).__name__}: {exc}",
                                    "trace": _clip(traceback.format_exc(), 2_000),
                                    "during": "render"}
            self.render_ms = round((time.monotonic() - t0) * 1000, 2)
            self.frame_version += 1
            self.frame_at = time.time()
            return {"frame_version": self.frame_version, "scene_version": self.canvas.version,
                    "render_ms": self.render_ms, "svg_bytes": len(self.frame_svg),
                    "error": self.frame_error}

    # ---------------------------------------------------------------- repl
    def repl(self, code: str, *, mode: str = "auto", render: bool = True,
             guard_s: float | None = 10.0, max_out: int = 16_000,
             frame_cb: Any = None) -> dict[str, Any]:
        """The LiveREPL: one statement/expression against the live namespace.
        `eval` for expressions (result captured into `_`), `exec` otherwise;
        `mode='auto'` tries an eval-compile first, matching what a human at a
        prompt meant. Functions *defined* in the REPL get keep-listed so the
        next file save does not delete them - the file owns what it defines,
        the REPL owns what it made.
        """
        with self.lock:
            t0 = time.monotonic()
            stdout = io.StringIO()
            value: Any = _MISSING
            error: dict[str, Any] | None = None
            use_eval = mode == "eval"
            if mode == "auto":
                try:
                    compile(code, "<live-repl>", "eval")
                    use_eval = True
                except SyntaxError:
                    use_eval = False
            before = set(self.ns)
            try:
                with _Guarded(guard_s), contextlib.redirect_stdout(stdout), \
                        contextlib.redirect_stderr(stdout):
                    if use_eval:
                        value = eval(compile(code, "<live-repl>", "eval"), self.ns)
                        if value is not None:
                            self.ns["_"] = value
                    else:
                        exec(compile(code, "<live-repl>", "exec"), self.ns)
            except LiveTimeout as exc:
                error = {"code": E.TIMEOUT.code, "message": str(exc)}
            except Exception as exc:
                error = {"code": type(exc).__name__,
                         "message": f"{type(exc).__name__}: {exc}",
                         "trace": _clip(traceback.format_exc(), 2_000)}
            for name in set(self.ns) - before:
                if callable(self.ns[name]) and _visible(name):
                    self.keep.add(name)                  # REPL-made defs survive saves
            self.repl_count += 1
            frame = None
            if error is None and render and self.auto_render:
                frame = self.render(guard_s=guard_s)
            if frame_cb is not None:
                frame_cb(self)
            out_text = _clip(stdout.getvalue(), max_out)
            value_repr = _clip(repr(value), 4_000) if value is not _MISSING and value is not None else None
            entry: dict[str, Any] = {"at": round(time.time(), 3),
                                     "ms": round((time.monotonic() - t0) * 1000, 2),
                                     "mode": "eval" if use_eval else "exec",
                                     "code": _clip(code, 400), "ok": error is None}
            if value_repr:
                entry["value"] = value_repr
            self.history.append(entry)
            result: dict[str, Any] = {"ok": error is None, "mode": entry["mode"],
                                      "stdout": out_text, "repl_count": self.repl_count}
            if value_repr is not None:
                result["value"] = value_repr
            if error:
                result["error"] = error
            if frame is not None:
                result["frame"] = frame
            return result

    def patch_def(self, name: str, code: str, *, guard_s: float | None = 10.0,
                  render: bool = True) -> PatchReport:
        """The function hot-swap tool: compile ONE def (or class) and merge it
        into the live namespace, in place. `name` may be dotted
        (`Class.method`) to patch a single method of an existing class."""
        with self.lock:
            report = PatchReport()
            t0 = time.monotonic()
            scratch: dict[str, Any] = {"__name__": self.id, "__builtins__": __builtins__}
            try:
                with _Guarded(guard_s):
                    compiled = compile(code, "<live-patch>", "exec")
                    exec(compiled, scratch)
            except SyntaxError as exc:
                report.ok = False
                report.error = {"code": E.PARSE.code,
                                "message": f"{exc.msg} (line {exc.lineno})",
                                "line": exc.lineno, "offset": exc.offset}
                self._remember(report, "patch", t0)
                return report
            except Exception as exc:
                report.ok = False
                report.error = {"code": E.INTERNAL.code, "message": f"{type(exc).__name__}: {exc}"}
                self._remember(report, "patch", t0)
                return report

            import inspect as _inspect

            from .patcher import _patch_class, _rebind_with_live_globals, _swap_code

            def _bad(msg: str) -> PatchReport:
                report.ok = False
                report.error = {"code": E.BAD_ARGS.code, "message": msg,
                                "defined": sorted(k for k in scratch if _visible(k))}
                self._remember(report, "patch", t0)
                return report

            if "." in name:
                cls_name, meth = name.split(".", 1)
                cls = self.ns.get(cls_name)
                if not _inspect.isclass(cls):
                    report.ok = False
                    report.error = {"code": E.ENOENT.code,
                                    "message": f"no live class {cls_name!r}"}
                    self._remember(report, "patch", t0)
                    return report
                new_fn = scratch.get(meth)
                if not callable(new_fn):
                    return _bad(f"patch code did not define {meth!r}")
                old_fn = getattr(cls, meth, None)
                try:
                    if not callable(old_fn):
                        raise ValueError("no existing method")
                    _swap_code(_unwrap_fn(old_fn), new_fn)
                    report.patched_methods.append(name)
                except (ValueError, TypeError):
                    setattr(cls, meth, _rebind_with_live_globals(new_fn, self.ns, name))
                    if old_fn is None:
                        report.added.append(name)
                    else:
                        report.rebound.append(name)
            else:
                # A SINGLE name is supplied: nothing else in the namespace is
                # up for review, so this path never consults the removal arm
                # (that arm is the whole-file reload's pruning pass; feeding a
                # one-def scratch into it would delete everything else).
                new_val = scratch.get(name)
                if new_val is None:
                    return _bad(f"patch code did not define {name!r}")
                old_val = self.ns.get(name)
                if _inspect.isfunction(new_val):
                    if _inspect.isfunction(old_val):
                        try:
                            _swap_code(old_val, new_val)
                            report.patched_functions.append(name)
                        except ValueError as exc:
                            self.ns[name] = _rebind_with_live_globals(new_val, self.ns, name)
                            report.rebound.append(name)
                            report.notes.append(f"{name}: in-place swap refused ({exc}); rebound")
                    else:
                        self.ns[name] = _rebind_with_live_globals(new_val, self.ns, name)
                        report.added.append(name)
                elif _inspect.isclass(new_val):
                    if _inspect.isclass(old_val):
                        _patch_class(old_val, new_val, self.ns, report)
                    else:
                        for attr, desc in list(vars(new_val).items()):
                            fn = getattr(desc, "__func__", desc)
                            if _inspect.isfunction(fn) and fn.__globals__ is not self.ns:
                                live_fn = _rebind_with_live_globals(fn, self.ns, f"{name}.{attr}")
                                if isinstance(desc, staticmethod):
                                    setattr(new_val, attr, staticmethod(live_fn))
                                elif isinstance(desc, classmethod):
                                    setattr(new_val, attr, classmethod(live_fn))
                                else:
                                    setattr(new_val, attr, live_fn)
                        self.ns[name] = new_val
                        report.added.append(name)
                else:
                    self.ns[name] = new_val            # data patch = fancy assignment
                    report.data_updated.append(name)
            self._remember(report, "patch", t0)
            if report.ok and render and self.auto_render:
                self.render(guard_s=guard_s)
            return report

    # ---------------------------------------------------------------- state
    def state_view(self, keys: list[str] | None = None, *, max_repr: int = 400) -> dict[str, Any]:
        with self.lock:
            names = keys or sorted(k for k in self.ns if _visible(k))
            rows: dict[str, Any] = {}
            for name in names:
                if name not in self.ns:
                    rows[name] = {"missing": True}
                    continue
                value = self.ns[name]
                row: dict[str, Any] = {"type": type(value).__name__}
                if callable(value) and not isinstance(value, (int, float, str, bool)):
                    row["repr"] = f"<{type(value).__name__} {getattr(value, '__qualname__', name)}>"
                    if _visible(name):
                        row["owned_by"] = "keep-list" if name in self.keep else (
                            "repl" if getattr(value, "__module__", self.id) == self.id else "source")
                else:
                    row["repr"] = _clip(repr(value), max_repr)
                    if name in self.keep:
                        row["owned_by"] = "keep-list"
                    elif name not in self.base_state:
                        row["owned_by"] = "repl"
                    elif not _same_value(self.base_state[name], value):
                        row["owned_by"] = "repl"            # source name, moved at runtime
                    else:
                        row["owned_by"] = "source"
                rows[name] = row
            return {"program": self.id, "names": rows, "snapshots": sorted(self.snapshots)}

    def snapshot(self, name: str, *, max_snapshots: int = 16) -> dict[str, Any]:
        if not name or not str(name).strip():
            raise SkeletonKeyError(E.BAD_ARGS, "snapshot name must be non-empty")
        with self.lock:
            if name not in self.snapshots and len(self.snapshots) >= max_snapshots:
                raise SkeletonKeyError(E.TOO_LARGE,
                                       f"snapshot cap reached ({max_snapshots})",
                                       hint="restore or re-use an existing snapshot name")
            snap: dict[str, Any] = {}
            skipped: list[str] = []
            for key, value in source_data_defaults(self.ns).items():
                if key in _INJECTED_SET:
                    continue                       # host-owned (canvas): never state
                ok, cloned = deepcopy_safe(value)
                if ok:
                    snap[key] = cloned
                else:
                    skipped.append(key)
            self.snapshots[str(name)] = snap
            return {"snapshot": str(name), "keys": sorted(snap), "skipped": skipped,
                    "count": len(snap)}

    def restore(self, name: str) -> dict[str, Any]:
        with self.lock:
            if name not in self.snapshots:
                raise SkeletonKeyError(E.ENOENT, f"no snapshot named {name!r}",
                                       details={"snapshots": sorted(self.snapshots)})
            snap = self.snapshots[name]
            restored: list[str] = []
            skipped: list[str] = []
            for key, value in snap.items():
                ok, cloned = deepcopy_safe(value)
                if ok:
                    self.ns[key] = cloned
                    # a deliberate restore hands ownership back to the source
                    # merge base, so the next file edit tracks this name again.
                    self.base_state[key] = cloned
                    restored.append(key)
                else:
                    skipped.append(key)
            frame = self.render() if self.auto_render else None
            out: dict[str, Any] = {"restored": restored, "skipped": skipped, "snapshot": name}
            if frame is not None:
                out["frame"] = frame
            return out

    def status(self) -> dict[str, Any]:
        with self.lock:
            hook_names = [h for h in _HOOKS if callable(self.ns.get(h))]
            return {
                "id": self.id, "path": self.display, "abs_path": self.path,
                "reloads": self.reloads, "failed_reloads": self.failed_reloads,
                "repl_count": self.repl_count, "frame_version": self.frame_version,
                "scene_version": self.canvas.version, "render_ms": self.render_ms,
                "hooks": hook_names, "deps": sorted(self.deps),
                "dep_paths": self.dep_paths(),
                "keep": sorted(self.keep), "snapshots": sorted(self.snapshots),
                "source_sha": self.last_source_sha,
                "auto_render": self.auto_render,
                "frame_error": self.frame_error,
                "uptime_s": round(time.time() - self.created_at, 1),
                "names": len([k for k in self.ns if _visible(k)]),
            }


def _unwrap_fn(fn: Any) -> Any:
    return getattr(fn, "__func__", fn)


def _module_base(module: Any) -> dict[str, Any]:
    """The initial 3-way base for a dependency module, captured once."""
    base: dict[str, Any] = {}
    for name, value in source_data_defaults(module.__dict__).items():
        ok, cloned = deepcopy_safe(value)
        if ok:
            base[name] = cloned
    return base


# ------------------------------------------------------------------- manager


class LiveManager:
    """Owns the programs, the watcher, and (lazily) the preview panel.

    One manager per toolkit. All tool handlers and the CLI go through here so
    the locking story (per-program RLock; manager lock only for the program
    map) lives in exactly one place.
    """

    def __init__(self, config: Any = None, *, sandbox: Any = None) -> None:
        self.config = config
        self.sandbox = sandbox
        self.programs: dict[str, LiveProgram] = {}
        self._by_path: dict[str, str] = {}
        self._map_lock = threading.Lock()
        self.watcher: FileWatcher | None = None
        self.panel: Any = None
        self._frame_listeners: list[Any] = []

    def _cfg(self, name: str, default: Any) -> Any:
        return getattr(self.config, name, default) if self.config is not None else default

    # ------------------------------------------------------------------ paths
    def resolve(self, raw: str) -> str:
        """Every file the live system touches resolves through the fs sandbox
        when one is attached, so roots/deny/symlink policy apply exactly like
        fs.* (TOOL-CONTRACT §1). Without a sandbox (bare manager) fall back to
        absolute-path normalisation; `status()` reports `sandboxed: false`."""
        if not raw or not str(raw).strip():
            raise SkeletonKeyError(E.BAD_ARGS, "path is required")
        if self.sandbox is not None:
            res = self.sandbox.resolve(raw, intent="read")
            if not res.exists:
                raise SkeletonKeyError(E.ENOENT, f"no such file: {raw!r}",
                                       details={"path": res.display})
            return res.real or res.abs
        raw = raw if os.path.isabs(raw) else os.path.abspath(raw)
        if not os.path.exists(raw):
            raise SkeletonKeyError(E.ENOENT, f"no such file: {raw!r}", details={"path": raw})
        return raw

    def _display_of(self, abs_path: str) -> str:
        if self.sandbox is not None:
            try:
                return self.sandbox.resolve(abs_path, intent="read").display
            except Exception:
                pass
        return abs_path

    # ---------------------------------------------------------------- programs
    def start(self, path: str, *, pid: str | None = None, watch: bool = True,
              auto_render: bool = True, keep: list[str] | None = None,
              argv: list[str] | None = None, canvas_size: tuple[int, int] = (420, 320),
              guard_s: float | None = None) -> dict[str, Any]:
        abs_path = self.resolve(path)
        max_programs = int(self._cfg("max_programs", 8))
        with self._map_lock:
            if abs_path in self._by_path:
                prog = self.programs[self._by_path[abs_path]]
                return {"program": prog.id, "reused": True, "status": prog.status()}
            if len(self.programs) >= max_programs:
                raise SkeletonKeyError(E.TOO_LARGE,
                                       f"live program cap reached ({max_programs})",
                                       hint="live.stop an old program, or raise [live] max_programs")
            base = os.path.splitext(os.path.basename(abs_path))[0] or "program"
            prog_id = pid or base
            n = 2
            while prog_id in self.programs:
                prog_id = f"{base}-{n}"
                n += 1
            prog = LiveProgram(prog_id, abs_path, display=self._display_of(abs_path),
                               canvas_size=canvas_size, keep=keep, argv=argv)
            prog.auto_render = auto_render
            data = prog.load_initial(
                guard_s=guard_s if guard_s is not None else float(self._cfg("exec_guard_s", 10.0)))
            self.programs[prog_id] = prog
            self._by_path[abs_path] = prog_id
        if watch:
            self._ensure_watcher()
        return {"program": prog_id, "reused": False, "load": data, "status": prog.status()}

    def stop(self, pid: str | None = None) -> dict[str, Any]:
        with self._map_lock:
            if pid is None:
                victims = list(self.programs)
            else:
                if pid not in self.programs:
                    raise SkeletonKeyError(E.ENOENT, f"no live program {pid!r}",
                                           details={"programs": sorted(self.programs)})
                victims = [pid]
            stopped = []
            for vid in victims:
                prog = self.programs.pop(vid)
                self._by_path.pop(prog.path, None)
                stopped.append(vid)
            if not self.programs and self.watcher is not None:
                self.watcher.stop()
                self.watcher = None
        return {"stopped": stopped, "remaining": sorted(self.programs)}

    def get(self, pid: str | None) -> LiveProgram:
        if pid is None:
            if len(self.programs) == 1:
                return next(iter(self.programs.values()))
            if not self.programs:
                raise SkeletonKeyError(E.ENOENT, "no live programs are running",
                                       hint="live.start a python file first",
                                       next_actions=[{"tool": "live.status", "args": {}}])
            raise SkeletonKeyError(E.BAD_ARGS, "multiple live programs; pass `program`",
                                   details={"programs": sorted(self.programs)})
        try:
            return self.programs[pid]
        except KeyError:
            raise SkeletonKeyError(E.ENOENT, f"no live program {pid!r}",
                                   details={"programs": sorted(self.programs)}) from None

    # ----------------------------------------------------------------- watcher
    def _ensure_watcher(self) -> dict[str, Any]:
        with self._map_lock:
            if self.watcher is None:
                self.watcher = FileWatcher(
                    self._watch_targets(), self._on_files_changed,
                    interval_s=float(self._cfg("watch_interval_s", 0.35)),
                    debounce_s=float(self._cfg("debounce_ms", 120)) / 1000.0)
                self.watcher.start()
            else:
                self.watcher.targets = self._watch_targets()
            return self.watcher.status()

    def _watch_targets(self) -> list[str]:
        targets: set[str] = set()
        for prog in self.programs.values():
            targets.add(prog.path)
            targets.update(prog.dep_paths())
        if not targets:
            targets.add(os.getcwd())
        return sorted(targets)

    def watch_status(self) -> dict[str, Any]:
        out: dict[str, Any] = {"watchfiles_available": watchfiles_available()}
        if self.watcher is None:
            out["watching"] = False
        else:
            out.update(self.watcher.status())
            out["watching"] = self.watcher.running
        return out

    def _on_files_changed(self, paths: list[str]) -> None:
        """Watcher thread -> here. A program's own file reloads it; a
        dependency file patches that module in every program that imported
        it. Dep changes re-render through `reload_dependency`'s auto path."""
        changed = {os.path.abspath(p) for p in paths}
        with self._map_lock:
            programs = list(self.programs.values())
        touched: list[str] = []
        for prog in programs:
            guard = float(self._cfg("exec_guard_s", 10.0))
            if prog.path in changed:
                prog.reload(guard_s=guard, reason="watch")
                touched.append(prog.id)
                # its imports may have changed which files we must watch
                if self.watcher is not None:
                    self.watcher.targets = self._watch_targets()
                continue
            for mod_name, entry in prog.deps.items():
                if os.path.abspath(entry["path"]) in changed:
                    rep = prog.reload_dependency(mod_name, guard_s=guard)
                    if rep.ok:
                        touched.append(f"{prog.id}<-{mod_name}")
                    else:
                        prog.frame_error = dict(rep.error or {})
                        prog.frame_version += 1
        self._broadcast()

    # ------------------------------------------------------------------- misc
    def fanout_frame(self, prog: LiveProgram) -> None:
        """After a REPL-driven render, so the preview moves instantly."""
        self._broadcast()

    def add_frame_listener(self, fn: Any) -> None:
        self._frame_listeners.append(fn)

    def _broadcast(self) -> None:
        for fn in list(self._frame_listeners):
            try:
                fn()
            except Exception:
                pass

    def status(self) -> dict[str, Any]:
        with self._map_lock:
            programs = [p.status() for p in self.programs.values()]
        return {
            "programs": programs,
            "count": len(programs),
            "watch": self.watch_status(),
            "sandboxed": self.sandbox is not None,
            "panel": self.panel.status() if self.panel else {"serving": False},
            "config": {
                "watch_interval_s": float(self._cfg("watch_interval_s", 0.35)),
                "debounce_ms": int(self._cfg("debounce_ms", 120)),
                "max_programs": int(self._cfg("max_programs", 8)),
                "exec_guard_s": float(self._cfg("exec_guard_s", 10.0)),
                "auto_render_default": bool(self._cfg("auto_render", True)),
            },
        }

    def history(self, pid: str | None = None, *, limit: int = 20) -> dict[str, Any]:
        prog = self.get(pid)
        return {"program": prog.id,
                "repl": list(prog.history)[-limit:],
                "patches": list(prog.patch_log)[-limit:]}

    # -------------------------------------------------------------- teardown
    def close(self) -> None:
        with self._map_lock:
            watcher, self.watcher = self.watcher, None
            panel, self.panel = self.panel, None
            self.programs.clear()
            self._by_path.clear()
        if watcher is not None:
            watcher.stop()
        if panel is not None:
            try:
                panel.stop()
            except Exception:
                pass
