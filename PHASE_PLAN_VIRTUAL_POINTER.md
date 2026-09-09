# Phase Plan and Capability - Virtual Pointer System

## Overview

This phase plan defines the implementation of a virtual pointer system driven by AI, operating alongside physical mouse input. The system enables AI agents to understand and interact with the GUI environment through a virtual pointer that tracks coordinates, hover state, and cursor operations, while simultaneously consuming physical pointer input without interference.

**Related Issue:** Virtual pointer for AI agent GUI interaction
**Status:** Proposed Phase
**Estimated Effort:** 15 engineer-days

---

## 1. What This Is

The virtual pointer system provides AI agents with a system-level view of the desktop pointer/cursor state, enabling:

- **AI-driven cursor positioning** - The AI can programmatically move the virtual pointer to specific coordinates
- **Hover detection** - AI knows what element the pointer is currently over
- **Coordinate tracking** - Precise pixel coordinates (x, y) with screen context
- **Parallel input consumption** - Both virtual (AI-driven) and physical (mouse/human) pointers operate simultaneously without mutual interference
- **Cursor/caret tracking** - Supports text input caret, custom cursors, and GUI element highlights
- **Computer vision integration** - Can be paired with CV for accuracy verification

The physical pointer continues to operate as normal via the OS/mouse driver, with no code required for basic functionality. The virtual pointer is an additional API-driven input source that the AI can query and manipulate.

---

## 2. Design Principles

| # | Principle | Description |
|---|-----------|-------------|
| 1 | **Non-interfering parallel input** | Virtual and physical pointers operate independently; AI actions don't displace the physical mouse position and vice versa |
| 2 | **System-level awareness** | Virtual pointer integrates with OS-level cursor tracking where possible |
| 3 | **Extensive logging & debugging** | All pointer operations are logged with timestamps, coordinates, and success/failure status |
| 4 | **Error resilience** | Failures in virtual pointer tracking don't break physical pointer operation |
| 5 | **CV pairing capability** | Optional computer vision overlay for accuracy verification and element detection |
| 6 | **Px-precision coordinates** | All positions tracked in screen pixels with optional DPI scaling awareness |

---

## 3. Capability Specifications

### 3.1 Virtual Pointer (AI-Driven)

| Feature | Description |
|---------|-------------|
| **OS-level integration** | hooks into system cursor tracking; aware of screen resolution, DPI, multi-monitor setup |
| **AI-driven movement** | AI can set virtual pointer position via API: `set_virtual_position(x, y)` |
| **Hover state tracking** | AI is notified when virtual pointer enters/leaves GUI elements |
| **Coordinate tracking** | Continuous tracking of px coordinates: `(x, y)` with timestamp |
| **Cursor/caret detection** | Distinguishes between arrow cursor, text I-beam, resize cursors, and text carets |
| **Element identification** | At each position, reports the underlaying GUI element/control |
| **Cross-platform** | Windows (Win32 API), Linux (X11/Wayland), macOS (Quartz) |

### 3.2 Physical Pointer (Mouse/Human)

| Feature | Description |
|---------|-------------|
| **No code necessary** | Standard mouse driver input; automatically available |
| **Simultaneous consumption** | System delivers both virtual and physical input events to the application |
| **Independent tracking** | Physical pointer position tracked separately from virtual |
| **Mouse operations** | Clicks, scrolls, drags tracked with source attribution (virtual vs physical) |

### 3.3 Simultaneous/Parallel Input Consumption

| Feature | Description |
|---------|-------------|
| **Non-interfering** | Virtual pointer moves don't affect physical cursor position and vice versa |
| **Event sourcing** | All pointer events tagged with `source: "virtual"` or `source: "physical"` |
| **Priority resolution** | When both virtual and physical target the same element, physical takes precedence for safety |
| **Consume vs query** | AI can "consume" events (mark as handled) or merely "query" current state |

### 3.4 Tracking and Data

