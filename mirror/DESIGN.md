# TrackKeys Mirror — Design Document

**Status:** Draft / initial implementation
**Last updated:** 2026-10-05
**Target platform:** Windows 11 host, Oracle VirtualBox, Windows 11 guests

---

## 1. Purpose

TrackKeys Mirror lets you type and move the mouse on your **local Windows 11
desktop** and have that same input replayed, **live**, into several
**VirtualBox Windows 11 guests at once**. You keep working on the host
normally; a copy of every keystroke and mouse action is delivered to each
selected VM.

The motivating use case: type `Hello World` into Notepad on the host and have
the same `Hello World` appear in Notepad on every mirrored VM, simultaneously —
useful for configuring, testing, or operating a fleet of identical machines in
lockstep.

### Scale target

- **5 endpoints total:** 1 local host + **4 guest VMs**.
- All 4 VMs receive the same keyboard and mouse stream at the same time.

---

## 2. Goals and non-goals

### Goals

- Live, low-latency mirroring of **keyboard** input to N VirtualBox VMs.
- Live mirroring of **mouse** input (position, buttons, wheel) to the same VMs.
- Faithful key behavior: forward **hardware scancodes**, so modifiers,
  in-guest shortcuts (Ctrl+C, Alt+Tab), function/arrow/keypad keys, dead keys
  and IME all work exactly as on the host.
- **No custom software inside the guests.** Injection is done entirely from the
  host through VirtualBox's own host-side APIs. (VirtualBox **Guest Additions**
  — Oracle's standard driver package — is installed in each guest to enable
  absolute-mouse positioning; it is not custom code and does not receive or
  relay our input.)
- Mirroring, not takeover: the host keeps using its own keyboard/mouse
  normally.
- A global toggle to start/stop mirroring at any time.

### Non-goals (for now)

- Cross-platform host or guests (Windows 11 only).
- Differing keyboard layouts between host and guests (we assume **identical**
  layouts, which makes scancode forwarding exact).
- Capturing the Windows **secure desktop** (UAC prompts, lock screen, Ctrl+Alt+Del
  entry) — the OS forbids this for any user-space tool.
