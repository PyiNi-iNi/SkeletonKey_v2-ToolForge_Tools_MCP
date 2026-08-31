"""Adaptive multi-shell execution: bash, PowerShell (7 + 5.1), and python.

The dialect renderer (dialect.py) owns *what text gets executed*; the runner
(base.py) owns *how it is spawned, bounded, and killed*. Keeping those apart is
what lets us unit-test Windows/PowerShell behaviour on a Linux box.
"""

from .base import BackgroundJob, SessionState, ShellOutcome, ShellRequest, ShellRunner
from .dialect import (
           RenderedScript,
           RenderOptions,
           decode_clixml,
           extract_json,
           parse_sentinel,
           render,
)

__all__ = ["BackgroundJob", "RenderOptions", "RenderedScript", "SessionState", "ShellOutcome",
           "ShellRequest", "ShellRunner", "decode_clixml", "extract_json", "parse_sentinel", "render"]