| Tracked Data | Format | Notes |
|--------------|--------|-------|
| **px coordinates** | `(int x, int y)` | Screen-relative or window-relative |
| **timestamp** | ISO 8601 or monotonic | When the reading was taken |
| **source** | `"virtual"` or `"physical"` | Origin of the pointer state |
| **cursor type** | `"arrow"`, `"ibeam"`, `"resize"`, `"hand"`, `"text"` | Current cursor visual |
| **element under cursor** | `{"id": "...", "class": "...", "role": "..."}` | Accessible if available |
| **modifier keys** | `{"ctrl": bool, "shift": bool, "alt": bool}` | State at time of tracking |
| **button state** | `{"left": bool, "right": bool, "middle": bool}` | Currently pressed |

### 3.5 CV Pairing for Better Accuracy

| Feature | Description |
|---------|-------------|
| **CV overlay** | Optional screen capture + computer vision to verify virtual pointer position |
| **Element verification** | CV confirms the element the virtual pointer claims to be over |
| **Accuracy reporting** | Reports discrepancy between virtual position and CV-detected element |
| **Calibration** | CV can calibrate virtual pointer to actual screen pixels |
| **Fallback** | If CV fails, virtual pointer coordinates are still trusted |

### 3.6 Extensive Logging, Debugging, and Error Handling

| Aspect | Implementation |
|--------|----------------|
| **Operation logging** | Every `set_position`, `move`, `hover` event logged with full context |
| **Error codes** | Custom error taxonomy: `VPOINTER_OUT_OF_BOUNDS`, `VPOINTER_NOT_AVAILABLE`, `VPOINTER_CV_MISMATCH` |
| **Debug mode** | Verbose logging with coordinate dump, CV comparison screenshots |
| **Error handling** | Failures isolated; physical pointer always remains functional |
| **Recovery** | Automatic re-sync if virtual pointer loses track of physical position |
| **Audit trail** | NDJSON ledger records every pointer operation (similar to other tool outputs) |

---

## 4. Architecture

```
skeletonkey/
  pointer/                    # NEW: Virtual pointer subsystem
    virtual.py               # Virtual pointer API implementation
    physical.py              # Physical pointer tracking
    tracker.py               # Main tracker - merges virtual + physical
    cv_pairer.py             # Optional CV pairing module
    errors.py                # Pointer-specific error taxonomy
    logger.py                # Pointer operation logger
    schemas/                 # JSON schemas for pointer events
      pointer-event.schema.json
      virtual-move.schema.json
```

### 4.1 Core Components

| Component | Responsibility |
|-----------|----------------|
| `VirtualPointer` | AI-driven pointer; can set position, query state, register hover callbacks |
| `PhysicalPointer` | Wraps OS-level mouse tracking; provides position, events, source attribution |
| `PointerTracker` | Merges virtual + physical input; handles non-interference, source tracking |
| `CVPairing` (optional) | Computer vision overlay; verifies position, detects elements |
| `PointerLogger` | Audit trail of all pointer operations |
| `PointerError` | Error types and exception hierarchy |

### 4.2 Call Lifecycle

```
Engine.call("pointer.set_virtual_position") 
  → resolve → gate → validate → authorize → budget charge → 
  → normalize → estimate → dispatch → 
  → audit + reliability record in finally → envelope
```

---

## 5. Phase Plan (Effort Estimate)

### Phase VP1 - Foundations (3 engineer-days)
- [ ] Create `skeletonkey/pointer/` directory structure
- [ ] Implement error taxonomy (`pointer/errors.py`)
- [ ] Create JSON schemas for pointer events
- [ ] Implement basic VirtualPointer class with `get_position()`, `set_position()`
- [ ] Implement basic PhysicalPointer class wrapping OS tracking
- [ ] Set up minimal logging framework

