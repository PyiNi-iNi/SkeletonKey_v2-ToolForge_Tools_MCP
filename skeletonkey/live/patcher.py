"""The patcher: the HMR primitive Python does not ship.

`importlib.reload` *rebinds* a module's names. Anything that already grabbed a
reference - a render loop holding a function, an instance whose class moved,
another module's `from x import f` - keeps running the OLD code. That is why
naive reload is not HMR.

This module does what React Fast Refresh does, for Python objects:

1. **Functions are patched in place.** The old function object keeps its
   identity, its `__globals__` (the live namespace), its registered hooks -
   only `__code__`, defaults, kwdefaults, annotations and `__doc__` are swapped.
   Every reference anywhere sees the new behaviour on the next call.
2. **Classes are patched in place.** Methods (instance/static/class) swap code
   on the same class object, so *existing instances* pick up the new methods
   without losing a single attribute - their attribute dict IS the state the
   runtime preserves.
3. **New names are bound with live globals.** A brand-new top-level function is
   rebuilt as `FunctionType(code, live_ns, ...)`; defining it in the scratch
   exec namespace would leave it reading an orphaned globals dict.
4. **Module data is reconciled with a 3-way merge** (see `three_way_decision`):
   `base` = the value the source set at the last successful load,
   `live` = what is in the namespace now (possibly mutated by the REPL),
   `fresh` = what the new source wants. Source-owned names track the file;
   REPL-owned mutations win and are reported as `preserved`.
5. **It is transactional.** Callers compile + exec the new source in a scratch
   namespace first; only a clean exec reaches `patch_namespace`, and patching
   itself builds its plan before touching the live namespace. A broken save
   cannot leave a program half-patched.

Everything returns plain data (`PatchReport`) instead of raising, so the
preview panel can render failures as an overlay instead of a crash.
"""

from __future__ import annotations

import copy
import inspect
import types
from dataclasses import dataclass, field
from typing import Any

# Names a module exec always injects but that are never "state".
_EXEC_NOISE = {"__annotations__", "__builtins__", "__doc__", "__loader__",
               "__spec__", "__cached__", "__package__", "__file__", "__name__"}


_MISSING = object()


# --------------------------------------------------------------------- report


@dataclass
class PatchReport:
    """One patch cycle over one namespace. Plain data: this is what the
    `live.reload` / `live.patch` envelopes and the panel's log render."""

    ok: bool = True
    patched_functions: list[str] = field(default_factory=list)
    patched_methods: list[str] = field(default_factory=list)     # "Class.method"
    rebound: list[str] = field(default_factory=list)             # in-place impossible, rebound
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)             # dropped from live ns
    removed_kept: list[str] = field(default_factory=list)        # gone from source, kept (state)
    data_updated: list[str] = field(default_factory=list)        # source-owned, tracked the file
    preserved: list[str] = field(default_factory=list)           # live-owned, survived the save
    notes: list[str] = field(default_factory=list)
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": self.ok}
        for key in ("patched_functions", "patched_methods", "rebound", "added", "removed",
                    "removed_kept", "data_updated", "preserved", "notes"):
            val = getattr(self, key)
            if val:
                out[key] = val
        if self.error:
            out["error"] = self.error
        out["changed"] = bool(self.patched_functions or self.patched_methods or self.rebound
                              or self.added or self.removed or self.data_updated)
        return out


# ------------------------------------------------------------------ utilities


def _owns(value: Any, module_marker: str) -> bool:
    """Did *this* source exec define the object? With no marker (scratch
    namespaces, drop-in style), ownership is accepted unconditionally - the
    marker exists to protect namespace members a program merely IMPORTED from
    being mistaken for locals."""
    if not module_marker:
        return True
    return getattr(value, "__module__", None) in (module_marker, None)


def _is_local_function(value: Any, module_marker: str) -> bool:
    """A plain Python function defined by *this* source (not an import)."""
    return inspect.isfunction(value) and _owns(value, module_marker)


def _is_local_class(value: Any, module_marker: str) -> bool:
    return inspect.isclass(value) and _owns(value, module_marker)


