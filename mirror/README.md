# TrackKeys Mirror

Mirror your **local Windows 11** keyboard and mouse, live, into **multiple
VirtualBox Windows 11 VMs at once** (target: 4 VMs). You keep working on the
host normally; a copy of every keystroke (as hardware **scancodes**) and mouse
action (as **absolute** position) is injected into each VM from the host — no
custom software runs inside the guests.

See [`DESIGN.md`](DESIGN.md) for the full architecture and rationale.

> **Status: early implementation, not yet verified on Windows.** The code is
> written against the Win32 hook API and the VirtualBox SDK but was authored in
> a Linux CI container with no VirtualBox, so it has **not been run**. Expect to
> debug the spots marked "VERSION NOTE" / "WINDOWS-ONLY". Test with the staged
> plan in `DESIGN.md` §14, starting with a single VM.

## How it works (one paragraph)

`capture.py` installs low-level keyboard/mouse hooks on the host and emits
normalized events. `mirror.py` fans each event out to every target VM.
`vboxctl.py` injects into each VM through VirtualBox's host-side COM API
(`IKeyboard::PutScancodes`, `IMouse::PutMouseEventAbsolute`). Everything is
in-process and local — no network, no ports, no guest agent.

## Requirements

- Windows 11 **host**.
- Oracle VirtualBox, with the **VirtualBox SDK** installed (provides `vboxapi`).
- Python **3.9+ 64-bit** (must match your VirtualBox's bitness).
- Each **guest**: Windows 11 with **Guest Additions installed** (required for
  absolute-mouse positioning) and the same keyboard layout as the host.

## Setup

### 1. Install the VirtualBox SDK (provides `vboxapi`)

1. Download the **VirtualBox SDK** matching your VirtualBox version from the
   Oracle downloads page and unzip it.
2. From the SDK's `installer/python` folder, install the bindings into your
   Python:
   ```
   cd sdk\installer\python
   python vboxapisetup.py install
   ```
   (Use the same Python interpreter you'll run Mirror with.)
3. Verify:
   ```
   python -c "import vboxapi; print('vboxapi OK')"
   ```

### 2. Prepare the guests

- Install **Guest Additions** in each VM (VirtualBox menu: *Devices → Insert
  Guest Additions CD image*, then run the installer in the guest).
- Make sure the guest keyboard layout matches the host exactly.

### 3. Configure

```
copy config.example.json config.json
```
Edit `config.json` and list your VM names **exactly as they appear in the
VirtualBox Manager**:
```json
{ "vms": ["Win11-A", "Win11-B", "Win11-C", "Win11-D"] }
```

**Multi-monitor hosts:** the mouse is mirrored from **one** monitor (the
"control monitor"), whose full area maps onto the VM screen. Keep the mouse on
that monitor while mirroring. Choose it with `mouse.monitor`:
```json
{ "mouse": { "monitor": "primary" } }     // default: your primary monitor
{ "mouse": { "monitor": 3 } }             // or a 1-based monitor index
```
On startup the app prints how many monitors it found and which one it's using.

## Run

### Desktop app (recommended)

Double-click **`gui.pyw`** (Windows launches it with `pythonw.exe`, so there's
no console window), or:
```
pythonw gui.pyw
```
It connects to VirtualBox, lists your VMs with checkboxes, lets you pick the
mouse source monitor, and starts/stops mirroring with a button (or Ctrl+Alt+F).
No config-file editing needed. You can also **start/power-off each VM** from the
list (optionally headless), and your selections, monitor, and window size are
**remembered between runs** (saved to `gui_settings.json`).

### Command line

1. Start the 4 VMs (windowed or, preferably, headless).
2. On the host:
   ```
   python mirror.py config.json
   ```
3. **Keep host focus on your local app, not on a VM window** (a focused VM would
   receive your keys directly *and* via injection = double input). Headless VMs
   avoid this entirely.
4. Press **Ctrl+Alt+F** to start mirroring. Type / move the mouse. Press
   **Ctrl+Alt+F** again to stop. **Ctrl+C** in the console quits (and releases
   any held keys in the guests first).

## System tray

With `pystray` + `pillow` installed, the app shows a **tray icon**. Closing the
window **minimizes to the tray** (mirroring keeps running); the tray menu has
**Show window**, **Start/Stop mirroring**, and **Quit**. Without those packages
the app still runs — closing the window just quits normally.

## Build a standalone .exe

To produce `dist\TrackKeysMirror.exe` (no Python needed on the target, but
VirtualBox still is):

```
pip install pyinstaller pystray pillow
build.bat
```

`build.bat` generates the icon and runs PyInstaller with the hidden imports
`vboxapi` needs. If the built `.exe` can't import `vboxapi`, confirm
`python -c "import vboxapi"` works in the same environment you built from, and
that VirtualBox is installed on the target machine.

## Acceptable use

For mirroring **your own** input to **your own** VMs on a machine you control.
It is overt (visible console, explicit toggle), local-only (no network), and
installs nothing in the guests beyond Oracle's standard Guest Additions. Don't
use it to drive machines that aren't yours.

## Troubleshooting (expected first-run issues)

- `import vboxapi` fails → the SDK bindings aren't installed into *this* Python
  (redo Setup step 1 with the right interpreter).
- `getSessionObject` / lock errors → `vboxapi` API shape differs by version;
  see the "VERSION NOTE" in `vboxctl.py`.
- Keys land but extended keys (arrows, right Ctrl) misbehave → low-level-hook
  extended-flag limitation; see DESIGN.md §16 and the optional Raw Input backend
  (roadmap M6).
- Mouse wheel scrolls the wrong way → flip the `dz`/`dw` sign in
  `vboxctl.send_mouse_abs` (convention noted in DESIGN.md §16).
- Cursor misaligned in a guest → confirm Guest Additions is active and the
  absolute pointing device is enabled.
- **Mouse works but no cursor icon is drawn in the guest** (clicks/hover land,
  but you can't see the pointer) → the mirror *is* working; VirtualBox just
  isn't painting the pointer because, with mouse integration, the host draws it
  only where the real host pointer hovers the VM window — and when the VM is
  driven via the API (headless / unfocused / host pointer on another monitor),
  nobody paints it. Force the **guest** to draw its own cursor: in each guest,
  **Control Panel → Mouse → Pointer Options → enable "Display pointer trails"**
  (shortest setting), or set a custom/large pointer scheme. This is the
  recommended setup for the multi-VM fan-out, since one host pointer can't hover
  all the VM windows at once.

## Files

| File | Role |
|---|---|
| `DESIGN.md` | Architecture & rationale |
| `gui.pyw` | Desktop GUI (no console) |
| `engine.py` | Orchestrator engine (threaded; used by GUI and CLI) |
| `icon.py` | Programmatic app/tray icon + `.ico` generator |
| `build.bat` | PyInstaller build script (→ single `.exe`) |
| `events.py` | Normalized event types |
| `capture.py` | Win32 low-level keyboard/mouse hooks |
| `vboxctl.py` | VirtualBox COM injection (per-VM targets) |
| `mirror.py` | Command-line entry point |
| `mousetest.py` / `capturetest.py` | Injection / capture diagnostics |
| `config.example.json` | Config template |
