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
    """Pointer position normalized to 0.0-1.0 across the virtual screen."""
    nx: float
    ny: float


@dataclass
class MouseButton:
    button: str      # "left" | "right" | "middle" | "x1" | "x2"
    down: bool


@dataclass
class MouseWheel:
    steps: int       # signed; one physical notch == 1 step
    horizontal: bool = False