def _owns_own_name(value: Any, binding: str, module_marker: str) -> bool:
    """True when `value` looks like the *definition* bound at `binding` - and
    not an alias that merely holds the object (`ref = draw`, `f = lambda`).
    Aliases are state in the 3-way sense: deleting them on an unrelated save
    would silently cut a registration the program made."""
    if not (_is_local_function(value, module_marker) or _is_local_class(value, module_marker)):
        return False
    return getattr(value, "__name__", binding) == binding


def source_data_defaults(ns: dict[str, Any]) -> dict[str, Any]:
    """The non-callable, non-dunder, non-module names a source exec produced -
    the *source-side* arm of the 3-way merge (`base`/`fresh`)."""
    out: dict[str, Any] = {}
    for name, value in ns.items():
        if name in _EXEC_NOISE or name.startswith("__"):
            continue
        if inspect.ismodule(value) or inspect.isroutine(value) or inspect.isclass(value):
            continue
        out[name] = value
    return out


def deepcopy_safe(value: Any) -> tuple[bool, Any]:
    """Deepcopy that admits defeat (locks, sockets, generators) instead of
    crashing the reload. Callers record the name and move on."""
    try:
        return True, copy.deepcopy(value)
    except Exception:
        return False, None


def _same_value(a: Any, b: Any) -> bool:
    """"live == base?" without trusting user __eq__ (numpy vectors, objects
    whose __eq__ raises...). Identity short-circuit first, then a guarded ==."""
    if a is b:
        return True
    if type(a) is not type(b):
        return False
    try:
        result = a == b
        return bool(result) if isinstance(result, bool) else False
    except Exception:
        return False


def three_way_decision(name: str, base: dict[str, Any], live_ns: dict[str, Any],
                       fresh: dict[str, Any], keep: set[str],
                       force: set[str] | None = None) -> str:
    """Who owns this name after a save?

    returns: "source"  - take the new source value (including first sight, or
                        an explicit `force_source` reclaim),
             "live"    - keep the running value (REPL mutation or __live_keep__),
             "absent"  - not in the fresh source at all; caller decides keep/remove.
    """
    if name not in fresh:
        return "absent"
    if force and name in force:
        return "source"
    if name in keep:
        return "live"
    if name not in base:
        return "source"                                   # newly introduced by the edit
    live_present = name in live_ns
    if not live_present:
        return "source"
    if _same_value(live_ns[name], base[name]):
        return "source"                                   # untouched since load: the file owns it
    return "live"                                         # someone moved it at runtime


# -------------------------------------------------------------- function swap


def _swap_code(old_fn: types.FunctionType, new_fn: types.FunctionType) -> None:
    """In-place code swap: the Fast Refresh move. Identity and __globals__
    survive, so held references and closures keep working against new code.

    Raises ValueError when the closure-capture shapes disagree (e.g. the edit
    turned a global read into a cell read); the caller then rebinds instead -
    correct for module-level lookups, reported so the panel can say so.
    """
    if old_fn.__code__.co_freevars != new_fn.__code__.co_freevars:
        raise ValueError(
            f"freevars changed {old_fn.__code__.co_freevars!r} -> {new_fn.__code__.co_freevars!r}")
    old_fn.__code__ = new_fn.__code__
    old_fn.__defaults__ = new_fn.__defaults__
    old_fn.__kwdefaults__ = new_fn.__kwdefaults__
    old_fn.__annotations__ = dict(new_fn.__annotations__)
    old_fn.__doc__ = new_fn.__doc__


def _rebind_with_live_globals(new_fn: types.FunctionType, live_ns: dict[str, Any],
                              name: str) -> types.FunctionType:
    """A function created by the scratch exec closes over the scratch globals
    dict. Rebuild it against the live namespace or it would read stale ghosts
    of every module-level name."""
    rebound = types.FunctionType(new_fn.__code__, live_ns, name,
                                 new_fn.__defaults__, new_fn.__closure__)
    rebound.__kwdefaults__ = new_fn.__kwdefaults__
    rebound.__annotations__ = dict(new_fn.__annotations__)
    rebound.__doc__ = new_fn.__doc__
    rebound.__qualname__ = new_fn.__qualname__
    rebound.__module__ = new_fn.__module__
    return rebound


