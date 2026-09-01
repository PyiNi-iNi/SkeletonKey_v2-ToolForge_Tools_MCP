"""Canned live programs for `sk live demo` and the docs.

The source text is the source of truth; `examples/live_hmr/orbital.py` is a
byte-identical copy for repo browsing (a test keeps them in sync). Keeping
the demo as data - not an import - matters: the demo's whole point is that
the file on disk is what the watcher reloads.
"""

ORBITAL_SRC = '''# LiveREPL playground (written by `sk live demo`). This file is WATCHED:
# save an edit and the preview panel updates; the running state below is
# preserved by the 3-way merge. Also alive from the panel's REPL console,
# `sk live repl "..."`, or an MCP host via live.repl.
import math

ticks = 0                 # frame counter: proof that state survives reloads
hue = "#58a6ff"           # try in the panel REPL:  hue = "#f2cc60"
zoom = 1.0                # camera scale; live: canvas.orbit(scale=1.4)
trail = []                # REPL-grown data persists across file saves
waves = 0                 # count of agent-handler invocations

# names in __live_keep__ ALWAYS survive reloads, even if a save edits them
__live_keep__ = ["trail"]

# a dict of callables is an agent/handler registry: the debugger panel at
# /agents lists it, and after a reload its entries are re-pointed at the
# freshly patched functions (Blueprint §4.2 registry rebinding).
__live_registries__ = ["HANDLERS"]


def about():
    return ("names: ticks hue zoom trail render() HANDLERS - edit the file, "
            "or assign in the REPL; state survives either way")


def handle_ping():
    """A demo 'agent action': callable from /agents, the REPL, or code."""
    global waves
    waves += 1
    return f"pong #{waves} (ticks={ticks})"


def handle_orbit_step(step=0.15):
    canvas.orbit(theta=canvas.camera["theta"] + step)
    return f"theta -> {canvas.camera['theta']:.2f}"


HANDLERS = {
    "ping": handle_ping,
    "orbit": handle_orbit_step,
}


def render():
    global ticks
    ticks += 1

    # --- HUD (2D) -----------------------------------------------------
    canvas.rect(12, 12, 168, 66, fill="#161b22", stroke="#30363d", rx=8, id="hud")
    canvas.text(22, 32, f"ticks={ticks}", anchor="start", fill="#e6edf3",
                font_size=13, id="hud-ticks")
    canvas.text(22, 50, "save-edits land live", anchor="start", fill="#3fb950",
                font_size=11, id="hud-live")
    canvas.text(22, 68, f"handler pings={waves}", anchor="start", fill="#bc8cff",
                font_size=11, id="hud-waves")

    # --- orbit ring (2D parametric) -----------------------------------
    for i in range(0, 75, 5):
        x = 210 + 95 * math.cos(i / 12)
        y = 175 + 62 * math.sin(i / 12)
        canvas.circle(x, y, 2.2, fill=hue, id=f"orb{i}")
    ang = ticks / 9.0
    canvas.circle(210 + 95 * math.cos(ang), 175 + 62 * math.sin(ang), 7,
                  fill="#f2cc60", id="planet")

    # --- mesh + wireframe cube (3D, ortho-projected in SVG; perspective in /view3d)
    peaks = [[-110, 24, -45], [-15, -58, 18], [62, 44, 52], [120, -12, -28]]
    canvas.mesh3d(peaks, [[0, 1, 2], [1, 3, 2]], id="mtn", fill=hue, opacity=0.9)
    canvas.cube3d(0, 0, 0, 64, id="cube", spin=ticks % 360,
                  stroke=hue, fill="none" if waves % 2 else "#1f6feb")
    canvas.camera["scale"] = zoom

    canvas.text(210, 302, "edit hue / render() / canvas.orbit(...) - no restart",
                fill="#8b949e", font_size=11, id="foot")
'''
