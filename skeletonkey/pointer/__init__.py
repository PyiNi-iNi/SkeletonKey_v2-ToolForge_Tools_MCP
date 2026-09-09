"""
SkeletonKey Virtual Pointer Module.

Provides AI-driven virtual pointer capabilities alongside physical mouse tracking.
Supports parallel, non-interfering input consumption with extensive logging,
debugging, and error handling.

Key Components:
- VirtualPointer: AI-driven cursor control
- PhysicalPointer: OS-level mouse tracking
- PointerTracker: Merged view of both pointers
- CVPairing: Computer vision integration for accuracy verification
- Error taxonomy with POINTER_* error codes
- Audit trail via NDJSON ledger
"""

from .virtual import VirtualPointer, VirtualPointerManager
from .physical import PhysicalPointer, get_physical_pointer, reset_physical_pointer
from .tracker import PointerTracker, build_tracker
from .logger import (
    init_audit_log,
    close_audit_log,
    log_virtual_operation,
    log_physical_operation,
    log_virtual_set_position,
    log_virtual_get_position,
    log_physical_get_position,
    log_cv_check,
)
from .errors import (
    PointerError,
    PointerErrorCode,
    VirtualPointerError,
    PhysicalPointerError,
    CVError,
)

# Version info
import logging

__version__ = "0.1.0"
__api_version__ = "0.1"

# Public API exports
__all__ = [
    "VirtualPointer",
    "VirtualPointerManager",
    "PhysicalPointer",
    "get_physical_pointer",
    "reset_physical_pointer",
    "PointerTracker",
    "build_tracker",
    "init_audit_log",
    "close_audit_log",
    "log_virtual_operation",
    "log_physical_operation",
    "log_virtual_set_position",
    "log_virtual_get_position",
    "log_physical_get_position",
    "log_cv_check",
    "PointerError",
    "PointerErrorCode",
    "VirtualPointerError",
    "PhysicalPointerError",
    "CVError",
    "__version__",
    "__api_version__",
]

# Module-level logger
logger = logging.getLogger("skeletonkey.pointer")

# Default screen dimensions (will be updated based on actual display)
DEFAULT_SCREEN_WIDTH = 1920
DEFAULT_SCREEN_HEIGHT = 1080

logger.info(
    "SkeletonKey pointer module loaded (v%s)", __version__,
    extra={"module": "skeletonkey.pointer", "version": __version__}
)