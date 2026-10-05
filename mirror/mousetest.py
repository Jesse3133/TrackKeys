"""
Mouse injection diagnostic for TrackKeys Mirror.

Isolates the VirtualBox mouse-injection path from input capture: it connects to
ONE running VM and drives the guest cursor to known positions, printing what it
reads and sends. Use this to find out why mouse mirroring isn't working.

Run on the host (VM running, Guest Additions installed, integration on):

    python mousetest.py "Win11-A"          # by VM name
    python mousetest.py                     # uses first VM in config.json

Watch the guest's cursor while this runs, and send me the full console output.
"""

import json
import sys
import time

from vboxctl import VBoxController, button_mask


def pick_vm():
    if len(sys.argv) > 1:
        return sys.argv[1]
    try:
        with open("config.json", "r", encoding="utf-8") as fh:
            vms = json.load(fh).get("vms", [])
        if vms:
            return vms[0]
    except FileNotFoundError:
        pass
    sys.exit("Usage: python mousetest.py \"VM Name\"  (or create config.json)")


def main():
    name = pick_vm()
    c = VBoxController()
    c.connect()
    target = c.make_target(name)
    print("[test] attaching to %r ..." % name)
    if not target.attach():
        sys.exit("[test] could not attach (is the VM running?)")

    m = target.mouse
    print("[test] guest resolution read as: %d x %d" % (target.width, target.height))
    # These properties tell us whether absolute positioning is even available.
    for prop in ("absoluteSupported", "relativeSupported", "needsHostCursor"):
        try:
            print("[test] mouse.%s = %s" % (prop, getattr(m, prop)))
        except Exception as exc:  # noqa: BLE001
            print("[test] mouse.%s unavailable (%s)" % (prop, exc))

    if target.width <= 1 or target.height <= 1:
        print("[test] !! resolution looks wrong; cursor would be pinned to a "
              "corner. getScreenResolution(0) likely failed.")

    # 1) Absolute positioning to known fractions of the screen.
    spots = [("center", 0.5, 0.5), ("top-left", 0.0, 0.0),
             ("top-right", 1.0, 0.0), ("bottom-right", 1.0, 1.0),
             ("bottom-left", 0.0, 1.0), ("center", 0.5, 0.5)]
    print("\n[test] --- absolute positioning (watch the guest cursor) ---")
    for label, nx, ny in spots:
        x = 1 + int(round(nx * (target.width - 1)))
        y = 1 + int(round(ny * (target.height - 1)))
        try:
            m.putMouseEventAbsolute(x, y, 0, 0, 0)
            print("[test] abs %-12s -> (%d, %d)" % (label, x, y))
        except Exception as exc:  # noqa: BLE001
            print("[test] abs %-12s FAILED at (%d, %d): %s" % (label, x, y, exc))
        time.sleep(1.2)

    # 2) Relative fallback (works even without absolute support).
    print("\n[test] --- relative movement fallback ---")
    try:
        for _ in range(10):
            m.putMouseEvent(12, 0, 0, 0, 0)
            time.sleep(0.05)
        for _ in range(10):
            m.putMouseEvent(0, 12, 0, 0, 0)
            time.sleep(0.05)
        print("[test] relative moves sent (cursor should have drifted right then down)")
    except Exception as exc:  # noqa: BLE001
        print("[test] relative movement FAILED: %s" % exc)

    target.close()
    print("\n[test] done. Report: did the cursor jump to the corners/center,"
          " drift with the relative moves, or not move at all?")


if __name__ == "__main__":
    main()
