# TrackKeys (desktop)

A transparent, **system-wide** keyboard-input tracker for your own computer.
Unlike the browser version (`../index.html`), which can only see keys typed
into its own window, this native app uses an OS-level keyboard hook, so it
records keys you press in **any** application.

It shows:

- a **live** view of the text you type,
- session **stats** (total keys, characters, keys/min, elapsed time),
- a **most-used keys** breakdown,
- a timestamped **key log** with modifier keys and key codes,
- **Pause**, **Clear**, and **Export CSV**.

Everything stays on your machine. The app never opens a network connection.

## Install

Requires Python 3.8+.

```bash
cd desktop
python -m pip install -r requirements.txt
python trackkeys.py
```

`tkinter` ships with most Python installations. If it's missing:
- **Debian/Ubuntu:** `sudo apt install python3-tk`
- **Fedora:** `sudo dnf install python3-tkinter`
- **macOS/Windows:** use the python.org installer, which includes it.

## OS permissions

A global keyboard hook needs permission on some systems:

- **macOS:** grant the app (your terminal or Python) **Accessibility** access
  under *System Settings → Privacy & Security → Accessibility*. You may also
  need **Input Monitoring**.
- **Linux:** works under X11. Under Wayland, global capture is restricted by
  the compositor and may not work; run an X11 session if needed.
- **Windows:** works without extra permissions.

## Acceptable use

This tool is for monitoring **your own** keyboard input on a machine **you own
and control**. It runs overtly — a visible window is shown the whole time, and
it does not hide itself, disguise what it is, or start silently in the
background.

Recording another person's keystrokes without their knowledge and consent is
illegal in many places and is not what this tool is for. Don't deploy it on
devices that aren't yours or to capture other people. Keep it transparent.
