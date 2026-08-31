"""Shared fixtures. Everything runs against a temp workspace with its own roots,
state dir, and journal, so tests never touch the repo working tree.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skeletonkey.core.config import Config
from skeletonkey.core.profile import CapabilityProfile, Prober, ShellProbe
from skeletonkey.toolkit import Toolkit, build


@pytest.fixture
def workspace(tmp_path):
    """A workspace with a small but realistic tree."""
    root = tmp_path / "ws"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "mod.py").write_text(
        "PORT = 8080\n\n\ndef handler(request):\n    return {'port': PORT}\n\n\nclass Server:\n    def __init__(self):\n        self.port = PORT\n",
        encoding="utf-8", newline="\n")
    (root / "src" / "pkg" / "util.py").write_text("def helper(x):\n    return x * 2\n", encoding="utf-8")
    (root / "README.md").write_text("# Demo\n\nSome text here.\n", encoding="utf-8")
    (root / ".env").write_text("API_TOKEN=sk-super-secret-value\n", encoding="utf-8")
    (root / "windows.txt").write_bytes(b"line one\r\nline two\r\n")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "junk.js").write_text("ignore me", encoding="utf-8")
    return root


@pytest.fixture
def config(workspace):
    return Config.load(cwd=str(workspace), overrides={
        "roots": [str(workspace)],
        "state": {"dir": str(workspace / ".sk")},
        "shell": {"tempdir": str(workspace / ".sk" / "shell")},
        "log_level": "ERROR",
    })


@pytest.fixture
def toolkit(config) -> Toolkit:
    tk = build(config=config)
    try:
        yield tk
    finally:
        tk.close()


@pytest.fixture
def engine(toolkit):
    return toolkit.engine


@pytest.fixture
def fs(toolkit):
    return toolkit.fs


@pytest.fixture
def sandbox(toolkit):
    return toolkit.sandbox


@pytest.fixture
def posix_only():
    if os.name == "nt":
        pytest.skip("posix-only shell test")


@pytest.fixture
def bash_available(toolkit):
    if "bash" not in toolkit.profile.available_dialects():
        pytest.skip("no bash on this host")


@pytest.fixture
def fake_profile():
    """A deterministic profile with no subprocess spawns: windows-ish + pwsh."""
    prof = CapabilityProfile(os="windows", os_release="Windows 11", arch="amd64",
                            python_version="3.12.0", is_admin=False)
    prof.shells = {
        "bash": ShellProbe(dialect="bash", kind="unix", path="C:/Program Files/Git/bin/bash.exe",
                          version=(5, 2, 15), supports_pipefail=True, supports_stdin_command=True),
        "pwsh": ShellProbe(dialect="pwsh", kind="powershell", path="C:/Program Files/PowerShell/7/pwsh.exe",
                           version=(7, 4, 2), supports_native_error_action=True,
                           supports_stdin_command=True, utf8_default=True),
        "powershell": ShellProbe(dialect="powershell", kind="powershell",
                                 path="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
                                 version=(5, 1, 0), notes=["legacy"]),
        "python": ShellProbe(dialect="python", kind="python", path="C:/Python312/python.exe", version=(3, 12, 0)),
    }
    prof.binaries = {"rg": "C:/tools/rg.exe", "git": "C:/tools/git.exe", "pwsh": prof.shells["pwsh"].path,
                     "bash": prof.shells["bash"].path}
    prof.versions = {"rg": "ripgrep 14.1.0", "git": "git version 2.45.0"}
    prof.capabilities = {"shell.powershell", "shell.unix", "search.ripgrep", "vcs.git", "fs.symlinks"}
    prof.filesystem = {"case_sensitive": False, "symlinks": True, "hardlinks": True, "fs_type": "ntfs",
                       "max_component": 255}
    prof.console = {"tty": False, "preferred_encoding": "cp1252", "code_page": 437, "color": False}
    prof.fingerprint = "fakeprofile000000"
    return prof


@pytest.fixture
def prober_no_probe():
    return Prober(run_probes=False)
