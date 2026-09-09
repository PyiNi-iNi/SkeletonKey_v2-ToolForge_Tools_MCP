"""
Virtual Pointer - AI-driven cursor control.

Provides AI agents with system-level cursor tracking and positioning capabilities.
The virtual pointer operates independently of the physical mouse cursor.
"""

import logging
import datetime
from typing import Optional, Tuple, Dict, Any, Callable

from .errors import PointerError, VirtualPointerError, PointerErrorCode

logger = logging.getLogger(__name__)


class VirtualPointer:
    """
    AI-driven virtual pointer that can be positioned independently of the physical mouse.

    Features:
    - Get current virtual position (x, y)
    - Set virtual position (AI-driven movement)
    - Track cursor type under virtual pointer
    - Detect element under virtual pointer
    - Register hover callbacks
    - CV pairing for accuracy verification
    """

    def __init__(self, screen_width: int, screen_height: int):
        """
        Initialize the virtual pointer.

        Args:
            screen_width: Total screen width in pixels
            screen_height: Total screen height in pixels
        """
        self._screen_width = screen_width
        self._screen_height = screen_height
        self._x: int = 0
        self._y: int = 0
        self._cursor_type: str = "arrow"
        self._hover_callbacks: list[Callable[[Dict[str, Any]], None]] = []
        self._initialized: bool = False
        self._cv_threshold: Optional[int] = None  # pixels, for CV pairing

        logger.info("VirtualPointer initialized", extra={
            "screen_width": screen_width,
            "screen_height": screen_height,
            "initialized": False
        })

    @property
    def position(self) -> Tuple[int, int]:
        """Current virtual pointer position (x, y)."""
        return (self._x, self._y)

    @property
    def cursor_type(self) -> str:
        """Current cursor type under virtual pointer."""
        return self._cursor_type

    def get_position(self) -> Dict[str, Any]:
        """
        Get current virtual pointer position with metadata.

        Returns:
            Dict with x, y, timestamp, source, and cursor_type
        """
        import datetime
        from . import __version__ as skeletonkey_version

        event = {
            "timestamp": datetime.datetime.now().isoformat() + "Z",
            "source": "virtual",
            "x": self._x,
            "y": self._y,
            "cursor_type": self._cursor_type,
        }
        logger.debug("Virtual pointer position read", extra={"event": event})
        return event

    def set_position(self, x: int, y: int) -> Dict[str, Any]:
        """
        Set the virtual pointer position (AI-driven).

        Args:
            x: X coordinate in pixels
            y: Y coordinate in pixels

        Returns:
            Result dict with success/failure status and previous position

        Raises:
            VirtualPointerError: If coordinates are out of bounds
        """
        # Validate bounds
        if x < 0 or x >= self._screen_width:
            raise VirtualPointerError(
                PointerErrorCode.VPOINTER_OUT_OF_BOUNDS,
                f"X coordinate {x} is out of bounds (screen width: {self._screen_width})",
                {"requested_x": x, "screen_width": self._screen_width}
            )
        if y < 0 or y >= self._screen_height:
            raise VirtualPointerError(
                PointerErrorCode.VPOINTER_OUT_OF_BOUNDS,
                f"Y coordinate {y} is out of bounds (screen height: {self._screen_height})",
                {"requested_y": y, "screen_height": self._screen_height}
            )

        previous = {"x": self._x, "y": self._y}
        self._x = x
        self._y = y

        logger.info("Virtual pointer position set", extra={
            "new_position": {"x": x, "y": y},
            "previous_position": previous
        })

        event = {
            "timestamp": datetime.datetime.now().isoformat() + "Z",
            "source": "virtual",
            "x": x,
            "y": y,
            "cursor_type": self._cursor_type,
            "action": "set_position"
        }
        logger.debug("Virtual pointer position set event", extra={"event": event})

        return {
            "ok": True,
            "previous_position": previous,
            "new_position": {"x": x, "y": y},
            "cursor_type": self._cursor_type
        }

    def set_cursor_type(self, cursor_type: str) -> Dict[str, Any]:
        """
        Set the cursor type displayed at the virtual pointer position.

        Args:
            cursor_type: One of "arrow", "ibeam", "resize", "hand", "text", etc.

        Returns:
            Result dict with previous cursor type
        """
        from .errors import PointerErrorCode

        valid_types = ["arrow", "ibeam", "resize", "hand", "text", "crosshair", "wait", "help", "not-allowed"]
        if cursor_type not in valid_types:
            raise VirtualPointerError(
                PointerErrorCode.VPOINTER_INVALID_COORDINATES,
                f"Invalid cursor type: {cursor_type}. Must be one of {valid_types}",
                {"requested": cursor_type, "valid": valid_types}
            )

        previous = self._cursor_type
        self._cursor_type = cursor_type

        logger.info("Virtual pointer cursor type set", extra={
            "new_cursor_type": cursor_type,
            "previous_cursor_type": previous
        })

        return {
            "ok": True,
            "previous_cursor_type": previous,
            "new_cursor_type": cursor_type
        }

    def register_hover_callback(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """
        Register a callback to be called when the virtual pointer hovers over/leaves an element.

        Args:
            callback: Function that takes a dict with element info under cursor
        """
        self._hover_callbacks.append(callback)
        logger.debug("Hover callback registered", extra={"total_callbacks": len(self._hover_callbacks)})

    def _trigger_hover_callbacks(self, element_under_cursor: Dict[str, Any]) -> None:
        """Trigger all registered hover callbacks with current element info."""
        for callback in self._hover_callbacks:
            try:
                callback(element_under_cursor)
            except Exception as e:
                logger.error("Hover callback failed", exc_info=True, extra={"error": str(e)})

    def get_element_under_cursor(self) -> Optional[Dict[str, Any]]:
        """
        Get the GUI element under the virtual pointer position.

        Returns:
            Dict with element info, or None if cannot be determined
        """
        # In a real implementation, this would query the OS/Window server
        # For now, return None as element identification is OS-dependent
        element_info = {
            "id": None,
            "role": None,
            "name": None,
            "class": None
        }

        # Log the attempt
        logger.debug("Attempting to get element under virtual pointer", extra={
            "position": {"x": self._x, "y": self._y}
        })

        # Trigger callbacks with whatever info we have
        self._trigger_hover_callbacks(element_info)

        return element_info if any(v is not None for v in element_info.values()) else None

    def enable_cv_pairing(self, threshold_px: int = 5) -> Dict[str, Any]:
        """
        Enable computer vision pairing for position verification.

        Args:
            threshold_px: Maximum allowed discrepancy in pixels between
                         virtual position and CV-detected position

        Returns:
            Configuration confirmation
        """
        self._cv_threshold = threshold_px
        logger.info("CV pairing enabled for virtual pointer", extra={
            "threshold_px": threshold_px
        })

        return {
            "ok": True,
            "threshold_px": threshold_px,
            "message": f"CV pairing enabled with {threshold_px}px threshold"
        }

    def disable_cv_pairing(self) -> Dict[str, Any]:
        """Disable computer vision pairing."""
        self._cv_threshold = None
        logger.info("CV pairing disabled for virtual pointer")
        return {"ok": True, "message": "CV pairing disabled"}

    def get_state(self) -> Dict[str, Any]:
        """Get current full state of the virtual pointer."""
        return {
            "position": {"x": self._x, "y": self._y},
            "cursor_type": self._cursor_type,
            "screen_width": self._screen_width,
            "screen_height": self._screen_height,
            "initialized": self._initialized,
            "cv_pairing": {
                "enabled": self._cv_threshold is not None,
                "threshold_px": self._cv_threshold
            } if self._cv_threshold else {"enabled": False}
        }


# Singleton instance manager for multi-monitor setups
class VirtualPointerManager:
    """
    Manager for multiple virtual pointers (e.g., across different screens).
    """

    def __init__(self):
        self._pointers: Dict[str, VirtualPointer] = {}
        self._active_screen: str = "primary"

    def create_pointer(self, screen_id: str, screen_width: int, screen_height: int) -> VirtualPointer:
        """Create a new virtual pointer for the given screen."""
        pointer = VirtualPointer(screen_width, screen_height)
        pointer._initialized = True
        self._pointers[screen_id] = pointer
        logger.info("Virtual pointer created", extra={"screen_id": screen_id})
        return pointer

    def get_pointer(self, screen_id: str = "primary") -> VirtualPointer:
        """Get the virtual pointer for the specified screen."""
        if screen_id not in self._pointers:
            raise VirtualPointerError(
                PointerErrorCode.VPOINTER_NOT_AVAILABLE,
                f"No virtual pointer for screen: {screen_id}",
                {"available_screens": list(self._pointers.keys())}
            )
        return self._pointers[screen_id]

    @property
    def active_screen(self) -> str:
        """Return the currently active screen identifier."""
        return self._active_screen

    @active_screen.setter
    def active_screen(self, screen_id: str) -> None:
        """Set the active screen."""
        if screen_id in self._pointers or screen_id == "primary":
            self._active_screen = screen_id
            logger.info("Active screen changed", extra={"screen_id": screen_id})
        else:
            raise VirtualPointerError(
                PointerErrorCode.VPOINTER_NOT_AVAILABLE,
                f"Screen {screen_id} not available",
                {"available_screens": list(self._pointers.keys())}
            )