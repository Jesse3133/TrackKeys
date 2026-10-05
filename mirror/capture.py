"""
Windows low-level input capture (keyboard + mouse) for TrackKeys Mirror.

Installs WH_KEYBOARD_LL and WH_MOUSE_LL on a dedicated thread with its own
message loop and emits normalized events (see events.py). It never swallows
input, so local typing/mouse behave normally; a copy of each event is handed to
the orchestrator.

WINDOWS-ONLY. This module cannot run or be verified off Windows; it is written
against the Win32 hook API and needs on-Windows testing (see DESIGN.md §14).
"""

import ctypes
import threading
from ctypes import CFUNCTYPE, POINTER, byref, c_int, c_size_t, c_void_p, c_ssize_t
from ctypes import wintypes

from events import KeyEvent, MouseMove, MouseButton, MouseWheel

# --- Win32 constants --------------------------------------------------------
WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14

WM_QUIT = 0x0012
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_MOUSEHWHEEL = 0x020E

LLKHF_EXTENDED = 0x01
LLKHF_UP = 0x80

XBUTTON1 = 0x0001
XBUTTON2 = 0x0002

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

ULONG_PTR = ctypes.c_size_t


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


HOOKPROC = CFUNCTYPE(c_ssize_t, c_int, c_size_t, c_void_p)
MONITORENUMPROC = CFUNCTYPE(wintypes.BOOL, c_void_p, c_void_p,
                            POINTER(wintypes.RECT), c_void_p)

SM_CXSCREEN = 0
SM_CYSCREEN = 1


def enumerate_monitors(user32):
    """Return a list of (left, top, width, height) for each monitor, in the
    order Windows enumerates them. The primary monitor always has origin (0, 0).
    """
    monitors = []

    def _cb(hmon, hdc, lprc, lparam):
        r = lprc.contents
        monitors.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return 1

    cb = MONITORENUMPROC(_cb)
    user32.EnumDisplayMonitors(None, None, cb, None)
    return monitors


def _hiword_signed(dword):
    """High 16 bits of a DWORD as a signed short (for wheel deltas)."""
    return ctypes.c_short((dword >> 16) & 0xFFFF).value


def _hiword(dword):
    return (dword >> 16) & 0xFFFF


