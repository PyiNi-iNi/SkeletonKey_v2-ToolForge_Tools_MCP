"""Per-session notification bookkeeping (V12).

`McpBridge` was written for stdio, where "one server, one client" is true and
a single `_digest` slot is fine. `--transport streamable-http` broke that
assumption without anyone updating the bookkeeping, so:

- `_digest` is one value shared by every connected client. The first session
  notified advances it, and every other session is then told nothing, because
  the digest already matches. They keep serving a stale tools/list.
- `session` is a single slot overwritten by whichever client called most
  recently, so a notification can go to the wrong client entirely.

These tests use a fake session rather than a live server: the bug is in the
bookkeeping, and a fake makes the starvation observable directly instead of
inferred from a passing handshake.
"""

from __future__ import annotations

import asyncio

import pytest

from tests._extras import HAS_MCP, NO_MCP_REASON

pytestmark = pytest.mark.skipif(not HAS_MCP, reason=NO_MCP_REASON)


class FakeSession:
    """Records the notifications it was sent."""

    def __init__(self, name: str, *, fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.notifications = 0

    async def send_tool_list_changed(self) -> None:
        if self.fail:
            raise RuntimeError(f"{self.name} is disconnected")
        self.notifications += 1


def _bridge(toolkit):
    from skeletonkey.mcp.adapter import McpBridge

    return McpBridge(toolkit)


def _listed(bridge) -> None:
    """Simulate a client having called tools/list, which arms notifications."""
    bridge._digest = bridge.advertise().digest


def _change_the_toolset(toolkit) -> None:
    res = toolkit.engine.call("integrations.load", {"name": "sqlite"})
    assert res.ok, res.error


# --------------------------------------------------------------------------

def test_every_connected_session_is_notified(toolkit):
    """The bug: one shared digest means the first session starves the rest."""
    from skeletonkey.mcp.adapter import notify_tools_changed

    bridge = _bridge(toolkit)
    a, b, c = FakeSession("a"), FakeSession("b"), FakeSession("c")
    for s in (a, b, c):
        bridge.track_session(s)
    _listed(bridge)

    _change_the_toolset(toolkit)
    asyncio.run(notify_tools_changed(bridge, a))

    assert a.notifications == 1
    assert b.notifications == 1, "session b was never told the toolset changed"
    assert c.notifications == 1, "session c was never told the toolset changed"


def test_no_notification_when_nothing_changed(toolkit):
    """The existing guard must survive: a client that gets list_changed after
    every call learns to ignore it."""
    from skeletonkey.mcp.adapter import notify_tools_changed

    bridge = _bridge(toolkit)
    a = FakeSession("a")
    bridge.track_session(a)
    _listed(bridge)

    asyncio.run(notify_tools_changed(bridge, a))
    assert a.notifications == 0


def test_exactly_one_notification_per_change(toolkit):
    """Not one per file event, not one per call - one per settled change."""
    from skeletonkey.mcp.adapter import notify_tools_changed

    bridge = _bridge(toolkit)
    a = FakeSession("a")
    bridge.track_session(a)
    _listed(bridge)

    _change_the_toolset(toolkit)
    for _ in range(5):
        asyncio.run(notify_tools_changed(bridge, a))

    assert a.notifications == 1


def test_a_dead_session_does_not_block_the_others(toolkit):
    """One disconnected client must not swallow everyone else's notification."""
    from skeletonkey.mcp.adapter import notify_tools_changed

    bridge = _bridge(toolkit)
    dead, alive = FakeSession("dead", fail=True), FakeSession("alive")
    bridge.track_session(dead)
    bridge.track_session(alive)
    _listed(bridge)

    _change_the_toolset(toolkit)
    asyncio.run(notify_tools_changed(bridge, dead))

    assert alive.notifications == 1, "a dead peer starved a live session"


def test_a_dead_session_is_dropped(toolkit):
    """Otherwise the list grows without bound for a long-lived http server."""
    from skeletonkey.mcp.adapter import notify_tools_changed

    bridge = _bridge(toolkit)
    dead = FakeSession("dead", fail=True)
    bridge.track_session(dead)
    _listed(bridge)

    _change_the_toolset(toolkit)
    asyncio.run(notify_tools_changed(bridge, dead))

    assert dead not in bridge.sessions


def test_no_notification_before_anyone_listed(toolkit):
    """Arming only on tools/list is deliberate: telling a client its list
    changed before it ever asked for one is noise."""
    from skeletonkey.mcp.adapter import notify_tools_changed

    bridge = _bridge(toolkit)
    a = FakeSession("a")
    bridge.track_session(a)
    assert bridge._digest is None

    _change_the_toolset(toolkit)
    asyncio.run(notify_tools_changed(bridge, a))
    assert a.notifications == 0


def test_tracking_is_idempotent(toolkit):
    """on_list_tools runs on every list; the same session must not stack up."""
    bridge = _bridge(toolkit)
    a = FakeSession("a")
    for _ in range(4):
        bridge.track_session(a)
    assert list(bridge.sessions).count(a) == 1


def test_the_calling_session_is_tracked_even_if_never_registered(toolkit):
    """notify_tools_changed is called with a session from the request context;
    it must work even when that session was never explicitly tracked."""
    from skeletonkey.mcp.adapter import notify_tools_changed

    bridge = _bridge(toolkit)
    _listed(bridge)
    lone = FakeSession("lone")

    _change_the_toolset(toolkit)
    asyncio.run(notify_tools_changed(bridge, lone))
    assert lone.notifications == 1


def test_none_session_is_not_tracked(toolkit):
    bridge = _bridge(toolkit)
    bridge.track_session(None)
    assert list(bridge.sessions) == []
