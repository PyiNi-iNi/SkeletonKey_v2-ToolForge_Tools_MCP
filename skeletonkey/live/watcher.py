"""File watching for the HMR loop: a stdlib polling baseline with an optional
`watchfiles` fast path.

The same shape as `skeletonkey/skills/watch.py`: `watchfiles` is an *extra*,
never an ImportError - a locked-down agent box gets the polling backend and a
`backend` field in `live.status` that says exactly what it got. Polling is
deterministic and step-able (`PollBackend.scan()` is called directly by tests;
no sleeps, no flakes).

Detachment rule: the watcher never reloads by itself. It emits changed paths;
`LiveManager` decides what those paths mean (which programs depend on them).
"""

from __future__ import annotations

import fnmatch
import hashlib
import importlib.util
import os
import threading
import time
from collections.abc import Callable
from typing import Any

# directory components that can only be editor noise inside a watch root
_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".venv", "venv",
              "node_modules", ".idea", ".vscode"}
_SKIP_GLOBS = ("*.pyc", "*.pyo", ".*.swp", ".*.swx", "*~")


def watchfiles_available() -> bool:
    return importlib.util.find_spec("watchfiles") is not None


def _signature(path: str, *, deep: bool) -> str | None:
    """Cheap identity for 'did this file change': stat tuple, optionally
    salted with a content hash so same-mtime writes (tests, `cp -p`) still
    register. None = gone."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    sig = f"{st.st_mtime_ns}:{st.st_size}"
    if deep:
        try:
            with open(path, "rb") as fh:
                sig += ":" + hashlib.sha1(fh.read(1_000_000)).hexdigest()[:12]
        except OSError:
            return None
    return sig


class PollBackend:
    """Snapshot-and-diff over a set of files and directories. Pure and
    synchronous by design: `scan()` returns the changed/absent/new paths since
    the previous scan and advances the snapshot. No threads in here."""

    def __init__(self, targets: list[str], *, deep_hash: bool = True) -> None:
        self.targets = [os.path.abspath(t) for t in targets]
        self.deep_hash = deep_hash
        self.known: dict[str, str] = {}       # path -> signature
        self.missing: set[str] = set()        # waited-for files not yet created
        self._primed = False                  # first scan is the baseline, never an event

    def _walk(self) -> dict[str, str]:
        found: dict[str, str] = {}
        for target in self.targets:
            if os.path.isfile(target):
                sig = _signature(target, deep=self.deep_hash)
                if sig is not None:
                    found[os.path.abspath(target)] = sig
            elif os.path.isdir(target):
                for dirpath, dirnames, filenames in os.walk(target):
                    dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
                    for fn in filenames:
                        if any(fnmatch.fnmatch(fn, g) for g in _SKIP_GLOBS):
                            continue
                        if not fn.endswith(".py"):
                            continue
                        p = os.path.join(dirpath, fn)
                        sig = _signature(p, deep=self.deep_hash)
                        if sig is not None:
                            found[p] = sig
            else:
                self.missing.add(os.path.abspath(target))
        return found

    def scan(self) -> list[str]:
        """One synchronous sweep; returns paths that changed / appeared /
        vanished since last scan. The first ever scan only primes the
        baseline: starting the watcher is not itself a change."""
        found = self._walk()
        if not self._primed:
            self.known = found
            self._primed = True
            self.missing = {t for t in self.targets if not os.path.exists(t)}
            return []
        changed: list[str] = []
        for path, sig in found.items():
            if self.known.get(path) != sig:
                changed.append(path)
        for path in self.known:
            if path not in found and path not in self.missing:
                changed.append(path)          # deleted: note it once
                self.missing.add(path)
        for path in found:
            self.missing.discard(path)
        self.known = found
        return sorted(changed)


class FileWatcher:
    """A daemon thread that polls (or natively watches) targets and calls
    `on_change([paths...])` with a debounced, coalesced batch. Editors write
    save -> rename -> metadata as 3-5 events; one reload per save is the
    contract, so bursts inside `debounce_s` collapse into one callback."""

    def __init__(self, targets: list[str], on_change: Callable[[list[str]], Any], *,
                 interval_s: float = 0.35, debounce_s: float = 0.12,
                 backend: str = "auto") -> None:
        self.targets = [os.path.abspath(t) for t in targets]
        self.on_change = on_change
        self.interval_s = max(0.05, float(interval_s))
        self.debounce_s = max(0.0, float(debounce_s))
        if backend == "auto":
            backend = "watchfiles" if watchfiles_available() else "poll"
        if backend == "watchfiles" and not watchfiles_available():
            backend = "poll"
        self.backend = backend
        self.errors: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.events = 0                        # batches delivered
        self.last_batch: list[str] = []

    # ------------------------------------------------------------------ loop
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        name = "sk-live-watch"
        if self.backend == "watchfiles":
            self._thread = threading.Thread(target=self._run_watchfiles, name=name, daemon=True)
        else:
            self._thread = threading.Thread(target=self._run_poll, name=name, daemon=True)
        self._thread.start()

    def stop(self, *, join_s: float = 2.0) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=join_s)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _deliver(self, paths: list[str]) -> None:
        if not paths:
            return
        time.sleep(self.debounce_s)            # collect the save's aftershocks
        self.events += 1
        self.last_batch = sorted(set(paths))
        try:
            self.on_change(self.last_batch)
        except Exception as exc:               # a bad callback must not kill watching
            self.errors.append(f"{type(exc).__name__}: {exc}")
            del self.errors[:-10]

    def _run_poll(self) -> None:
        backend = PollBackend(self.targets)
        backend.scan()                          # baseline: startup is not a change
        while not self._stop.wait(self.interval_s):
            try:
                changed = backend.scan()
            except Exception as exc:
                self.errors.append(f"scan: {type(exc).__name__}: {exc}")
                del self.errors[:-10]
                continue
            if changed:
                self._deliver(changed)

    def _run_watchfiles(self) -> None:
        from watchfiles import Change, watch  # type: ignore[import-not-found]

        wanted = set(self.targets)

        def relevant(change: tuple[int, str]) -> bool:
            _kind, path = change
            ap = os.path.abspath(path)
            base = os.path.basename(ap)
            if not (ap.endswith(".py") or os.path.isdir(ap)):
                return False
            if any(fnmatch.fnmatch(base, g) for g in _SKIP_GLOBS):
                return False
            parts = set(ap.split(os.sep))
            if parts & _SKIP_DIRS:
                return False
            # exact file target, or inside a directory target (boundary-safe)
            return any(ap == w or ap.startswith(w.rstrip(os.sep) + os.sep) for w in wanted)

        try:
            for changes in watch(*self.targets, recursive=True,
                                 stop_event=self._stop, rust_timeout=int(self.interval_s * 1000)):
                paths = sorted({os.path.abspath(p) for kind, p in changes
                                if kind in (Change.added, Change.modified, Change.deleted)
                                and relevant((kind, p))})
                if paths:
                    self._deliver(paths)
        except Exception as exc:
            self.errors.append(f"watchfiles: {type(exc).__name__}: {exc}")

    def status(self) -> dict[str, Any]:
        return {"backend": self.backend, "running": self.running, "targets": self.targets,
                "interval_s": self.interval_s, "debounce_s": self.debounce_s,
                "batches": self.events, "last_batch": self.last_batch,
                "watchfiles_installed": watchfiles_available(),
                **({"errors": list(self.errors)} if self.errors else {})}
