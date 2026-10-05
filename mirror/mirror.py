"""
TrackKeys Mirror -- command-line entry point.

A thin terminal front-end over engine.Engine (see engine.py). Mirrors local
Windows 11 keyboard + mouse into the VirtualBox guests listed in config.json.
For a graphical, no-terminal version run gui.pyw instead.

    python mirror.py [config.json]

Toggle mirroring with Ctrl+Alt+F. Ctrl+C quits (releasing held keys first).
WINDOWS + VIRTUALBOX ONLY.
"""

import json
import queue
import sys
import time

from engine import Engine

DEFAULT_CONFIG = {
    "vms": [],
    "toggle_hotkey": "ctrl+alt+f",
    "mouse": {"enabled": True, "flush_hz": 200, "monitor": "primary",
              "mode": "hybrid"},
    "start_enabled": False,
    "reconcile_seconds": 3,
}


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

    mouse = cfg.get("mouse", {})
    engine = Engine()
    engine.target_names = cfg["vms"]
    engine.monitor = mouse.get("monitor", "primary")
    engine.mouse_enabled = bool(mouse.get("enabled", True))
    engine.mouse_mode = mouse.get("mode", "hybrid")
    engine.flush_hz = max(30, int(mouse.get("flush_hz", 200)))
    engine.reconcile_seconds = float(cfg.get("reconcile_seconds", 3))
    if cfg.get("start_enabled"):
        engine.enabled = True

    engine.start()
    print("TrackKeys Mirror (CLI). Ctrl+Alt+F toggles mirroring; Ctrl+C quits.")

    try:
        while engine._thread and engine._thread.is_alive():
            try:
                s = engine.status.get(timeout=0.5)
            except queue.Empty:
                continue
            kind = s.get("kind")
            if kind in ("log", "error"):
                print("[%s] %s" % (kind, s["msg"]))
            elif kind == "vms":
                names = ", ".join("%s%s" % (n, "" if r else " (stopped)")
                                  for n, r in s["vms"])
                print("[vms] %s" % (names or "none registered"))
            elif kind == "stopped":
                break
    except KeyboardInterrupt:
        print("\nStopping...")
        engine.stop()
        engine.join(timeout=5)


if __name__ == "__main__":
    main()
