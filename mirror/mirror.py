"""
TrackKeys Mirror -- orchestrator / entry point.

Wires host input capture to VirtualBox injection: captures keyboard + mouse on
the local Windows 11 host and mirrors them live into N VirtualBox guests at
once. Local input is unaffected.

Run on the host:  python mirror.py [config.json]

Toggle mirroring with Ctrl+Alt+F. Ctrl+C in this console quits (releasing any
held keys in the guests first).

WINDOWS + VIRTUALBOX ONLY. See DESIGN.md and README.md.
"""

import json
import queue
import sys
import time

from events import KeyEvent, MouseMove, MouseButton, MouseWheel
from vboxctl import VBoxController

# Virtual-key codes used only to detect the toggle chord.
VK_CTRL = {0x11, 0xA2, 0xA3}
VK_ALT = {0x12, 0xA4, 0xA5}
VK_F = 0x46

DEFAULT_CONFIG = {
    "vms": [],
    "toggle_hotkey": "ctrl+alt+f",
    "mouse": {"enabled": True, "flush_hz": 200},
    "start_enabled": False,
    "reconcile_seconds": 3,
}


class Mirror:
    def __init__(self, config):
        self.cfg = config
        self.enabled = bool(config.get("start_enabled", False))
        self.mouse_enabled = bool(config.get("mouse", {}).get("enabled", True))
        self.flush_hz = max(30, int(config.get("mouse", {}).get("flush_hz", 200)))
        self.reconcile_seconds = float(config.get("reconcile_seconds", 3))

        self.queue = queue.Queue()
        self.controller = VBoxController()
        self.targets = {}            # vm name -> Target
        self.held = {}               # (scancode, extended) -> True
        self.ctrl = False
        self.alt = False
        self._swallow_f_up = False

        # Mouse state
        self.mnx = 0.5
        self.mny = 0.5
        self.buttons = set()
        self.mouse_dirty = False

    # -- setup -------------------------------------------------------------
    def connect(self):
        self.controller.connect()
        for name in self.cfg.get("vms", []):
            self.targets[name] = self.controller.make_target(name)
        self._reconcile(force=True)

    def _alive_targets(self):
        return [t for t in self.targets.values() if t.alive]

    # -- event handling ----------------------------------------------------
    def on_event(self, ev):
        # Runs in the capture thread -- just enqueue.
        self.queue.put(ev)

    def _handle(self, ev):
        if isinstance(ev, KeyEvent):
            self._handle_key(ev)
        elif isinstance(ev, MouseMove):
            self.mnx, self.mny = ev.nx, ev.ny
            self.mouse_dirty = True
        elif isinstance(ev, MouseButton):
            self._handle_button(ev)
        elif isinstance(ev, MouseWheel):
            self._handle_wheel(ev)

    def _handle_key(self, ev):
        # Track modifier state for the toggle chord.
        if ev.vk in VK_CTRL:
            self.ctrl = not ev.up
        elif ev.vk in VK_ALT:
            self.alt = not ev.up

        # Ctrl+Alt+F toggles mirroring and is not forwarded.
        if ev.vk == VK_F:
            if not ev.up and self.ctrl and self.alt:
                self._set_enabled(not self.enabled)
                self._swallow_f_up = True
                return
            if ev.up and self._swallow_f_up:
                self._swallow_f_up = False
                return

        if not self.enabled:
            return

        key = (ev.scancode, ev.extended)
        if ev.up:
            for t in self._alive_targets():
                t.send_key(ev)
            self.held.pop(key, None)
        else:
            self.held[key] = True
            for t in self._alive_targets():
                t.send_key(ev)

    def _handle_button(self, ev):
        if not (self.enabled and self.mouse_enabled):
            return
        if ev.down:
            self.buttons.add(ev.button)
        else:
            self.buttons.discard(ev.button)
        for t in self._alive_targets():
            t.send_mouse_abs(self.mnx, self.mny, self.buttons)

    def _handle_wheel(self, ev):
        if not (self.enabled and self.mouse_enabled):
            return
        dz = 0 if ev.horizontal else ev.steps
        dw = ev.steps if ev.horizontal else 0
        for t in self._alive_targets():
            t.send_mouse_abs(self.mnx, self.mny, self.buttons, dz=dz, dw=dw)

    def _flush_mouse(self):
        if not (self.enabled and self.mouse_enabled and self.mouse_dirty):
            self.mouse_dirty = False
            return
        for t in self._alive_targets():
            t.send_mouse_abs(self.mnx, self.mny, self.buttons)
        self.mouse_dirty = False

    # -- policy ------------------------------------------------------------
    def _set_enabled(self, value):
        self.enabled = value
        print("[mirror] mirroring %s" % ("ENABLED" if value else "disabled"))
        if not value:
            self._release_all_keys()
            self.buttons.clear()

    def _release_all_keys(self):
        for (scancode, extended) in list(self.held):
            for t in self._alive_targets():
                t.send_break(scancode, extended)
        self.held.clear()

    def _reconcile(self, force=False):
        for name, target in self.targets.items():
            running = self.controller.is_running(name)
            if running and not target.alive:
                if target.attach():
                    print("[mirror] attached to %s (%dx%d)"
                          % (name, target.width, target.height))
            elif target.alive and not running:
                print("[mirror] %s stopped; detaching" % name)
                target.close()

    # -- run loop ----------------------------------------------------------
    def run(self, capture):
        flush_interval = 1.0 / self.flush_hz
        last_flush = time.monotonic()
        last_reconcile = time.monotonic()
        print("[mirror] ready. Ctrl+Alt+F to toggle (currently %s). Ctrl+C to quit."
              % ("ON" if self.enabled else "off"))
        try:
            while True:
                try:
                    ev = self.queue.get(timeout=flush_interval)
                    self._handle(ev)
                except queue.Empty:
                    pass
                now = time.monotonic()
                if now - last_flush >= flush_interval:
                    self._flush_mouse()
                    last_flush = now
                if now - last_reconcile >= self.reconcile_seconds:
                    self._reconcile()
                    last_reconcile = now
        except KeyboardInterrupt:
            print("\n[mirror] shutting down")
        finally:
            self._release_all_keys()
            for t in self.targets.values():
                t.close()
            capture.stop()


def load_config(path):
    cfg = dict(DEFAULT_CONFIG)
    if path:
        with open(path, "r", encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    return cfg


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    try:
        cfg = load_config(path)
    except FileNotFoundError:
        sys.exit("Config file not found: %s (copy config.example.json)" % path)

    if not cfg.get("vms"):
        sys.exit("No VMs configured. List VirtualBox machine names in 'vms'.")

    # Import capture lazily so non-Windows machines can at least import the
    # rest of the package for inspection/tests.
    from capture import Capture

    mirror = Mirror(cfg)
    mirror.connect()
    capture = Capture(mirror.on_event)
    capture.start()
    mirror.run(capture)


if __name__ == "__main__":
    main()
