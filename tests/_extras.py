"""Optional-dependency probes shared by the test suite.

`mcp` is an optional extra (ADR-0001: the core has no mandatory dependencies).
Tests needing a live MCP server must SKIP when it is absent rather than fail:
a skip states honestly that an optional dependency is missing, while a failure
is noise that teaches everyone to ignore a red suite. This repo lost real
signal to exactly that - a "known" 27-failure baseline that turned out to be
nothing but an unprovisioned environment.

Import this rather than reaching into conftest, which is not a normal module
and is not importable by name from every rootdir layout.
"""

from __future__ import annotations

import importlib.util

HAS_MCP: bool = importlib.util.find_spec("mcp") is not None
NO_MCP_REASON: str = "requires the optional [mcp] extra: pip install -e '.[mcp]'"