### Phase VP2 - Parallel Input Consumption (3 engineer-days)
- [ ] Implement PointerTracker merging virtual + physical input
- [ ] Source attribution system (`source: "virtual"` vs `"physical"`)
- [ ] Non-interference guarantees (virtual moves don't displace physical)
- [ ] Modifier key and button state tracking

### Phase VP3 - Coordinate Tracking and Data (3 engineer-days)
- [ ] Px-coordinate tracking with screen/DPI awareness
- [ ] Cursor type detection and reporting
- [ ] Element-under-cursor identification
- [ ] Timestamp and source tagging on every read

### Phase VP4 - CV Pairing (3 engineer-days)
- [ ] Optional CVPairing class
- [ ] Screen capture integration
- [ ] Element verification via CV
- [ ] Accuracy reporting and calibration

### Phase VP5 - Logging, Debugging, and Error Handling (3 engineer-days)
- [ ] Comprehensive operation logging
- [ ] Error handling with custom error codes
- [ ] Debug mode with coordinate dumps
- [ ] Audit trail / NDJSON ledger integration
- [ ] Recovery mechanisms for lost track

### Phase VP6 - Documentation and Testing (2 engineer-days)
- [ ] Write unit tests for all pointer operations
- [ ] Integration tests with CV pairing
- [ ] Documentation of API and capabilities
- [ ] Example usage scripts

**Total: 17 engineer-days**

---

## 6. Acceptance Criteria

All checks green:

1. **Virtual pointer positioning** - `set_virtual_position(x, y)` moves virtual cursor without affecting physical mouse position
2. **Parallel input consumption** - Both virtual and physical pointers report independent positions; consuming one doesn't affect the other
3. **Px-coordinate tracking** - `get_virtual_position()` returns `(int x, int y)` within screen bounds; out-of-bounds returns error `VPOINTER_OUT_OF_BOUNDS`
4. **Cursor type tracking** - `get_cursor_type()` returns current cursor visual (`arrow`, `ibeam`, etc.)
5. **Element under cursor** - `get_element_under_cursor()` returns identifiable element or `null` if unavailable
6. **CV pairing** - When enabled, CV can verify virtual pointer position within tolerance; reports `VPOINTER_CV_MISMATCH` if discrepancy exceeds threshold
7. **Extensive logging** - Every pointer operation appears in the audit ledger with timestamp, coordinates, source, and success/failure status
8. **Error handling** - All error codes from defined taxonomy; failures don't break physical pointer operation
9. **Cross-platform** - At least Linux support implemented; Windows/macOS design documented for future

---

## 7. API Reference (Proposed)

```python
# Get current virtual pointer position
pos = await pointer.get_virtual_position()
# pos = {"x": 1234, "y": 567, "timestamp": "2026-01-15T10:30:00Z", "source": "virtual"}

# Set virtual pointer position (AI-driven)
result = await pointer.set_virtual_position(x=100, y=200)
# result = {"ok": True, "previous": {"x": ..., "y": ...}}

# Get current physical pointer position  
phys_pos = await pointer.get_physical_position()
# phys_pos = {"x": 1234, "y": 567, "source": "physical"}

# Get combined tracker state
state = await pointer.get_tracker_state()
# state = {
#   "virtual": {"x": ..., "y": ..., "cursor": "arrow"},
#   "physical": {"x": ..., "y": ..., "cursor": "ibeam"},
#   "last_updated": "2026-01-15T10:30:00Z"
# }

# Hover callback - AI notified when virtual pointer enters/leaves element
pointer.register_hover_callback(callback_func)

# CV pairing
pointer.enable_cv_pairing(threshold_px=5)  # warn if CV differs by >5px
```

---

## 8. Error Taxonomy

| Error Code | Meaning | Retryable |
|------------|---------|-----------|
| `VPOINTER_OUT_OF_BOUNDS` | Requested position outside screen | No |
| `VPOINTER_NOT_AVAILABLE` | Pointer tracking not initialized | Yes |
| `VPOINTER_CV_MISMATCH` | CV verification failed (discrepancy > threshold) | No |
| `VPOINTER_ELEMENT_NOT_FOUND` | Cannot identify element under cursor | No |
| `VPOINTER_ACCESS_DENIED` | OS denies access to pointer tracking | No |

---

## 9. Related ADRs and Docs

- ADR-0007: Non-interfering parallel input streams
- TOOL-CONTRACT.md: Envelope contract for pointer operations
- SECURITY-MODEL.md: Pointer access permissions and privacy
- Config: `pointer.enabled`, `pointer.cv_enabled`, `pointer.cv_threshold_px`

---

## 10. Migration / Future Work

- **Multi-monitor support** - Virtual pointer aware of spanning across multiple displays
- **Remote desktop integration** - Virtual pointer over RDP/VNC connections
- **Gesture support** - Drag, scroll, multi-finger gestures via virtual pointer
- **Accessibility** - Virtual pointer for screen reader interaction
- **AI gesture prediction** - AI predicts likely cursor movement paths for optimization
