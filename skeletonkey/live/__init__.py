"""Live HMR subsystem: a Python hot-module-reload loop with a LiveREPL on top.

The pieces, in stack order (see docs/LIVE-HMR.md for the contract):

  scene     - a tiny retained scene graph that renders to SVG (2D shapes and
              orbit-projected 3D wireframes), dependency-free.
  patcher   - the HMR primitive Python lacks: in-place code swap for functions
              and classes, with a 3-way state merge so REPL-made mutations
              survive file saves.
  watcher   - file watching with a stdlib polling baseline; `watchfiles` is an
              optional fast path, mirroring the skills watcher precedent.
  runtime   - LiveProgram / LiveManager: load, reload, snapshot, render, and
              the LiveREPL eval loop, all behind one lock per program.
  panel     - the HTTP preview panel (frame, SSE/poll refresh, error overlay,
              in-page REPL).
  tools     - the `live.*` tool group registered with the engine.

Everything here is importable with zero third-party packages (ADR-0001).
"""

from .patcher import PatchReport, patch_namespace, source_data_defaults, three_way_decision
from .runtime import LiveManager, LiveProgram
from .scene import Scene
from .watcher import FileWatcher, PollBackend, watchfiles_available

__all__ = [
    "FileWatcher",
    "LiveManager",
    "LiveProgram",
    "PatchReport",
    "PollBackend",
    "Scene",
    "patch_namespace",
    "source_data_defaults",
    "three_way_decision",
    "watchfiles_available",
]
