"""ui_manager — Gradio UI, bake-off mode, state preservation, auth.

Gradio is optional (lazy import); the same auth/state/bake-off cores power
a headless console UI and a small HTTP auth server, so the feature is
fully testable (and deployable in terminals) without Gradio.
"""

from __future__ import annotations

from .auth import DeviceFlow, NativeAuth, hash_password, verify_password
from .bakeoff import BakeOff, BakeOffEntry
from .state import Conversation, StateStore
from .app import HeadlessUI, build_app

__all__ = [
    "BakeOff",
    "BakeOffEntry",
    "Conversation",
    "DeviceFlow",
    "HeadlessUI",
    "NativeAuth",
    "StateStore",
    "build_app",
    "hash_password",
    "verify_password",
]