- Clipboard, file, or audio redirection (VirtualBox already offers these).
- Recording/replay-to-file (that is TrackKeys the logger's job).

---

## 3. Key design decisions (and why)

| Decision | Choice | Rationale |
|---|---|---|
| What we forward | **Scancodes** (keyboard), **absolute position** (mouse) | Physical-key fidelity; identical layouts make it exact; absolute mouse keeps all cursors in lockstep |
| Where injection happens | **Host-side VirtualBox API** | Satisfies "no guest agent"; APIs natively speak scancodes |
| Guest Additions | **Enabled** in all guests | Required for `PutMouseEventAbsolute`; without it, mouse drifts across VMs |
| Transport | **In-process COM** to local VirtualBox | No network; lowest latency; no feedback loop |
| Local input | **Not suppressed** | We want a true mirror; host keeps working |
| Capture mechanism | **Windows low-level hooks** (`WH_KEYBOARD_LL`, `WH_MOUSE_LL`) | Expose scancode + extended flag (keyboard) and absolute screen coords + buttons + wheel (mouse) |
| Language | **Python** + `vboxapi` | Matches the rest of this repo; `vboxapi` ships with the VirtualBox SDK; no build toolchain needed |

> **Alternative considered:** C#/.NET with Raw Input (`WM_INPUT`) and COM
> interop. More idiomatic on Windows and gives fuller extended-key fidelity
> (true E0/E1 prefixes), but adds a build toolchain. Python with low-level
> hooks is the lower-friction starting point; the capture backend is isolated
> (see §6.1) so it can be swapped for a Raw Input backend later without
> touching the rest of the system.

---

## 4. Terminology

- **Host** — the local Windows 11 desktop running TrackKeys Mirror.
- **Guest / VM** — a VirtualBox Windows 11 virtual machine receiving input.
- **Target** — a single VM that Mirror is actively injecting into (a live COM
  session + its keyboard/mouse interfaces).
- **Scancode** — a hardware key code (PS/2 **set 1** on this stack). "Make" =
  key down, "break" = key up (`make | 0x80`). Extended keys are prefixed with
  `0xE0`.
- **Fan-out** — delivering one captured event to all targets.

---

## 5. Architecture overview

```
                     LOCAL HOST (Windows 11)
  ┌──────────────────────────────────────────────────────────┐
  │  capture.py  (low-level hooks, own thread + message loop)  │
  │     • keyboard: scancode, extended flag, up/down           │
  │     • mouse: absolute screen pos, buttons, wheel           │
  │                         │ normalized events                │
  │                         ▼                                  │
  │  mirror.py  (orchestrator)                                 │
  │     • toggle hotkey (Ctrl+Alt+F)                           │
  │     • held-key tracking (for safe release)                 │
  │     • mouse coalescing (~125–250 Hz)                       │
  │                         │ fan-out                          │
  │        ┌────────────┬───┴────────┬────────────┐            │
  │     vboxctl       vboxctl     vboxctl      vboxctl         │
  │     Target VM1    Target VM2  Target VM3   Target VM4      │
  └────────┼────────────┼────────────┼────────────┼───────────┘
           ▼            ▼            ▼            ▼
         IConsole     IConsole     IConsole     IConsole      (via COM)
      Keyboard.PutScancodes / Mouse.PutMouseEventAbsolute
           │            │            │            │
         GUEST 1      GUEST 2      GUEST 3      GUEST 4
     (Win 11 + Guest Additions; windowed-unfocused or headless)
```

Three layers, cleanly separated:

1. **Capture** (`capture.py`) — turns OS input into normalized events.
2. **Orchestration** (`mirror.py`) — toggle, held-key bookkeeping, mouse
   coalescing, fan-out, lifecycle.
3. **Injection** (`vboxctl.py`) — one `Target` per VM, wrapping a VirtualBox
   COM session and translating normalized events into API calls.

---

## 6. Component design

### 6.1 Capture layer (`capture.py`)

Installs two Windows low-level hooks on a dedicated thread that runs its own
message loop (hooks require a message pump).

**Keyboard** (`WH_KEYBOARD_LL` → `KBDLLHOOKSTRUCT`):
- `scanCode` — the set-1 scancode.
- `flags`: `LLKHF_EXTENDED (0x01)` → emit an `0xE0` prefix; `LLKHF_UP (0x80)` →
  key release.
- `vkCode` — used only to recognize modifiers for the toggle hotkey.

Emitted event: `KeyEvent(scancode, extended: bool, up: bool, vk)`.

**Mouse** (`WH_MOUSE_LL` → `MSLLHOOKSTRUCT`):
- `pt.x/pt.y` — absolute **screen** coordinates (virtual desktop; may be
  negative on multi-monitor).
- message type → move / button down / button up / wheel.
- `mouseData` — wheel delta (signed, multiples of 120) or X-button id.

The capture layer normalizes mouse position to `0.0–1.0` across the **virtual
screen** (using `SM_XVIRTUALSCREEN/SM_YVIRTUALSCREEN/SM_CXVIRTUALSCREEN/
SM_CYVIRTUALSCREEN`) so each target can scale to its own guest resolution.

Emitted events:
- `MouseMove(nx, ny)` — normalized position.
- `MouseButton(button, down)` — left/right/middle/x1/x2.
- `MouseWheel(delta_steps, horizontal: bool)`.

The hook callbacks **do not block** and **do not swallow** input (so local
typing is unaffected) — they push events to a queue consumed by the
orchestrator. The one exception: the Ctrl+Alt+F toggle chord is recognized and
not forwarded (see §6.2).

> **Fidelity note:** low-level hooks expose a single "extended" bit, not the
> full E0/E1 byte stream. This covers the common extended keys (arrows, nav
> cluster, right Ctrl/Alt, numpad Enter/Divide). The `E1` Pause/Break sequence
> and a few numpad corner cases are approximated; a future Raw Input backend
> would make these exact.

### 6.2 Orchestrator (`mirror.py`)

Owns the run loop and policy:

- **Toggle hotkey:** tracks Ctrl/Alt state from key events; on `Ctrl+Alt+F`
  flips mirroring on/off and suppresses that `F` from being forwarded. Starts
  **OFF** so input is never streamed by surprise.
- **Held-key tracking:** records every scancode currently "made" that has been
  forwarded. On stop, target loss, or shutdown, sends the matching "break"
  codes to every target so no modifier/key gets stuck in a guest.
- **Mouse coalescing:** mouse-move events are collapsed and flushed on a timer
  (~4–8 ms, i.e. 125–250 Hz) to avoid a COM call per raw report × N VMs.
  Buttons and wheel are sent immediately.
- **Fan-out:** each forwarded event is applied to all live targets; a failing
  target is marked dead and scheduled for re-attach without blocking the rest.
- **Lifecycle:** periodically reconciles the configured VM list against
  VirtualBox (which VMs are running), attaching new/recovered targets and
  dropping stopped ones.

### 6.3 Injection layer (`vboxctl.py`)

`VBoxController` connects once to the local VirtualBox (`vboxapi`,
`VirtualBoxManager`). For each configured VM it creates a `Target`:

- Find the machine: `vbox.findMachine(name)`.
- Require `machine.state == MachineState_Running`.
- Acquire a **shared** lock: `machine.lockMachine(session, LockType_Shared)` —
  multiple shared locks are allowed while the VM runs under the GUI, so our
  process can attach alongside the running window.
- Grab interfaces: `console = session.console`, `console.keyboard`,
  `console.mouse`, `console.display`.
- Query guest resolution via `display.getScreenResolution(0)` for absolute
  mouse scaling (refreshed if it changes).

**Keyboard injection** — `Target.send_scancodes(byte_list)`:
- Build the byte stream from a normalized key event:
  - prefix `0xE0` if extended,
  - `code = scancode & 0xFF`,
  - set `0x80` for a break (key up).
- Call `keyboard.putScancodes([...])`.

**Mouse injection:**
- Position — `Target.send_mouse_abs(nx, ny, buttons)`: scale normalized
  `nx/ny` to `(1..width, 1..height)` of this guest and call
  `mouse.putMouseEventAbsolute(x, y, dz=0, dw=0, buttonState)`.
- Buttons — maintained as a bitmask (`0x01` left, `0x02` right, `0x04` middle)
  and sent with each absolute event.
- Wheel — `mouse.putMouseEventAbsolute(x, y, dz, dw, buttons)` with `dz`
  (vertical) / `dw` (horizontal) wheel steps.

**Cleanup** — `Target.close()` releases held keys (orchestrator-driven) and
calls `session.unlockMachine()`.

---

## 7. Event model

Normalized events produced by capture and consumed by the orchestrator:

```
KeyEvent      { scancode:int, extended:bool, up:bool, vk:int }
MouseMove     { nx:float, ny:float }          # 0.0–1.0 on the virtual screen
MouseButton   { button:str, down:bool }       # "left"|"right"|"middle"|"x1"|"x2"
MouseWheel    { steps:int, horizontal:bool }
```

Normalized events keep the injection layer independent of capture specifics and
let each target apply its own guest resolution.

---

## 8. Concurrency model

- **Capture thread:** runs the Windows message loop and both hooks; only pushes
  events to a thread-safe queue. Never calls COM.
- **Orchestrator thread:** drains the queue, applies policy, calls targets.
- **Lifecycle timer:** periodic VM reconciliation (can run on the orchestrator
  thread between drains, or a dedicated timer).
- **COM threading:** VirtualBox COM calls are made from a single thread
  (the orchestrator) to avoid apartment/marshaling issues. If reconciliation
  needs COM, it runs on that same thread.

Mouse-move coalescing lives in the orchestrator: keep only the latest position
and flush on the timer.

---

## 9. Operational model (how you run it)

1. Install **Guest Additions** in each of the 4 VMs; enable the absolute
   pointing device (default when Additions are present).
   - Also enable a **software-drawn cursor** in each guest (Mouse → Pointer
     Options → "Display pointer trails", shortest). With mouse integration the
     host only paints the pointer where the real host cursor hovers a VM
     window, so an API-driven / headless / unfocused VM shows no cursor icon
     even though positioning works. Forcing the guest to draw its own cursor
     makes it visible — required for the multi-VM fan-out, where one host
     pointer can't hover every VM window.
2. Start the 4 VMs (windowed or headless).
3. Run `mirror.py` on the host with a config listing the 4 VM names.
4. **Keep host focus on your local app — not on a VM window.** Injection goes
   through the hypervisor channel; if a VM window has focus it would also get
   your keys directly (double input). Running VMs **headless** removes the
   temptation entirely.
5. Press **Ctrl+Alt+F** to start mirroring; type/move; press again to stop.

---

## 10. Security & safety

- **Local-only:** no network listener, no ports. All injection is in-process
  COM to the local VirtualBox.
- **Own machines only:** this tool mirrors your own input to your own VMs. It
  is overt (visible console, explicit toggle) and installs nothing in the
  guests beyond Oracle's standard Guest Additions.
- **No feedback loop:** injected input never re-enters the host input queue.
- **Stuck-key protection:** held keys are released on every stop/detach.
- **Secure desktop:** UAC/lock-screen input cannot and will not be captured —
  by OS design.

---

## 11. Failure handling

| Failure | Behavior |
|---|---|
| A VM is not running | Target not attached; reconciler retries when it starts |
| Shared lock fails | Log, mark target dead, retry on next reconcile |
| COM call throws mid-session | Mark target dead, release its held keys, re-attach later |
| VM resolution changes | Re-query on next absolute event / periodically |
| Mirror stopped or app exits | Flush "break" for all held keys to all targets, unlock sessions |
| One target slow/dead | Never blocks others; fan-out is per-target best-effort |

---

## 12. Configuration

`config.json` (see `config.example.json`):

```json
{
  "vms": ["Win11-A", "Win11-B", "Win11-C", "Win11-D"],
  "toggle_hotkey": "ctrl+alt+f",
  "mouse": { "enabled": true, "flush_hz": 200 },
  "start_enabled": false,
  "reconcile_seconds": 3
}
```

- `vms` — VirtualBox machine names (as shown in the VirtualBox Manager).
- `mouse.flush_hz` — mouse-move coalescing rate.
- `start_enabled` — whether mirroring is on at launch (default off).

---

## 13. Build & run

Requirements:
- Windows 11 host, Oracle VirtualBox + the **VirtualBox SDK** (provides
  `vboxapi`).
- Python 3.9+ (64-bit, matching VirtualBox).
- Guest Additions installed in each guest.

See `README.md` for exact setup steps (SDK install, `vboxapi` registration,
running the app).

---

## 14. Testing plan

Because capture and COM injection are Windows/VirtualBox-specific, testing is
staged:

1. **Injection unit test** — with one VM running, send a fixed scancode
   sequence (e.g. type `hello`) via `vboxctl` directly; confirm it appears in
   the guest. Validates COM wiring and scancode byte-building.
2. **Mouse test** — move the absolute pointer to known fractions (corners,
   center) on one VM; confirm cursor placement.
3. **Capture test** — log normalized events from the hooks without injection;
   confirm scancodes/extended flags and normalized mouse coords look right.
4. **Single-VM mirror** — end-to-end to one VM.
5. **Fan-out** — scale to 4 VMs; watch for divergence, latency, stuck keys.
6. **Soak** — long session; verify no stuck modifiers, no session leaks, clean
   recovery when a VM reboots.

---

## 15. Roadmap / milestones

Core engine (the original CLI milestones) is done and verified on one VM:
injection, capture, single-VM mirror, multi-target fan-out, lifecycle, and the
absolute-mouse + multi-monitor + cursor-visibility fixes.

Desktop-app track:

- **GUI-M1 — Engine split:** orchestrator extracted into `engine.py` (threaded,
  command/status queues, COM confined to the worker thread); CLI `mirror.py`
  re-based on it. *(done)*
- **GUI-M2 — Basic window:** `gui.pyw` (no console) with live VM discovery +
  checkboxes, monitor dropdown, mouse toggle, start/stop, status, log. *(done)*
- **GUI-M3 — Polish:** settings persistence, live counters, start/stop VMs from
  the UI.
- **GUI-M4 — Tray + icon:** system-tray presence, app icon, clean shutdown.
- **GUI-M5 — Packaging:** single `.exe` via PyInstaller (verify `vboxapi`
  bundling); VirtualBox itself still required on the target.
- **Optional — Raw Input backend:** exact E0/E1 key fidelity; mouse via
  `WM_INPUT`.

---

## 16. Known risks / open questions

- **Low-level hook fidelity** for rare extended/`E1` keys — mitigated by the
  optional Raw Input backend (M6).
- **Wheel sign conventions** (`dz`/`dw` direction) need on-hardware
  confirmation against VirtualBox.
- **Absolute coordinate range** for `PutMouseEventAbsolute` (guest pixels vs.
  normalized) to confirm per installed VirtualBox version.
- **Shared-lock availability** depends on the VM running under the GUI/VBoxSVC;
  headless VMs started by our own session may need a different lock flow —
  verify on the target setup.
- **COM apartment threading** under `vboxapi` on Windows — keep all COM on one
  thread.
