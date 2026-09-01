"""P6 zero-dependency guarantee (ADR-0001): core imports with site-packages off.

A subprocess with `-S` never adds `site-packages` to the path; `PYTHONPATH` points
at the repo, so `import skeletonkey` can only resolve from *source*. Then we prove
no third-party package was pulled in alongside it — the promise an operator buys
when they `pip install skeletonkey-toolforge`. The `mcp` extra is deliberately
NOT imported by `toolkit`/`mcp.client` (it is imported lazily inside the
connector), and the transport module itself is allowed to need `mcp` — that is
exactly what an *extra* means.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_THIRD_PARTY = {"mcp", "watchfiles", "pydantic", "anyio", "yaml", "httpx",
                 "psutil", "numpy", "click", "rich", "requests", "dotenv"}

_CORE_PROBE = """
import sys
import skeletonkey.core
import skeletonkey.core.registry
import skeletonkey.core.semantic
import skeletonkey.core.profile
import skeletonkey.toolkit
import skeletonkey.mcp.client
import skeletonkey.live.patcher
import skeletonkey.live.runtime
leaked = sorted(set(%r) & set(m.split('.')[0] for m in sys.modules))
assert not leaked, 'core import leaked third-party packages: ' + ', '.join(leaked)
print('OK')
""".replace("%r", repr(sorted(_THIRD_PARTY)))


def _env() -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(REPO) + (os.pathsep + existing if existing else "")
    env.pop("VIRTUAL_ENV", None)
    return env


def test_core_probe_imports_clean_without_site_packages():
    """`import skeletonkey.toolkit` (and mcp.client/live) needs no third party
    and no installed distribution: it works under `python -S` where
    `site-packages` — including the editable install — simply doesn't exist."""
    p = subprocess.run([sys.executable, "-S", "-c", _CORE_PROBE], env=_env(),
                       capture_output=True, text=True, timeout=180)
    assert p.returncode == 0, p.stderr or p.stdout
    assert p.stdout.strip() == "OK"


def test_toolkit_does_not_import_the_mcp_extra():
    """The transport is an extra: a toolkit build that never touches remotes must
    not import `mcp` even when it is installed."""
    code = (
        "import sys\n"
        "import skeletonkey.toolkit\n"
        "import skeletonkey.mcp.client\n"
        "assert 'mcp' not in sys.modules, sorted(m for m in sys.modules if m.startswith('mcp'))\n"
        "print('OK')\n"
    )
    p = subprocess.run([sys.executable, "-c", code], env=_env(),
                       capture_output=True, text=True, timeout=180)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "OK"
