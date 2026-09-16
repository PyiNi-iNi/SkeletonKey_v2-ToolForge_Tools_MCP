"""Shared pytest fixtures for the features platform.

Makes ``features.*`` importable without installation: the repo root is put
on ``sys.path``. Heavy third-party dependencies are never imported by tests
unless a test explicitly installs a mock for them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