class Capture:
    """Starts input hooks on a background thread and emits normalized events.

    on_event: callable invoked with a KeyEvent/MouseMove/MouseButton/MouseWheel.
              It must be fast and non-blocking (push to a queue); it runs inside
              the hook callback.
    """

    def __init__(self, on_event, monitor="primary"):
        self.on_event = on_event
        # Which monitor's area maps onto the VM screen. "primary" or a 1-based
        # index into enumerate_monitors(); keep the mouse on this monitor while
        # mirroring.
        self._monitor = monitor
        self._thread = None
        self._thread_id = None
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kbd_proc = None
        self._mouse_proc = None
        self._kbd_hook = None
        self._mouse_hook = None
        # Source-monitor rect used to normalize the pointer to 0.0-1.0.
        self._src_left = self._src_top = 0
        self._src_w = self._src_h = 1

        u = self._user32
        u.SetWindowsHookExW.argtypes = [c_int, HOOKPROC, c_void_p, wintypes.DWORD]
        u.SetWindowsHookExW.restype = c_void_p
        u.CallNextHookEx.argtypes = [c_void_p, c_int, c_size_t, c_void_p]
        u.CallNextHookEx.restype = c_ssize_t
        u.UnhookWindowsHookEx.argtypes = [c_void_p]
        u.UnhookWindowsHookEx.restype = wintypes.BOOL
        u.GetMessageW.argtypes = [POINTER(wintypes.MSG), c_void_p,
                                  wintypes.UINT, wintypes.UINT]
        u.GetMessageW.restype = wintypes.BOOL
        u.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                         c_size_t, c_void_p]
        u.PostThreadMessageW.restype = wintypes.BOOL
        u.GetSystemMetrics.argtypes = [c_int]
        u.GetSystemMetrics.restype = c_int
        u.EnumDisplayMonitors.argtypes = [c_void_p, c_void_p, MONITORENUMPROC,
                                          c_void_p]
        u.EnumDisplayMonitors.restype = wintypes.BOOL

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        self._thread = threading.Thread(target=self._run, name="capture",
                                        daemon=True)
        self._thread.start()

    def stop(self):
        if self._thread_id is not None:
            self._user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, None)
        if self._thread is not None:
            self._thread.join(timeout=2)

    # -- internal ----------------------------------------------------------
    def _resolve_source_monitor(self):
        """Pick the monitor whose area maps onto the VM screen."""
        mons = enumerate_monitors(self._user32)

        sel = self._monitor
        if isinstance(sel, str) and sel.isdigit():
            sel = int(sel)

        chosen = None
        if isinstance(sel, int):
            idx = sel - 1  # config is 1-based
            if 0 <= idx < len(mons):
                chosen = mons[idx]
        if chosen is None:  # "primary" (or bad index) -> monitor at origin
            for m in mons:
                if m[0] == 0 and m[1] == 0:
                    chosen = m
                    break
        if chosen is None and mons:
            chosen = mons[0]
        if chosen is None:  # last resort: primary metrics
            g = self._user32.GetSystemMetrics
            chosen = (0, 0, max(1, g(SM_CXSCREEN)), max(1, g(SM_CYSCREEN)))

        self._src_left, self._src_top, w, h = chosen
        self._src_w = max(1, w)
        self._src_h = max(1, h)
        print("[capture] %d monitor(s) found; mirroring mouse from "
              "origin=(%d,%d) size=%dx%d (keep the mouse on this monitor)"
              % (len(mons), self._src_left, self._src_top,
                 self._src_w, self._src_h))

    def _run(self):
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        self._resolve_source_monitor()

        # Keep references so the trampolines aren't garbage-collected.
        self._kbd_proc = HOOKPROC(self._on_keyboard)
        self._mouse_proc = HOOKPROC(self._on_mouse)

        self._kbd_hook = self._user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._kbd_proc, None, 0)
        self._mouse_hook = self._user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._mouse_proc, None, 0)
        if not self._kbd_hook or not self._mouse_hook:
            raise ctypes.WinError(ctypes.get_last_error())

        msg = wintypes.MSG()
        while self._user32.GetMessageW(byref(msg), None, 0, 0) > 0:
            pass  # LL hooks fire in this thread; we just pump the loop.

        if self._kbd_hook:
            self._user32.UnhookWindowsHookEx(self._kbd_hook)
        if self._mouse_hook:
            self._user32.UnhookWindowsHookEx(self._mouse_hook)

    def _on_keyboard(self, nCode, wParam, lParam):
        if nCode >= 0:
            kb = ctypes.cast(lParam, POINTER(KBDLLHOOKSTRUCT)).contents
            up = bool(kb.flags & LLKHF_UP)
            extended = bool(kb.flags & LLKHF_EXTENDED)
            try:
                self.on_event(KeyEvent(scancode=kb.scanCode & 0xFF,
                                       extended=extended, up=up,
                                       vk=kb.vkCode))
            except Exception:
                pass  # never let a consumer error break the hook chain
        return self._user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _on_mouse(self, nCode, wParam, lParam):
        if nCode >= 0:
            ms = ctypes.cast(lParam, POINTER(MSLLHOOKSTRUCT)).contents
            msg = wParam
            try:
                self._dispatch_mouse(msg, ms)
            except Exception:
                pass
        return self._user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _dispatch_mouse(self, msg, ms):
        if msg == WM_MOUSEMOVE:
            nx = (ms.pt.x - self._src_left) / self._src_w
            ny = (ms.pt.y - self._src_top) / self._src_h
            nx = min(1.0, max(0.0, nx))
            ny = min(1.0, max(0.0, ny))
            self.on_event(MouseMove(nx, ny))
        elif msg == WM_LBUTTONDOWN:
            self.on_event(MouseButton("left", True))
        elif msg == WM_LBUTTONUP:
            self.on_event(MouseButton("left", False))
        elif msg == WM_RBUTTONDOWN:
            self.on_event(MouseButton("right", True))
        elif msg == WM_RBUTTONUP:
            self.on_event(MouseButton("right", False))
        elif msg == WM_MBUTTONDOWN:
            self.on_event(MouseButton("middle", True))
        elif msg == WM_MBUTTONUP:
            self.on_event(MouseButton("middle", False))
        elif msg in (WM_XBUTTONDOWN, WM_XBUTTONUP):
            which = "x1" if _hiword(ms.mouseData) == XBUTTON1 else "x2"
            self.on_event(MouseButton(which, msg == WM_XBUTTONDOWN))
        elif msg == WM_MOUSEWHEEL:
            steps = _hiword_signed(ms.mouseData) // 120
            if steps:
                self.on_event(MouseWheel(steps, horizontal=False))
        elif msg == WM_MOUSEHWHEEL:
            steps = _hiword_signed(ms.mouseData) // 120
            if steps:
                self.on_event(MouseWheel(steps, horizontal=True))
