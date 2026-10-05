"""
VirtualBox host-side injection for TrackKeys Mirror.

One `VBoxController` connects to the local VirtualBox via the SDK's `vboxapi`.
Each running VM becomes a `Target` wrapping a shared-lock COM session and its
Keyboard / Mouse / Display interfaces. No software runs inside the guest beyond
Oracle's standard Guest Additions (needed for absolute-mouse positioning).

WINDOWS + VIRTUALBOX ONLY. Requires the VirtualBox SDK (provides `vboxapi`).
Cannot be run or verified off that stack; see DESIGN.md §14.

Note: `vboxapi` entry points vary slightly across VirtualBox versions
(e.g. how the session object is obtained). The spots most likely to need a
version tweak are marked "VERSION NOTE".
"""

# VirtualBox IMouse button-state bits.
MB_LEFT = 0x01
MB_RIGHT = 0x02
MB_MIDDLE = 0x04
MB_X1 = 0x08
MB_X2 = 0x10

_BUTTON_BITS = {
    "left": MB_LEFT, "right": MB_RIGHT, "middle": MB_MIDDLE,
    "x1": MB_X1, "x2": MB_X2,
}


def build_scancodes(scancode, extended, up):
    """Build the PS/2 set-1 byte sequence for one key transition."""
    code = scancode & 0xFF
    if up:
        code |= 0x80
    return ([0xE0, code] if extended else [code])


def button_mask(buttons):
    """Translate a set of pressed button names into VirtualBox's bitmask."""
    mask = 0
    for b in buttons:
        mask |= _BUTTON_BITS.get(b, 0)
    return mask


class Target:
    """A single VM we inject into."""

    def __init__(self, controller, name):
        self._c = controller
        self.name = name
        self.session = None
        self.console = None
        self.keyboard = None
        self.mouse = None
        self.display = None
        self.width = 1
        self.height = 1
        self.alive = False

    def attach(self):
        """Acquire a shared lock and grab injection interfaces. Returns bool."""
        mgr = self._c.mgr
        vbox = self._c.vbox
        const = self._c.const
        try:
            machine = vbox.findMachine(self.name)
            if machine.state != const.MachineState_Running:
                return False
            # VERSION NOTE: getSessionObject signature differs by version.
            self.session = mgr.getSessionObject(vbox)
            machine.lockMachine(self.session, const.LockType_Shared)
            self.console = self.session.console
            self.keyboard = self.console.keyboard
            self.mouse = self.console.mouse
            self.display = self.console.display
            self.refresh_resolution()
            self.alive = True
            return True
        except Exception as exc:  # noqa: BLE001 - log and mark dead
            print("[vbox] attach failed for %s: %s" % (self.name, exc))
            self._safe_unlock()
            return False

    def refresh_resolution(self):
        try:
            res = self.display.getScreenResolution(0)
            # (width, height, bpp, xOrigin, yOrigin, status)
            w, h = int(res[0]), int(res[1])
            if w > 0 and h > 0:
                self.width, self.height = w, h
        except Exception:
            pass  # keep last known resolution

    # -- injection ---------------------------------------------------------
    def send_key(self, ev):
        self._put_scancodes(build_scancodes(ev.scancode, ev.extended, ev.up))

    def send_break(self, scancode, extended):
        self._put_scancodes(build_scancodes(scancode, extended, up=True))

    def _put_scancodes(self, codes):
        if not self.alive:
            return
        try:
            self.keyboard.putScancodes(codes)
        except Exception as exc:  # noqa: BLE001
            self._die("putScancodes", exc)

    def send_mouse_abs(self, nx, ny, buttons, dz=0, dw=0):
        if not self.alive:
            return
        x = 1 + int(round(nx * (self.width - 1)))
        y = 1 + int(round(ny * (self.height - 1)))
        try:
            self.mouse.putMouseEventAbsolute(x, y, dz, dw, button_mask(buttons))
        except Exception as exc:  # noqa: BLE001
            self._die("putMouseEventAbsolute", exc)

    # -- lifecycle ---------------------------------------------------------
    def _die(self, where, exc):
        print("[vbox] %s on %s failed (%s); marking dead" % (where, self.name, exc))
        self.alive = False

    def _safe_unlock(self):
        try:
            if self.session is not None:
                self.session.unlockMachine()
        except Exception:
            pass
        self.session = self.console = None
        self.keyboard = self.mouse = self.display = None

    def close(self):
        self.alive = False
        self._safe_unlock()


class VBoxController:
    """Connects to the local VirtualBox and manages Targets."""

    def __init__(self):
        self.mgr = None
        self.vbox = None
        self.const = None

    def connect(self):
        from vboxapi import VirtualBoxManager  # imported lazily (SDK required)
        self.mgr = VirtualBoxManager(None, None)
        self.vbox = self.mgr.getVirtualBox()
        self.const = self.mgr.constants
        print("[vbox] connected to VirtualBox %s" % self.vbox.version)

    def is_running(self, name):
        try:
            return self.vbox.findMachine(name).state == self.const.MachineState_Running
        except Exception:
            return False

    def list_machines(self):
        """Return [(name, is_running), ...] for every registered VM."""
        out = []
        for m in self.vbox.machines:
            try:
                out.append((m.name,
                            m.state == self.const.MachineState_Running))
            except Exception:
                pass
        out.sort(key=lambda t: t[0].lower())
        return out

    def make_target(self, name):
        return Target(self, name)