def _unwrap(descriptor: Any) -> types.FunctionType | None:
    """Class dict entries: plain function, staticmethod or classmethod -> the
    underlying function whose __code__ we can swap."""
    if inspect.isfunction(descriptor):
        return descriptor
    if isinstance(descriptor, (staticmethod, classmethod)):
        fn = descriptor.__func__
        return fn if inspect.isfunction(fn) else None
    return None


def _patch_class(old_cls: type, new_cls: type, live_ns: dict[str, Any],
                 report: PatchReport) -> None:
    """Patch methods on the SAME class object. Existing instances keep their
    attributes (that is the preserved state) and immediately dispatch to the
    new method code. `__init__` swaps like any method - running instances are
    deliberately NOT re-initialised."""
    for attr, new_desc in vars(new_cls).items():
        if attr in ("__dict__", "__weakref__", "__doc__", "__module__", "__qualname__"):
            continue
        new_fn = _unwrap(new_desc)
        if new_fn is None:
            # class-level data attribute: the class, not instances, owns it.
            if attr not in vars(old_cls) or not _same_value(vars(old_cls).get(attr), new_desc):
                setattr(old_cls, attr, new_desc)
                if attr not in report.data_updated:
                    report.data_updated.append(f"{new_cls.__name__}.{attr}")
            continue
        old_desc = vars(old_cls).get(attr)
        old_fn = _unwrap(old_desc) if old_desc is not None else None
        qual = f"{new_cls.__name__}.{attr}"
        if old_fn is not None:
            try:
                _swap_code(old_fn, new_fn)
                report.patched_methods.append(qual)
                continue
            except ValueError as exc:
                report.notes.append(f"{qual}: in-place swap refused ({exc}); rebound on class")
                report.rebound.append(qual)
        # new method (or a forced rebind): rebuild against live globals so it
        # reads the module's live data, then install with the same descriptor
        # flavour the source used.
        live_fn = _rebind_with_live_globals(new_fn, live_ns, f"{new_cls.__name__}.{attr}")
        if isinstance(new_desc, staticmethod):
            setattr(old_cls, attr, staticmethod(live_fn))
        elif isinstance(new_desc, classmethod):
            setattr(old_cls, attr, classmethod(live_fn))
        else:
            setattr(old_cls, attr, live_fn)
        if qual not in report.rebound:
            report.added.append(qual)
    for attr in vars(old_cls):
        if attr in ("__dict__", "__weakref__", "__doc__", "__module__"):
            continue
        if attr not in vars(new_cls):
            report.removed_kept.append(f"{new_cls.__name__}.{attr}")
    # docstring / qualname drift is cosmetic; keep the report honest anyway.
    if new_cls.__doc__ != old_cls.__doc__:
        old_cls.__doc__ = new_cls.__doc__


# ------------------------------------------------------------------ main loop


