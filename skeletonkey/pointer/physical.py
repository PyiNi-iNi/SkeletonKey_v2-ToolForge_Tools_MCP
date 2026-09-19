"""
Physical Pointer - OS-level mouse cursor tracking.

Wraps operating system mouse input to provide physical pointer position,
button state, cursor type, and modifier key status. Operates independently
of the virtual pointer system.
"""

import logging
import platform
from typing import Optional, Tuple, Dict, Any

from .errors import PointerError, PhysicalPointerError, PointerErrorCode

logger = logging.getLogger(__name__)


class PhysicalPointer:
    """
    Physical mouse pointer tracker.

    Provides access to the OS-level mouse cursor position and state.
    This runs independently of the VirtualPointer - moves of one do not
    affect the other.
    """

    def __init__(self):
        """
        Initialize the physical pointer tracker.

        On Linux, uses X11/Xlib to track mouse position.
        On Windows, would use Win32 API.
        On macOS, would use Quartz/IOKit.
        """
        self._x: int = 0
        self._y: int = 0
        self._cursor_type: str = "arrow"
        self._button_state: Dict[str, bool] = {"left": False, "right": False, "middle": False}
        self._modifier_keys: Dict[str, bool] = {"ctrl": False, "shift": False, "alt": False, "meta": False}
        self._initialized: bool = False

        system = platform.system()
        logger.info("PhysicalPointer initialized", extra={"platform": system})

        # Attempt platform-specific initialization
        self._initialized = self._initialize_platform()

    def _initialize_platform(self) -> bool:
        """Platform-specific initialization for mouse tracking."""
        system = platform.system()
        try:
            if system == "Linux":
                return self._init_linux_x11()
            elif system == "Windows":
                return self._init_windows()
            elif system == "Darwin":
                return self._init_darwin()
            else:
                logger.warning(f"Physical pointer not supported on {system}")
                return False
        except Exception as e:
            logger.error(f"Failed to initialize physical pointer on {system}", exc_info=True)
            return False

    def _init_linux_x11(self) -> bool:
        """Initialize X11 mouse tracking."""
        try:
            import Xlib
            import Xlib.display
            display = Xlib.display.Display()
            root = display.screen().root
            # Get current mouse position
            cursor_info = root.query_pointer()
            self._x = cursor_info.root_x
            self._y = cursor_info.root_y
            # Get cursor shape
            cursor = cursor_info.cursor
            logger.debug("X11 physical pointer initialized", extra={"position": {"x": self._x, "y": self._y}})
            return True
        except ImportError:
            logger.warning("Xlib not available, physical pointer tracking disabled")
            return False
        except Exception as e:
            logger.error("X11 physical pointer initialization failed", exc_info=True)
            return False

    def _init_windows(self) -> bool:
        """Initialize Windows mouse tracking."""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            # Get cursor position
            point = ctypes.wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
            self._x = point.x
            self._y = point.y
            logger.debug("Windows physical pointer initialized", extra={"position": {"x": self._x, "y": self._y}})
            return True
        except ImportError:
            logger.warning("ctypes Windows support not fully available")
            return False
        except Exception as e:
            logger.error("Windows physical pointer initialization failed", exc_info=True)
            return False

    def _init_darwin(self) -> bool:
        """Initialize macOS mouse tracking."""
        try:
            # Would use IOKit/Quartz on macOS
            logger.debug("macOS physical pointer tracking (stub)")
            return True  # Stub - full implementation would use CoreGraphics
        except Exception as e:
            logger.error("macOS physical pointer initialization failed", exc_info=True)
            return False

    def update_state(self) -> Dict[str, Any]:
        """
        Update the physical pointer state from the OS.

        Must be called periodically to refresh the position and state.
        Returns current state dict.
        """
        system = platform.system()
        try:
            if system == "Linux":
                self._update_linux_x11()
            elif system == "Windows":
                self._update_windows()
            elif system == "Darwin":
                self._update_darwin()
        except Exception as e:
            logger.error("Failed to update physical pointer state", exc_info=True)

        event = {
            "timestamp": __import__("datetime").datetime.now().isoformat() + "Z",
            "source": "physical",
            "x": self._x,
            "y": self._y,
            "cursor_type": self._cursor_type,
            "button_state": self._button_state.copy(),
            "modifier_keys": self._modifier_keys.copy(),
        }
        logger.debug("Physical pointer state updated", extra={"event": event})
        return event

    def _update_linux_x11(self) -> None:
        """Update physical pointer state from X11."""
        try:
            import Xlib
            import Xlib.display
            display = Xlib.display.Display()
            root = display.screen().root
            cursor_info = root.query_pointer()
            self._x = cursor_info.root_x
            self._y = cursor_info.root_y
            # Update button state
            self._button_state = {
                "left": bool(cursor_info.state & 0x0001),
                "middle": bool(cursor_info.state & 0x0020),
                "right": bool(cursor_info.state & 0x0004),
            }
            # Note: modifier keys would need separate keyboard tracking
            logger.debug("X11 physical pointer state updated", extra={"position": {"x": self._x, "y": self._y}})
        except Exception as e:
            logger.error("X11 physical pointer state update failed", exc_info=True)

    def _update_windows(self) -> None:
        """Update physical pointer state from Windows."""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            point = ctypes.wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
            self._x = point.x
            self._y = point.y
            # Windows button state would require additional API calls
            logger.debug("Windows physical pointer state updated", extra={"position": {"x": self._x, "y": self._y}})
        except Exception as e:
            logger.error("Windows physical pointer state update failed", exc_info=True)

    def _update_darwin(self) -> None:
        """Update physical pointer state from macOS."""
        logger.debug("macOS physical pointer state update (stub)")

    @property
    def position(self) -> Tuple[int, int]:
        """Current physical pointer position (x, y)."""
        return (self._x, self._y)

    @property
    def cursor_type(self) -> str:
        """Current cursor type under physical pointer."""
        return self._cursor_type

    def get_position(self) -> Dict[str, Any]:
        """
        Get current physical pointer position with metadata.

        Returns:
            Dict with x, y, timestamp, source, and full state
        """
        return self.update_state()

    def get_state(self) -> Dict[str, Any]:
        """Get current full state of the physical pointer."""
        return {
            "position": {"x": self._x, "y": self._y},
            "cursor_type": self._cursor_type,
            "button_state": self._button_state.copy(),
            "modifier_keys": self._modifier_keys.copy(),
            "initialized": self._initialized,
        }

    def get_event(self) -> Dict[str, Any]:
        """Get a pointer event from the physical pointer (alias for get_position)."""
        return self.get_position()


# Global physical pointer instance
_physical_pointer: Optional[PhysicalPointer] = None


def get_physical_pointer() -> PhysicalPointer:
    """Get the global physical pointer instance, initializing if needed."""
    global _physical_pointer
    if _physical_pointer is None:
        _physical_pointer = PhysicalPointer()
    return _physical_pointer


def reset_physical_pointer() -> None:
    """Reset the global physical pointer instance."""
    global _physical_pointer
    _physical_pointer = None