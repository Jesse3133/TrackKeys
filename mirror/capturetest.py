"""
Capture diagnostic for TrackKeys Mirror.

Isolates the host-side input capture from VirtualBox entirely: it installs the
keyboard+mouse hooks and prints the normalized events they produce, WITHOUT
injecting anything into any VM. Use it to confirm the hooks actually fire for
mouse movement (and keys) on your host.

Run on the host for ~15 seconds, moving the mouse and pressing a few keys:

    python capturetest.py

Then send me the output. If you see "mouse moves: 0" the mouse hook isn't
producing events; if the count climbs as you move the mouse, capture is fine
and the problem is on the injection side.
"""

import time

from capture import Capture
from events import KeyEvent, MouseMove, MouseButton, MouseWheel

counts = {"key": 0, "move": 0, "button": 0, "wheel": 0}
_last_move_print = [0.0]


def on_event(ev):
    if isinstance(ev, KeyEvent):
        counts["key"] += 1
        print("[cap] key scancode=0x%02X extended=%s up=%s vk=0x%02X"
              % (ev.scancode, ev.extended, ev.up, ev.vk))
    elif isinstance(ev, MouseMove):
        counts["move"] += 1
        now = time.monotonic()
        if now - _last_move_print[0] > 0.5:  # throttle move prints
            print("[cap] mouse move -> nx=%.3f ny=%.3f (total moves=%d)"
                  % (ev.nx, ev.ny, counts["move"]))
            _last_move_print[0] = now
    elif isinstance(ev, MouseButton):
        counts["button"] += 1
        print("[cap] button %s %s" % (ev.button, "down" if ev.down else "up"))
    elif isinstance(ev, MouseWheel):
        counts["wheel"] += 1
        print("[cap] wheel steps=%d horizontal=%s" % (ev.steps, ev.horizontal))


def main():
    cap = Capture(on_event, monitor="primary")
    cap.start()
    print("[cap] capturing for 15s -- move the mouse and press some keys ...")
    try:
        time.sleep(15)
    except KeyboardInterrupt:
        pass
    cap.stop()
    print("\n[cap] totals: keys=%(key)d moves=%(move)d buttons=%(button)d "
          "wheel=%(wheel)d" % counts)
    if counts["move"] == 0:
        print("[cap] !! no mouse-move events captured -- the mouse hook is not "
              "firing (or the mouse stayed off the chosen monitor).")


if __name__ == "__main__":
    main()
