"""
Pointer Tracker - Merges virtual and physical pointer input.

Provides a unified view of both virtual (AI-driven) and physical (mouse/human)
pointer states, ensuring non-interfering parallel consumption.
"""

import logging
from typing import Dict, Any, Optional, Tuple

from .virtual import VirtualPointer
from .physical import PhysicalPointer, get_physical_pointer
from .errors import PointerError, PointerErrorCode

logger = logging.getLogger(__name__)


class PointerTracker:
    """
    Merges virtual and physical pointer input into a unified tracker.

    Key capabilities:
    - Non-interfering: virtual moves don't displace physical and vice versa
    - Source attribution: every event tagged with "virtual" or "physical"
    - Combined state: get combined view of both pointers
    - CV pairing support: can integrate with computer vision
    - Debug mode: verbose logging of all operations
    """

    def __init__(self, virtual_pointer: VirtualPointer, physical_pointer: Optional[PhysicalPointer] = None):
        """
        Initialize the pointer tracker.

        Args:
            virtual_pointer: The VirtualPointer instance
            physical_pointer: The PhysicalPointer instance (defaults to global)
        """
        self._virtual = virtual_pointer
        self._physical = physical_pointer or get_physical_pointer()
        self._last_sync: Optional[str] = None
        self._debug_mode: bool = False
        self._sync_threshold_px: int = 10  # Max allowed discrepancy for sync check

        logger.info("PointerTracker initialized", extra={
            "virtual_initialized": self._virtual._initialized if hasattr(self._virtual, '_initialized') else False,
            "physical_initialized": self._physical._initialized if hasattr(self._physical, '_initialized') else False
        })

    def set_debug_mode(self, enabled: bool = True) -> Dict[str, Any]:
        """Enable or disable debug mode with verbose logging."""
        self._debug_mode = enabled
        self._virtual._debug_mode = enabled  # Access via internal reference
        self._physical._debug_mode = enabled

        logger.info("PointerTracker debug mode %s", "enabled" if enabled else "disabled")
        return {
            "ok": True,
            "debug_mode": enabled
        }

    def get_combined_state(self) -> Dict[str, Any]:
        """
        Get combined state of both virtual and physical pointers.

        Returns:
            Dict with both virtual and physical pointer states
        """
        virtual_event = self._virtual.get_position()
        physical_event = self._physical.get_position()

        # Calculate distance between virtual and physical positions
        vx, vy = virtual_event["x"], virtual_event["y"]
        px, py = physical_event["x"], physical_event["y"]
        distance_px = ((vx - px) ** 2 + (vy - py) ** 2) ** 0.5

        state = {
            "virtual": {
                "position": {"x": vx, "y": vy},
                "cursor_type": virtual_event.get("cursor_type", "arrow"),
                "source": "virtual"
            },
            "physical": {
                "position": {"x": px, "y": py},
                "cursor_type": physical_event.get("cursor_type", "arrow"),
                "source": "physical"
            },
            "distance_px": round(distance_px, 2),
            "last_sync": self._last_sync,
            "in_sync": distance_px <= self._sync_threshold_px,
            "timestamp": virtual_event.get("timestamp")
        }

        if self._debug_mode:
            logger.debug("Combined pointer state", extra={"state": state})

        return state

    def get_virtual_position(self) -> Dict[str, Any]:
        """Get just the virtual pointer position (convenience method)."""
        return self._virtual.get_position()

    def set_virtual_position(self, x: int, y: int) -> Dict[str, Any]:
        """
        Set the virtual pointer position.

        Guarantees: physical pointer position is NOT affected.
        """
        result = self._virtual.set_position(x, y)

        # Log the non-interference guarantee
        logger.info("Virtual pointer position set (non-interfering)", extra={
            "new_virtual": {"x": x, "y": y},
            "physical_unchanged": True
        })

        return result

    def get_physical_position(self) -> Dict[str, Any]:
        """Get just the physical pointer position (convenience method)."""
        return self._physical.get_position()

    def sync_virtual_to_physical(self) -> Dict[str, Any]:
        """
        Sync virtual pointer position to match physical pointer.

        Useful for re-calibration after losing track.
        """
        phys_state = self._physical.get_state()
        phys_pos = phys_state["position"]

        self._virtual.set_position(phys_pos["x"], phys_pos["y"])
        self._last_sync = __import__("datetime").datetime.now().isoformat() + "Z"

        logger.info("Virtual pointer synced to physical", extra={
            "sync_position": phys_pos,
            "timestamp": self._last_sync
        })

        return {
            "ok": True,
            "synced_position": phys_pos,
            "timestamp": self._last_sync
        }

    def check_cv_pairing(self, cv_x: int, cv_y: int) -> Dict[str, Any]:
        """
        Check CV-detected position against virtual pointer position.

        Args:
            cv_x: X coordinate from computer vision
            cv_y: Y coordinate from computer vision

        Returns:
            Dict with discrepancy info and any error flags
        """
        virtual_pos = self._virtual.get_position()
        vx, vy = virtual_pos["x"], virtual_pos["y"]

        cv_distance = ((vx - cv_x) ** 2 + (vy - cv_y) ** 2) ** 0.5
        threshold = self._virtual._cv_threshold if hasattr(self._virtual, '_cv_threshold') else None

        result = {
            "virtual_position": {"x": vx, "y": vy},
            "cv_position": {"x": cv_x, "y": cv_y},
            "discrepancy_px": round(cv_distance, 2),
            "cv_pairing_enabled": threshold is not None,
            "within_threshold": cv_distance <= (threshold or float('inf')),
            "timestamp": virtual_pos.get("timestamp")
        }

        if threshold and cv_distance > threshold:
            result["error"] = f"CV mismatch: {cv_distance:.1f}px > {threshold}px threshold"
            logger.warning("CV pairing mismatch detected", extra=result)
        else:
            logger.debug("CV pairing check passed", extra=result)

        return result

    def get_state(self) -> Dict[str, Any]:
        """Get full tracker state."""
        return {
            "virtual": self._virtual.get_state(),
            "physical": self._physical.get_state(),
            "tracker_config": {
                "sync_threshold_px": self._sync_threshold_px,
                "debug_mode": self._debug_mode,
                "last_sync": self._last_sync
            }
        }


# Convenience function to create a full tracker with both pointers
def build_tracker(screen_width: int = 1920, screen_height: int = 1080) -> PointerTracker:
    """
    Build a PointerTracker with initialized virtual and physical pointers.

    Args:
        screen_width: Total screen width in pixels
        screen_height: Total screen height in pixels

    Returns:
        Configured PointerTracker instance
    """
    import platform
    from .virtual import VirtualPointer

    # Create virtual pointer
    virtual = VirtualPointer(screen_width, screen_height)
    virtual._initialized = True

    # Create physical pointer (uses OS detection)
    from .physical import PhysicalPointer
    physical = PhysicalPointer()
    physical._initialized = True

    tracker = PointerTracker(virtual, physical)
    logger.info("PointerTracker built with screen dimensions", extra={
        "screen_width": screen_width,
        "screen_height": screen_height
    })

    return tracker