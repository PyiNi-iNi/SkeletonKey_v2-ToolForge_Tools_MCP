"""Remote target capability probing implementation (P7 exploratory).

Read-only probes that discover what a target can do. No mutations,
no credential storage, no persistent connections. The probe runs a
small set of diagnostic commands and collects the output into a
structured report.

Security contract:
  * Every probe command is read-only (uname, whoami, df, etc.).
  * Credentials for SSH/Docker come from the caller's environment
    (ssh-agent, Docker socket); the probe never stores them.
  * Timeouts are enforced; a hung probe is killed, not retried.
  * The before-image is always explicit: the report records what
    was observed, not what was assumed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Literal

TargetKind = Literal["local", "docker", "ssh"]


@dataclass
class TargetSpec:
    """Specification for a remote target.

    Attributes
    ----------
    kind:
        The target type: ``local``, ``docker``, or ``ssh``.
    name:
        Human-readable label for the target.
    container:
        Docker container name or ID (kind=docker).
    host:
        SSH host (kind=ssh).
    user:
        SSH user (kind=ssh).
    port:
        SSH port (kind=ssh, default 22).
    timeout_s:
        Per-command timeout for probe commands.
    """

    kind: TargetKind = "local"
    name: str = ""
    container: str = ""
    host: str = ""
    user: str = ""
    port: int = 22
    timeout_s: float = 10.0

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind, "name": self.name or self.kind}
        if self.container:
            d["container"] = self.container
        if self.host:
            d["host"] = self.host
        if self.user:
            d["user"] = self.user
        if self.kind == "ssh" and self.port != 22:
            d["port"] = self.port
        d["timeout_s"] = self.timeout_s
        return d


@dataclass
class ProbeResult:
    """Result of a single probe command.

    Attributes
    ----------
    command:
        The command that was run.
    ok:
        Whether the command succeeded (exit code 0).
    stdout:
        Captured stdout (trimmed to 4KB).
    stderr:
        Captured stderr (trimmed to 1KB).
    duration_ms:
        How long the command took.
    """

    command: str = ""
    ok: bool = False
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "ok": self.ok,
            "stdout": self.stdout[:200],  # summary in dict
            "stderr": self.stderr[:200],
            "duration_ms": self.duration_ms,
        }


@dataclass
class ProbeReport:
    """Full probe report for a target.

    Attributes
    ----------
    target:
        The target specification.
    os_name:
        Operating system name (from uname / platform).
    os_version:
        OS version string.
    arch:
        CPU architecture.
    hostname:
        The target's hostname.
    user:
        The effective user.
    shells_available:
        List of shell executables found on PATH.
    python_version:
        Python version if available.
    tools_found:
        Map of tool name -> path for common tools.
    filesystem:
        Basic filesystem info (mount points, free space).
    resource_limits:
        ulimit-style resource limits (where available).
    results:
        The individual probe command results.
    duration_ms:
        Total probe duration.
    error:
        Error message if the probe failed entirely.
    """

    target: TargetSpec = field(default_factory=TargetSpec)
    os_name: str = ""
    os_version: str = ""
    arch: str = ""
    hostname: str = ""
    user: str = ""
    shells_available: list[str] = field(default_factory=list)
    python_version: str = ""
    tools_found: dict[str, str] = field(default_factory=dict)
    filesystem: dict[str, Any] = field(default_factory=dict)
    resource_limits: dict[str, Any] = field(default_factory=dict)
    results: list[ProbeResult] = field(default_factory=list)
    duration_ms: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "target": self.target.to_dict(),
            "os": {
                "name": self.os_name,
                "version": self.os_version,
                "arch": self.arch,
            },
            "hostname": self.hostname,
            "user": self.user,
            "shells_available": self.shells_available,
            "python_version": self.python_version,
            "tools_found": self.tools_found,
            "filesystem": self.filesystem,
            "resource_limits": self.resource_limits,
            "probe_count": len(self.results),
            "probe_ok_count": sum(1 for r in self.results if r.ok),
            "duration_ms": self.duration_ms,
        }
        if self.error:
            d["error"] = self.error
        return d


# ---- Probe commands --------------------------------------------------------

_PROBE_COMMANDS: list[tuple[str, str]] = [
    ("uname -a", "os_info"),
    ("whoami", "user"),
    ("hostname", "hostname"),
    ("echo $SHELL", "default_shell"),
    ("which bash sh zsh fish pwsh powershell 2>/dev/null || true", "shells"),
    ("python3 --version 2>/dev/null || python --version 2>/dev/null || true", "python"),
    ("which git rg rg.exe jq curl docker ssh 2>/dev/null || true", "tools"),
    ("df -h / 2>/dev/null || true", "filesystem"),
    ("ulimit -a 2>/dev/null || true", "limits"),
    ("nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || true", "cpus"),
    ("free -h 2>/dev/null || sysctl -n hw.memsize 2>/dev/null || true", "memory"),
]

# Tools we look for in the probe
_COMMON_TOOLS = [
    "git", "rg", "jq", "curl", "docker", "ssh",
    "python3", "python", "node", "npm", "go", "rustc",
    "make", "cmake", "gcc", "clang",
]

_SHELLS = ["bash", "sh", "zsh", "fish", "pwsh", "powershell"]


# ---- Execution backends ----------------------------------------------------

def _run_local(cmd: str, timeout: float) -> ProbeResult:
    """Run a probe command locally."""
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration = int((time.monotonic() - t0) * 1000)
        return ProbeResult(
            command=cmd,
            ok=proc.returncode == 0,
            stdout=proc.stdout[:4096],
            stderr=proc.stderr[:1024],
            duration_ms=duration,
        )
    except subprocess.TimeoutExpired:
        duration = int((time.monotonic() - t0) * 1000)
        return ProbeResult(
            command=cmd,
            ok=False,
            stderr=f"timed out after {timeout}s",
            duration_ms=duration,
        )
    except OSError as exc:
        return ProbeResult(
            command=cmd,
            ok=False,
            stderr=str(exc)[:1024],
            duration_ms=int((time.monotonic() - t0) * 1000),
        )


def _run_docker(cmd: str, container: str, timeout: float) -> ProbeResult:
    """Run a probe command inside a Docker container."""
    docker_cmd = f"docker exec {container} sh -c {_shell_quote(cmd)}"
    return _run_local(docker_cmd, timeout)


def _run_ssh(cmd: str, host: str, user: str, port: int, timeout: float) -> ProbeResult:
    """Run a probe command over SSH."""
    target = f"{user}@{host}" if user else host
    ssh_cmd = (
        f"ssh -o ConnectTimeout={int(timeout)} -o BatchMode=yes "
        f"-o StrictHostKeyChecking=no -p {port} {target} {_shell_quote(cmd)}"
    )
    return _run_local(ssh_cmd, timeout)


def _shell_quote(s: str) -> str:
    """Quote a string for shell embedding."""
    return "'" + s.replace("'", "'\\''") + "'"


# ---- Parsers ---------------------------------------------------------------

def _parse_results(results: list[ProbeResult]) -> dict[str, Any]:
    """Parse probe results into structured fields."""
    info: dict[str, Any] = {
        "os_name": "",
        "os_version": "",
        "arch": "",
        "hostname": "",
        "user": "",
        "shells_available": [],
        "python_version": "",
        "tools_found": {},
        "filesystem": {},
        "resource_limits": {},
    }

    for r in results:
        if not r.ok or not r.stdout.strip():
            continue

        cmd = r.command

        if "uname -a" in cmd:
            parts = r.stdout.strip().split()
            if len(parts) >= 1:
                info["os_name"] = parts[0]
            if len(parts) >= 3:
                info["os_version"] = parts[2]
            if len(parts) >= 12:
                info["arch"] = parts[-1]

        elif "whoami" in cmd:
            info["user"] = r.stdout.strip()

        elif "hostname" in cmd and "uname" not in cmd:
            info["hostname"] = r.stdout.strip()

        elif "SHELL" in cmd:
            shell_path = r.stdout.strip()
            if shell_path:
                shell_name = os.path.basename(shell_path)
                if shell_name and shell_name not in info["shells_available"]:
                    info["shells_available"].append(shell_name)

        elif "which bash" in cmd:
            for shell in _SHELLS:
                for line in r.stdout.strip().split("\n"):
                    if shell in line and "/" in line:
                        if shell not in info["shells_available"]:
                            info["shells_available"].append(shell)
                        break

        elif "python" in cmd and "--version" in cmd:
            info["python_version"] = r.stdout.strip() or r.stderr.strip()

        elif "which git" in cmd:
            for tool in _COMMON_TOOLS:
                for line in r.stdout.strip().split("\n"):
                    line = line.strip()
                    if tool in line and "/" in line:
                        info["tools_found"][tool] = line.split()[-1]
                        break

        elif "df -h" in cmd:
            lines = r.stdout.strip().split("\n")
            if len(lines) >= 2:
                values = lines[1].split()
                info["filesystem"] = {
                    "root_total": values[1] if len(values) > 1 else "",
                    "root_used": values[2] if len(values) > 2 else "",
                    "root_available": values[3] if len(values) > 3 else "",
                }

        elif "ulimit" in cmd:
            for line in r.stdout.strip().split("\n"):
                if "open files" in line.lower():
                    parts = line.split()
                    if parts:
                        info["resource_limits"]["open_files"] = parts[-1]
                elif "max user processes" in line.lower():
                    parts = line.split()
                    if parts:
                        info["resource_limits"]["max_processes"] = parts[-1]

        elif "nproc" in cmd or "hw.ncpu" in cmd:
            info["resource_limits"]["cpus"] = r.stdout.strip()

        elif "free -h" in cmd or "hw.memsize" in cmd:
            info["resource_limits"]["memory"] = r.stdout.strip()[:100]

    return info


# ---- Public API ------------------------------------------------------------

def probe_local(*, timeout_s: float = 10.0) -> ProbeReport:
    """Probe the local environment."""
    spec = TargetSpec(kind="local", name="localhost", timeout_s=timeout_s)
    return probe_target(spec)


def probe_docker(
    container: str,
    *,
    name: str = "",
    timeout_s: float = 10.0,
) -> ProbeReport:
    """Probe a Docker container (read-only)."""
    spec = TargetSpec(
        kind="docker",
        name=name or f"docker:{container}",
        container=container,
        timeout_s=timeout_s,
    )
    return probe_target(spec)


def probe_ssh(
    host: str,
    *,
    user: str = "",
    port: int = 22,
    name: str = "",
    timeout_s: float = 10.0,
) -> ProbeReport:
    """Probe a remote host over SSH (read-only)."""
    spec = TargetSpec(
        kind="ssh",
        name=name or f"ssh:{host}",
        host=host,
        user=user,
        port=port,
        timeout_s=timeout_s,
    )
    return probe_target(spec)


def probe_target(spec: TargetSpec) -> ProbeReport:
    """Run all probe commands against the specified target.

    Returns a ProbeReport regardless of individual command success;
    the report's ``error`` field is set only if the probe could not
    run at all (e.g., Docker CLI not found for a Docker target).
    """
    t0 = time.monotonic()
    report = ProbeReport(target=spec)
    results: list[ProbeResult] = []

    # Preflight: check that the target is reachable
    if spec.kind == "docker":
        if not shutil.which("docker"):
            report.error = "docker CLI not found"
            report.duration_ms = int((time.monotonic() - t0) * 1000)
            return report
    elif spec.kind == "ssh":
        if not shutil.which("ssh"):
            report.error = "ssh CLI not found"
            report.duration_ms = int((time.monotonic() - t0) * 1000)
            return report
        if not spec.host:
            report.error = "SSH target requires a host"
            report.duration_ms = int((time.monotonic() - t0) * 1000)
            return report

    # Run probe commands
    for cmd, _label in _PROBE_COMMANDS:
        if spec.kind == "local":
            result = _run_local(cmd, spec.timeout_s)
        elif spec.kind == "docker":
            result = _run_docker(cmd, spec.container, spec.timeout_s)
        elif spec.kind == "ssh":
            result = _run_ssh(cmd, spec.host, spec.user, spec.port, spec.timeout_s)
        else:
            result = ProbeResult(command=cmd, ok=False, stderr=f"unknown target kind: {spec.kind}")
        results.append(result)

    # Parse results
    info = _parse_results(results)
    report.results = results
    report.os_name = info["os_name"]
    report.os_version = info["os_version"]
    report.arch = info["arch"]
    report.hostname = info["hostname"]
    report.user = info["user"]
    report.shells_available = info["shells_available"]
    report.python_version = info["python_version"]
    report.tools_found = info["tools_found"]
    report.filesystem = info["filesystem"]
    report.resource_limits = info["resource_limits"]
    report.duration_ms = int((time.monotonic() - t0) * 1000)

    return report
