"""
Normalized input events shared between the capture layer and the orchestrator.

Keeping these independent of both the Windows capture specifics and the
VirtualBox injection specifics lets each side evolve on its own (see DESIGN.md
sections 6 and 7).
"""

from dataclasses import dataclass


@dataclass
class KeyEvent:
    """A single key transition.

    scancode:  PS/2 set-1 scancode (low byte significant).
    extended:  True for E0-prefixed keys (arrows, nav cluster, right Ctrl/Alt,
               numpad Enter/Divide, ...).
    up:        True = key released (break), False = key pressed (make).
    vk:        Windows virtual-key code, used only to detect the toggle chord.
    """
    scancode: int
    extended: bool
    up: bool
    vk: int = 0


@dataclass
class MouseMove:
    """A pointer movement.

    nx, ny:  position normalized to 0.0-1.0 across the control monitor
             (used for absolute positioning).
    dx, dy:  raw pixel delta since the previous move (used for relative /
             drag injection in hybrid and relative mouse modes).
    """
    nx: float
    ny: float
    dx: int = 0
    dy: int = 0


@dataclass
class MouseButton:
    button: str      # "left" | "right" | "middle" | "x1" | "x2"
    down: bool


@dataclass
class MouseWheel:
    steps: int       # signed; one physical notch == 1 step
    horizontal: bool = False
