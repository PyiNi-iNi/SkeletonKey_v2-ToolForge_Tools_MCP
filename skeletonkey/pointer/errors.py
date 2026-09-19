"""
Pointer Error Taxonomy

Error codes for the virtual pointer system.
All errors follow the pattern: POINTER_* with consistent naming.
"""

from enum import Enum, auto


class PointerErrorCode(Enum):
    """Enumeration of all pointer-related error codes."""

    #: General/unknown pointer error
    UNKNOWN = auto()

    #: Virtual pointer specific errors
    VPOINTER_OUT_OF_BOUNDS = auto()  # Requested position outside screen
    VPOINTER_NOT_AVAILABLE = auto()  # Pointer tracking not initialized
    VPOINTER_CV_MISMATCH = auto()    # CV verification failed (discrepancy > threshold)
    VPOINTER_ELEMENT_NOT_FOUND = auto()  # Cannot identify element under cursor
    VPOINTER_ACCESS_DENIED = auto()  # OS denies access to pointer tracking
    VPOINTER_INVALID_COORDINATES = auto()  # Invalid x or y values

    #: Physical pointer specific errors
    PHYSICAL_NOT_FOUND = auto()  # Physical pointer state not available
    PHYSICAL_LOST = auto()       # Physical pointer position lost

    #: Tracker/merge errors
    TRACKER_MISMATCH = auto()    # Virtual and physical pointer state mismatch
    TRACKER_CONFLICT = auto()    # Virtual and physical conflict resolution failure

    #: CV pairing errors
    CV_INITIALIZATION_FAILED = auto()  # Computer vision subsystem failed to initialize
    CV_CAPTURE_FAILED = auto()        # Failed to capture screen for CV
    CV_PROCESSING_FAILED = auto()     # CV processing error


class PointerError(Exception):
    """Base exception for all pointer-related errors."""

    def __init__(self, code: PointerErrorCode, message: str = "", details: dict | None = None):
        self.code = code
        self.message = message or self._default_message()
        self.details = details or {}
        super().__init__(self.message)

    def _default_message(self) -> str:
        return f"Pointer error {self.code.name}: {self.code.value}"

    def __str__(self):
        return f"[{self.code.name}] {self.message}"


class VirtualPointerError(PointerError):
    """Exception raised for virtual pointer errors."""

    def __init__(self, code: PointerErrorCode, message: str = "", details: dict | None = None):
        super().__init__(code, message, details)


class PhysicalPointerError(PointerError):
    """Exception raised for physical pointer errors."""

    def __init__(self, code: PointerErrorCode, message: str = "", details: dict | None = None):
        super().__init__(code, message, details)


class CVError(PointerError):
    """Exception raised for computer vision pairing errors."""

    def __init__(self, code: PointerErrorCode, message: str = "", details: dict | None = None):
        super().__init__(code, message, details)