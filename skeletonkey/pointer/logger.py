"""
Pointer Operation Logger - Audit trail for all pointer operations.

Provides structured logging of virtual and physical pointer operations,
compatible with the overall SkeletonKey audit/ledger system.
"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, Any, Optional, List

from .errors import PointerError, PointerErrorCode

# Module-level logger
_pointer_logger = logging.getLogger("skeletonkey.pointer")

# Audit log file path (relative to workspace or configurable)
_AUDIT_LOG_PATH: Optional[str] = None
_audit_file = None


def init_audit_log(log_path: str = "pointer_audit.log") -> str:
    """
    Initialize the pointer audit log file.

    Args:
        log_path: Path to the audit log file (defaults to pointer_audit.log in cwd)

    Returns:
        Absolute path to the initialized log file
    """
    global _AUDIT_LOG_PATH, _audit_file

    # Resolve path - can be relative or absolute
    if not os.path.isabs(log_path):
        # Resolve relative to workspace root
        workspace = os.environ.get(
            "SKELETONKEY_WORKSPACE",
            "/home/user/SkeletonKey_v2-ToolForge_Tools_MCP"
        )
        log_path = os.path.join(workspace, log_path)

    _AUDIT_LOG_PATH = log_path

    # Ensure directory exists
    log_dir = os.path.dirname(log_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    # Open file in append mode, write header if new file
    if not os.path.exists(log_path) or os.path.getsize(log_path) == 0:
        with open(log_path, "w") as f:
            f.write("# SkeletonKey Pointer Audit Log\n")
            f.write(f"# Generated: {datetime.now().isoformat()}\n")
            f.write("# Format: NDJSON - one JSON object per line\n")
            f.write("# Fields: timestamp, source, operation, success, details\n")

    _audit_file = open(log_path, "a")
    _pointer_logger.info("Pointer audit log initialized", extra={"log_path": log_path})

    return _AUDIT_LOG_PATH


def write_audit_entry(
    timestamp: str,
    source: str,
    operation: str,
    success: bool,
    details: Optional[Dict[str, Any]] = None,
    error_code: Optional[str] = None
) -> None:
    """
    Write a single audit entry to the pointer audit log.

    Args:
        timestamp: ISO 8601 formatted timestamp
        source: "virtual" or "physical"
        operation: Name of the operation (e.g., "set_position", "get_position")
        success: Whether the operation succeeded
        details: Additional details dict
        error_code: PointerErrorCode value if failure
    """
    global _audit_file

    entry = {
        "timestamp": timestamp,
        "source": source,
        "operation": operation,
        "success": success,
    }

    if details:
        entry["details"] = details
    if error_code:
        entry["error_code"] = error_code

    # Write as NDJSON line
    line = json.dumps(entry, ensure_ascii=False)
    if _audit_file:
        _audit_file.write(line + "\n")
        _audit_file.flush()  # Ensure immediate write for audit purposes
    else:
        # Fallback: log via standard logger
        _pointer_logger.info("Pointer audit (no file)", extra={"entry": entry})


def close_audit_log() -> None:
    """Close the audit log file."""
    global _audit_file
    if _audit_file:
        _audit_file.close()
        _audit_file = None
        _pointer_logger.info("Pointer audit log closed")


def log_virtual_operation(
    operation: str,
    success: bool,
    **kwargs: Any
) -> None:
    """
    Log a virtual pointer operation to the audit trail.

    Args:
        operation: Operation name (e.g., "set_position", "get_position")
        success: Whether the operation succeeded
        **kwargs: Additional details to include
    """
    import datetime
    timestamp = datetime.datetime.now().isoformat() + "Z"
    details = kwargs

    write_audit_entry(
        timestamp=timestamp,
        source="virtual",
        operation=operation,
        success=success,
        details=details
    )


def log_physical_operation(
    operation: str,
    success: bool,
    **kwargs: Any
) -> None:
    """
    Log a physical pointer operation to the audit trail.

    Args:
        operation: Operation name (e.g., "get_position", "move")
        success: Whether the operation succeeded
        **kwargs: Additional details to include
    """
    import datetime
    timestamp = datetime.datetime.now().isoformat() + "Z"
    details = kwargs

    write_audit_entry(
        timestamp=timestamp,
        source="physical",
        operation=operation,
        success=success,
        details=details
    )


# Convenience functions for common operations
def log_virtual_set_position(x: int, y: int, success: bool, previous: Optional[Dict] = None, error: Optional[str] = None) -> None:
    """Log virtual set_position operation."""
    details = {"x": x, "y": y}
    if previous:
        details["previous"] = previous
    if error:
        details["error"] = error

    log_virtual_operation("set_position", success, **details)


def log_virtual_get_position(x: int, y: int, success: bool, error: Optional[str] = None) -> None:
    """Log virtual get_position operation."""
    details = {"x": x, "y": y}
    if error:
        details["error"] = error

    log_virtual_operation("get_position", success, **details)


def log_physical_get_position(x: int, y: int, success: bool, error: Optional[str] = None) -> None:
    """Log physical get_position operation."""
    details = {"x": x, "y": y}
    if error:
        details["error"] = error

    log_physical_operation("get_position", success, **details)


def log_cv_check(
    success: bool,
    discrepancy_px: float,
    within_threshold: bool,
    threshold_px: Optional[float] = None,
    error: Optional[str] = None
) -> None:
    """Log CV pairing check operation."""
    details = {
        "discrepancy_px": discrepancy_px,
        "within_threshold": within_threshold,
    }
    if threshold_px is not None:
        details["threshold_px"] = threshold_px
    if error:
        details["error"] = error

    log_virtual_operation("cv_pairing_check", success, **details)