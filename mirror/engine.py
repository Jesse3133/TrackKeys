"""
TrackKeys Mirror -- engine.

The orchestrator logic, lifted out of the terminal entry point so it can be
driven by either the CLI (mirror.py) or the GUI (gui.pyw). It runs its loop on
a background worker thread, owns all VirtualBox COM access on that thread, and
communicates with the caller through two thread-safe queues:

  * commands  (caller -> engine): start/stop, which VMs, enable, monitor, ...
  * status    (engine -> caller): log lines, VM list, live stats, lifecycle.

The caller NEVER touches VirtualBox COM or engine internals directly -- it only
enqueues commands and drains status. This keeps COM on one apartment/thread and
keeps the UI responsive.
"""

import queue
import threading
import time

import vboxctl
from events import KeyEvent, MouseMove, MouseButton, MouseWheel
from vboxctl import VBoxController

# Virtual-key codes used only to detect the toggle chord.
VK_CTRL = {0x11, 0xA2, 0xA3}
VK_ALT = {0x12, 0xA4, 0xA5}
VK_F = 0x46


class Engine:
    def __init__(self):
        # Caller-facing queues.
        self.status = queue.Queue()      # engine -> caller (dicts with "kind")
        self._cmd = queue.Queue()        # caller -> engine
        self._evq = queue.Queue()        # capture -> engine (input events)

        # Configuration (set before start(), or via commands afterwards).
        self.monitor = "primary"
        self.mouse_enabled = True
        # "absolute": always absolute positioning (lockstep, but drags can feel
        #             off because the guest derives drag velocity from absolute
        #             input).
        # "hybrid":   absolute when idle, relative deltas while a button is
        #             held (native-feeling drags), re-sync to absolute on
        #             release. Keeps multi-VM lockstep. (default)
        # "relative": always relative deltas (best drag feel, but cursors can
        #             drift apart across VMs).
        self.mouse_mode = "hybrid"
        self.flush_hz = 200
        self.reconcile_seconds = 3.0
        self.target_names = []
        self.launch_type = "gui"   # "gui" or "headless" for launch_vm

        # Runtime state (worker thread only).
        self.controller = None
        self.targets = {}                # name -> Target
        self.held = {}                   # (scancode, extended) -> True
        self.ctrl = False
        self.alt = False
        self._swallow_f_up = False
        self.enabled = False
        self.mnx = 0.5
        self.mny = 0.5
        self.buttons = set()
        self.mouse_dirty = False        # pending absolute flush
        self._rel_dx = 0                # accumulated relative delta
        self._rel_dy = 0
        self._rel_dirty = False         # pending relative flush
        self._key_count = 0
        self._move_count = 0

        self._capture = None
        self._thread = None
        self._stop = False

    # ======================================================================
    # Public API (call from the UI / main thread) -- everything just enqueues.
    # ======================================================================
    def start(self):
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="engine",
                                        daemon=True)
        self._thread.start()

    def stop(self):
        self._cmd.put(("stop", None))

    def join(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)

    def set_targets(self, names):
        self._cmd.put(("targets", list(names)))

    def set_enabled(self, value):
        self._cmd.put(("enabled", bool(value)))

    def toggle(self):
        self._cmd.put(("toggle", None))

    def set_mouse_enabled(self, value):
        self._cmd.put(("mouse_enabled", bool(value)))

    def set_mouse_mode(self, mode):
        self._cmd.put(("mouse_mode", mode))

    def set_monitor(self, monitor):
        self._cmd.put(("monitor", monitor))

    def refresh_vms(self):
        self._cmd.put(("refresh", None))

    def set_launch_type(self, headless):
        self._cmd.put(("launch_type", "headless" if headless else "gui"))

    def launch_vm(self, name):
        self._cmd.put(("launch", name))

    def poweroff_vm(self, name):
        self._cmd.put(("poweroff", name))

    # ======================================================================
    # Worker thread
    # ======================================================================
    def _run(self):
        try:
            import pythoncom  # noqa: WPS433 - COM init for this thread
            pythoncom.CoInitialize()
            _com = True
        except Exception:
            _com = False

        try:
            self.controller = VBoxController()
            self.controller.connect()
            self._emit("log", msg="Connected to VirtualBox %s"
                       % self.controller.vbox.version)
        except Exception as exc:  # noqa: BLE001
            self._emit("error", msg="VirtualBox connection failed: %s" % exc)
            self._emit("stopped")
            return

        self._emit_vms()
        self._apply_targets(self.target_names)

        try:
            from capture import Capture  # lazy: Windows-only import
            self._capture = Capture(self._on_event, monitor=self.monitor)
            self._capture.start()
        except Exception as exc:  # noqa: BLE001
            self._emit("error", msg="Input capture failed to start: %s" % exc)

        self._reconcile(force=True)

        flush_interval = 1.0 / self.flush_hz
        last_flush = last_reconcile = last_stats = time.monotonic()
        self._emit("ready")

        while not self._stop:
            self._drain_commands()
            try:
                ev = self._evq.get(timeout=flush_interval)
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
            if now - last_stats >= 1.0:
                self._emit_stats()
                last_stats = now

        # Shutdown
        self._release_all_keys()
        for t in self.targets.values():
            t.close()
        if self._capture:
            self._capture.stop()
        if _com:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass
        self._emit("stopped")

    # -- status emission ---------------------------------------------------
    def _emit(self, kind, **data):
        data["kind"] = kind
        self.status.put(data)

    def _emit_vms(self):
        try:
            self._emit("vms", vms=self.controller.list_machines())
        except Exception as exc:  # noqa: BLE001
            self._emit("error", msg="Listing VMs failed: %s" % exc)

    def _emit_stats(self):
        tgt = [(t.name, t.alive, t.width, t.height)
               for t in self.targets.values()]
        self._emit("stats", enabled=self.enabled, keys=self._key_count,
                   moves=self._move_count, targets=tgt)

    # -- commands ----------------------------------------------------------
    def _drain_commands(self):
        while True:
            try:
                cmd, arg = self._cmd.get_nowait()
            except queue.Empty:
                return
            if cmd == "stop":
                self._stop = True
            elif cmd == "targets":
                self._apply_targets(arg)
            elif cmd == "enabled":
                self._set_enabled(arg)
            elif cmd == "toggle":
                self._set_enabled(not self.enabled)
            elif cmd == "mouse_enabled":
                self.mouse_enabled = arg
            elif cmd == "mouse_mode":
                if arg in ("absolute", "hybrid", "relative"):
                    self.mouse_mode = arg
            elif cmd == "monitor":
                self.monitor = arg
                self._restart_capture()
            elif cmd == "refresh":
                self._emit_vms()
            elif cmd == "launch_type":
                self.launch_type = arg
            elif cmd == "launch":
                self._spawn_power("launch", arg)
            elif cmd == "poweroff":
                self._spawn_power("poweroff", arg)

    def _spawn_power(self, action, name):
        """Run a VM power op on its own thread (own COM apartment) so the
        mirroring loop never blocks on VM boot/shutdown."""
        self._emit("log", msg="%s %s..." % (action, name))

        def work():
            com = False
            try:
                import pythoncom
                pythoncom.CoInitialize()
                com = True
            except Exception:
                pass
            try:
                if action == "launch":
                    vboxctl.launch_vm(name, self.launch_type)
                else:
                    vboxctl.poweroff_vm(name)
            except Exception as exc:  # noqa: BLE001
                self._emit("error", msg="%s %s failed: %s" % (action, name, exc))
            finally:
                if com:
                    try:
                        import pythoncom
                        pythoncom.CoUninitialize()
                    except Exception:
                        pass
            self._cmd.put(("refresh", None))

        threading.Thread(target=work, name="vmpower", daemon=True).start()

    def _apply_targets(self, names):
        names = list(names)
        for n in list(self.targets):
            if n not in names:
                self.targets[n].close()
                del self.targets[n]
                self._emit("log", msg="Detached %s" % n)
        for n in names:
            if n not in self.targets:
                self.targets[n] = self.controller.make_target(n)
        self.target_names = names
        self._reconcile(force=True)

    def _restart_capture(self):
        try:
            if self._capture:
                self._capture.stop()
            from capture import Capture
            self._capture = Capture(self._on_event, monitor=self.monitor)
            self._capture.start()
            self._emit("log", msg="Mouse source monitor set to %r" % self.monitor)
        except Exception as exc:  # noqa: BLE001
            self._emit("error", msg="Could not switch monitor: %s" % exc)

    # -- input handling (worker thread) ------------------------------------
    def _on_event(self, ev):
        self._evq.put(ev)

    def _alive_targets(self):
        return [t for t in self.targets.values() if t.alive]

    def _handle(self, ev):
        if isinstance(ev, KeyEvent):
            self._handle_key(ev)
        elif isinstance(ev, MouseMove):
            self.mnx, self.mny = ev.nx, ev.ny
            self._move_count += 1
            if self._use_relative():
                self._rel_dx += ev.dx
                self._rel_dy += ev.dy
                self._rel_dirty = True
            else:
                self.mouse_dirty = True
        elif isinstance(ev, MouseButton):
            self._handle_button(ev)
        elif isinstance(ev, MouseWheel):
            self._handle_wheel(ev)

    def _handle_key(self, ev):
        if ev.vk in VK_CTRL:
            self.ctrl = not ev.up
        elif ev.vk in VK_ALT:
            self.alt = not ev.up

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

        self._key_count += 1
        key = (ev.scancode, ev.extended)
        if ev.up:
            for t in self._alive_targets():
                t.send_key(ev)
            self.held.pop(key, None)
        else:
            self.held[key] = True
            for t in self._alive_targets():
                t.send_key(ev)

    def _use_relative(self):
        """True when the current move should be sent as a relative delta."""
        if self.mouse_mode == "relative":
            return True
        if self.mouse_mode == "hybrid" and self.buttons:
            return True
        return False

    def _handle_button(self, ev):
        if not (self.enabled and self.mouse_enabled):
            return
        if ev.down:
            self.buttons.add(ev.button)
        else:
            self.buttons.discard(ev.button)
        # Drop any pending relative delta; the button event defines position.
        self._rel_dx = self._rel_dy = 0
        self._rel_dirty = False
        if self.mouse_mode == "relative":
            # Button state change without moving.
            for t in self._alive_targets():
                t.send_mouse_rel(0, 0, self.buttons)
        else:
            # Absolute anchors the press at the right spot (and, on release in
            # hybrid, re-syncs all VMs to the exact position).
            for t in self._alive_targets():
                t.send_mouse_abs(self.mnx, self.mny, self.buttons)

    def _handle_wheel(self, ev):
        if not (self.enabled and self.mouse_enabled):
            return
        dz = 0 if ev.horizontal else ev.steps
        dw = ev.steps if ev.horizontal else 0
        for t in self._alive_targets():
            if self._use_relative():
                t.send_mouse_rel(0, 0, self.buttons, dz=dz, dw=dw)
            else:
                t.send_mouse_abs(self.mnx, self.mny, self.buttons, dz=dz, dw=dw)

    def _flush_mouse(self):
        if not (self.enabled and self.mouse_enabled):
            self.mouse_dirty = self._rel_dirty = False
            self._rel_dx = self._rel_dy = 0
            return
        if self._rel_dirty and (self._rel_dx or self._rel_dy):
            dx, dy = int(self._rel_dx), int(self._rel_dy)
            for t in self._alive_targets():
                t.send_mouse_rel(dx, dy, self.buttons)
            self._rel_dx -= dx
            self._rel_dy -= dy
        self._rel_dirty = False
        if self.mouse_dirty:
            for t in self._alive_targets():
                t.send_mouse_abs(self.mnx, self.mny, self.buttons)
            self.mouse_dirty = False

    # -- policy ------------------------------------------------------------
    def _set_enabled(self, value):
        self.enabled = value
        self._emit("log", msg="Mirroring %s" % ("ENABLED" if value else "disabled"))
        self._emit("enabled", value=value)
        if not value:
            self._release_all_keys()
            self.buttons.clear()

    def _release_all_keys(self):
        for (scancode, extended) in list(self.held):
            for t in self._alive_targets():
                t.send_break(scancode, extended)
        self.held.clear()

    def _reconcile(self, force=False):
        changed = False
        for name, target in self.targets.items():
            running = self.controller.is_running(name)
            if running and not target.alive:
                if target.attach():
                    self._emit("log", msg="Attached to %s (%dx%d)"
                               % (name, target.width, target.height))
                    changed = True
            elif target.alive and not running:
                self._emit("log", msg="%s stopped; detaching" % name)
                target.close()
                changed = True
            elif target.alive:
                target.refresh_resolution()
        if changed:
            self._emit_stats()
