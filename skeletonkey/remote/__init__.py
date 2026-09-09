"""Remote target capability probing (P7 exploratory).

Provides a read-only probe interface for discovering what a remote
target (Docker container, SSH host, local subprocess) can do before
any work is dispatched to it. The probe never mutates the target;
it collects OS, shell, filesystem layout, tool availability, and
resource limits into a structured report.

This is the foundation for future remote-target adapters. The current
implementation supports:

* ``local`` — probe the current process environment (always available).
* ``docker`` — probe a Docker container by exec (requires the Docker
  CLI; read-only commands only).
* ``ssh`` — probe a remote host via SSH (requires the ``ssh`` CLI;
  read-only commands only).

The journal always stays local; the probe only reads from the target.
"""

from .targets import (
    ProbeReport,
    ProbeResult,
    TargetSpec,
    probe_docker,
    probe_local,
    probe_ssh,
    probe_target,
)

__all__ = [
    "ProbeReport",
    "ProbeResult",
    "TargetSpec",
    "probe_docker",
    "probe_local",
    "probe_ssh",
    "probe_target",
]