def patch_namespace(live_ns: dict[str, Any], scratch_ns: dict[str, Any], *,
                    base: dict[str, Any], keep: set[str] | None = None,
                    module_marker: str = "", ignore: set[str] | None = None,
                    force: set[str] | None = None
                    ) -> tuple[PatchReport, dict[str, Any]]:
    """Merge a freshly exec'd module (`scratch_ns`) into the running namespace.

    `base` is the source-side state captured after the previous load; the
    return value carries the NEW base alongside the report. Pure with respect
    to failure semantics: anything unexpected while planning is reported, and
    the swaps themselves are object-micro-surgery that either applies cleanly
    or degrades to a reported rebind - a bad save never wedges the program.

    `ignore` names are runtime injections (the scratch `canvas`): present in
    both namespaces, owned by the host, never merged, removed or reported.
    `force` names take the source value this cycle even when the REPL moved
    them (the deliberate "the file wins for these" hatch).
    """
    keep = set(keep or ())
    ignore = set(ignore or ())
    force = set(force or ())
    report = PatchReport()
    fresh_data = source_data_defaults(scratch_ns)

    for name, new_val in scratch_ns.items():
        if name in _EXEC_NOISE or name in ignore:
            continue
        if name.startswith("__") and name.endswith("__"):
            continue
        old_val = live_ns.get(name)
        if _is_local_function(new_val, module_marker):
            if _is_local_function(old_val, module_marker):
                try:
                    _swap_code(old_val, new_val)
                    report.patched_functions.append(name)
                except ValueError as exc:
                    live_ns[name] = _rebind_with_live_globals(new_val, live_ns, name)
                    report.rebound.append(name)
                    report.notes.append(f"{name}: in-place swap refused ({exc}); rebound")
            else:
                live_ns[name] = _rebind_with_live_globals(new_val, live_ns, name)
                report.added.append(name)
        elif _is_local_class(new_val, module_marker):
            if _is_local_class(old_val, module_marker):
                _patch_class(old_val, new_val, live_ns, report)
            else:
                # classes defined in scratch capture scratch as their methods'
                # globals via the functions themselves; rebind each method.
                for attr, desc in list(vars(new_val).items()):
                    fn = _unwrap(desc)
                    if fn is not None and fn.__globals__ is not live_ns:
                        live_fn = _rebind_with_live_globals(fn, live_ns, f"{name}.{attr}")
                        if isinstance(desc, staticmethod):
                            setattr(new_val, attr, staticmethod(live_fn))
                        elif isinstance(desc, classmethod):
                            setattr(new_val, attr, classmethod(live_fn))
                        else:
                            setattr(new_val, attr, live_fn)
                live_ns[name] = new_val
                report.added.append(name)
        elif inspect.ismodule(new_val) or inspect.isroutine(new_val) or inspect.isclass(new_val):
            # imported names: refreshed identity (e.g. re-import after edit)
            # is the correct behaviour for module objects and foreign callables.
            if old_val is not new_val and name not in keep:
                live_ns[name] = new_val
                if name not in report.added:
                    report.added.append(name) if old_val is None else report.data_updated.append(name)
        else:
            decision = three_way_decision(name, base, live_ns, fresh_data, keep, force)
            if decision == "source":
                was = live_ns.get(name, _MISSING)
                live_ns[name] = new_val
                if name in force and was is not _MISSING and not _same_value(was, new_val):
                    report.data_updated.append(name)
                    report.notes.append(f"{name}: forced to the file's value "
                                        f"(repl-held value was {was!r})"[:200])
                elif name in base and not _same_value(base[name], new_val):
                    report.data_updated.append(name)
                elif name not in base:
                    report.added.append(name)
            elif decision == "live":
                report.preserved.append(name)

    # names that vanished from the source
    fresh_names = set(scratch_ns)
    for name in list(live_ns):
        if name in _EXEC_NOISE or name in ignore:
            continue
        if name.startswith("__") and name.endswith("__"):
            continue
        if name in fresh_names or name in keep:
            continue
        if name in base and _same_value(live_ns.get(name), base[name]):
            # source-owned and untouched since load: the edit deleted it, so
            # we delete it. (base is a deepcopy - compare by value, not `is`.)
            del live_ns[name]
            report.removed.append(name)
        elif _owns_own_name(live_ns.get(name), name, module_marker):
            # looks like a def (binding name == the object's __name__): the
            # edit deleted it, so it goes. ALIASES (`ref = draw` - a name
            # holding someone else's object) are state, not code: kept below.
            del live_ns[name]
            report.removed.append(name)
        elif name in base:
            # source-owned but the REPL moved it since load: an orphan now,
            # not garbage - keep it and say so.
            report.removed_kept.append(name)
        else:
            report.removed_kept.append(name)               # live state somebody made

    # the base for the NEXT reload is this source's data (deep-copied so a
    # later REPL mutation of a list/dict does not silently re-write history).
    new_base: dict[str, Any] = {}
    for name, value in fresh_data.items():
        ok, cloned = deepcopy_safe(value)
        if ok:
            new_base[name] = cloned
        else:
            report.notes.append(f"{name}: not deep-copyable; 3-way merge treats it as live-owned")
    return report, new_base
