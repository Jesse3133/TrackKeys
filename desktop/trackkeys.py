#!/usr/bin/env python3
"""
TrackKeys (desktop) -- a transparent, system-wide keyboard-input tracker.

This records the keys YOU press on YOUR OWN computer and displays them in a
window: a live typed-text view, session stats, a most-used-keys breakdown,
and a timestamped log. It can export the log to CSV.

Design principles (please keep them):
  * Overt, not covert. A visible window is shown the whole time it runs.
  * Local only. Nothing is ever sent over the network.
  * No stealth. It does not hide itself, auto-start silently, or disguise
    what it is.

Intended use is self-monitoring on a machine you own and control. Capturing
another person's keystrokes without their knowledge and consent is illegal
in many jurisdictions. Don't do that.

Requires: pynput  (pip install pynput)
tkinter ships with most Python installs.
"""

import csv
import queue
import sys
import threading
from collections import Counter
from datetime import datetime

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except Exception as exc:  # pragma: no cover
    sys.exit("tkinter is required but could not be imported: %s" % exc)

try:
    from pynput import keyboard
except Exception:  # pragma: no cover
    sys.exit(
        "The 'pynput' package is required.\n"
        "Install it with:  pip install pynput\n"
        "Then run this script again."
    )


# ---------------------------------------------------------------------------
# Theme (mirrors the TrackKeys web app)
# ---------------------------------------------------------------------------
BG = "#14161c"
PANEL = "#1c1f28"
PANEL_ALT = "#232734"
BORDER = "#333846"
TEXT = "#e8e9ee"
TEXT_DIM = "#9096a8"
ACCENT = "#5b8def"
DANGER = "#e35d6a"
SUCCESS = "#4caf7d"
MONO = ("SF Mono", "Menlo", "Consolas", "monospace")

MAX_LOG = 2000          # rows retained in memory / shown
LIVE_LIMIT = 4000       # chars kept in the live buffer

# Friendly labels for non-character keys.
SPECIAL_LABELS = {
    "space": "Space",
    "enter": "Enter",
    "backspace": "Backspace",
    "tab": "Tab",
    "esc": "Esc",
    "delete": "Del",
    "caps_lock": "Caps",
    "up": "↑",
    "down": "↓",
    "left": "←",
    "right": "→",
}
MODIFIER_NAMES = {
    "ctrl", "ctrl_l", "ctrl_r",
    "alt", "alt_l", "alt_r", "alt_gr",
    "shift", "shift_l", "shift_r",
    "cmd", "cmd_l", "cmd_r",
}


def describe_key(key):
    """Return (display_name, is_char, raw_char_or_None) for a pynput key."""
    if isinstance(key, keyboard.KeyCode):
        if key.char is not None:
            return key.char, True, key.char
        # Ctrl+letter etc. arrive with no .char; fall back to vk.
        return ("<%s>" % key.vk if key.vk is not None else "<?>"), False, None
    # keyboard.Key member, e.g. Key.space
    name = key.name if hasattr(key, "name") else str(key)
    return SPECIAL_LABELS.get(name, name), False, None


def modifier_label(name):
    if name.startswith("ctrl"):
        return "Ctrl"
    if name.startswith("alt"):
        return "Alt"
    if name.startswith("shift"):
        return "Shift"
    if name.startswith("cmd"):
        return "Cmd"
    return name


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
class TrackKeys:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.paused = False
        self.total = 0
        self.chars = 0
        self.log = []                 # list of dicts: time, name, mods, code
        self.counts = Counter()
        self.start_time = datetime.now()
        self.typed = ""
        self.held_mods = set()        # currently held modifier display names
        self.listener = None

        self._build_ui()
        self._start_listener()
        self.root.after(100, self._drain_events)
        self.root.after(1000, self._tick)

    # -- UI ----------------------------------------------------------------
    def _build_ui(self):
        self.root.title("TrackKeys — recording (system-wide)")
        self.root.configure(bg=BG)
        self.root.geometry("760x680")
        self.root.minsize(560, 520)

        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True, padx=16, pady=16)

        tk.Label(outer, text="TrackKeys", bg=BG, fg=TEXT,
                 font=("Helvetica", 18, "bold")).pack(anchor="w")
        tk.Label(outer,
                 text=("Recording keys pressed anywhere on this computer. "
                       "Data stays on this machine — nothing is sent anywhere."),
                 bg=BG, fg=TEXT_DIM, font=("Helvetica", 10),
                 wraplength=720, justify="left").pack(anchor="w", pady=(2, 12))

        # Status row
        status = tk.Frame(outer, bg=PANEL, highlightbackground=BORDER,
                          highlightthickness=1)
        status.pack(fill="x", pady=(0, 10))
        inner = tk.Frame(status, bg=PANEL)
        inner.pack(fill="x", padx=14, pady=10)
        self.dot = tk.Canvas(inner, width=12, height=12, bg=PANEL,
                             highlightthickness=0)
        self.dot.pack(side="left")
        self._dot_id = self.dot.create_oval(2, 2, 11, 11, fill=SUCCESS, outline="")
        self.status_lbl = tk.Label(inner, text="Recording", bg=PANEL, fg=TEXT,
                                   font=("Helvetica", 10, "bold"))
        self.status_lbl.pack(side="left", padx=8)

        # Stats
        stats = tk.Frame(outer, bg=BG)
        stats.pack(fill="x", pady=(0, 10))
        self.stat_vars = {}
        for i, (key, label) in enumerate([
            ("total", "Total keys"), ("chars", "Characters"),
            ("kpm", "Keys / min"), ("time", "Session time"),
        ]):
            cell = tk.Frame(stats, bg=PANEL_ALT, highlightbackground=BORDER,
                            highlightthickness=1)
            cell.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 6, 0))
            stats.grid_columnconfigure(i, weight=1)
            tk.Label(cell, text=label.upper(), bg=PANEL_ALT, fg=TEXT_DIM,
                     font=("Helvetica", 8, "bold")).pack(anchor="w", padx=12, pady=(10, 2))
            var = tk.StringVar(value="0")
            self.stat_vars[key] = var
            tk.Label(cell, textvariable=var, bg=PANEL_ALT, fg=TEXT,
                     font=("Helvetica", 20, "bold")).pack(anchor="w", padx=12, pady=(0, 10))

        # Live typed text
        tk.Label(outer, text="LIVE TEXT", bg=BG, fg=TEXT_DIM,
                 font=("Helvetica", 8, "bold")).pack(anchor="w")
        self.live = tk.Text(outer, height=4, bg=PANEL_ALT, fg=TEXT,
                            insertbackground=ACCENT, relief="flat",
                            highlightbackground=BORDER, highlightthickness=1,
                            font=MONO, wrap="word", padx=10, pady=8)
        self.live.pack(fill="x", pady=(4, 10))
        self.live.configure(state="disabled")

        # Controls
        controls = tk.Frame(outer, bg=BG)
        controls.pack(fill="x", pady=(0, 10))
        self.btn_pause = tk.Button(controls, text="Pause", command=self.toggle_pause,
                                   bg=ACCENT, fg="#ffffff", relief="flat",
                                   activebackground="#4a7cd8", activeforeground="#fff",
                                   font=("Helvetica", 10, "bold"), padx=14, pady=6,
                                   bd=0, cursor="hand2")
        self.btn_pause.pack(side="left")
        tk.Button(controls, text="Export CSV", command=self.export_csv,
                  bg=PANEL_ALT, fg=TEXT, relief="flat", activebackground=BORDER,
                  activeforeground=TEXT, font=("Helvetica", 10), padx=14, pady=6,
                  bd=0, cursor="hand2").pack(side="left", padx=6)
        tk.Button(controls, text="Clear", command=self.clear_all,
                  bg=PANEL_ALT, fg=DANGER, relief="flat", activebackground=BORDER,
                  activeforeground=DANGER, font=("Helvetica", 10), padx=14, pady=6,
                  bd=0, cursor="hand2").pack(side="left")

        # Top keys
        tk.Label(outer, text="MOST USED KEYS", bg=BG, fg=TEXT_DIM,
                 font=("Helvetica", 8, "bold")).pack(anchor="w")
        self.top_lbl = tk.Label(outer, text="No keys yet.", bg=BG, fg=TEXT_DIM,
                                font=MONO, justify="left", wraplength=720, anchor="w")
        self.top_lbl.pack(fill="x", pady=(4, 10))

        # Log table
        tk.Label(outer, text="KEY LOG", bg=BG, fg=TEXT_DIM,
                 font=("Helvetica", 8, "bold")).pack(anchor="w")
        table_frame = tk.Frame(outer, bg=PANEL_ALT)
        table_frame.pack(fill="both", expand=True, pady=(4, 0))

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TK.Treeview", background=PANEL_ALT, fieldbackground=PANEL_ALT,
                        foreground=TEXT, rowheight=22, borderwidth=0)
        style.configure("TK.Treeview.Heading", background=PANEL, foreground=TEXT_DIM,
                        relief="flat", font=("Helvetica", 8, "bold"))
        style.map("TK.Treeview", background=[("selected", ACCENT)])

        cols = ("time", "key", "mods", "code")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings",
                                 style="TK.Treeview")
        for c, w, txt in [("time", 90, "Time"), ("key", 150, "Key"),
                          ("mods", 140, "Modifiers"), ("code", 120, "Code")]:
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor="w")
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # -- listener ----------------------------------------------------------
    def _start_listener(self):
        self.listener = keyboard.Listener(on_press=self._on_press,
                                          on_release=self._on_release)
        self.listener.daemon = True
        self.listener.start()

    def _on_press(self, key):
        # Runs on the listener thread -- just queue the raw data.
        self.events.put(("press", key, datetime.now()))

    def _on_release(self, key):
        self.events.put(("release", key, datetime.now()))

    def _drain_events(self):
        try:
            while True:
                kind, key, when = self.events.get_nowait()
                if kind == "press":
                    self._handle_press(key, when)
                else:
                    self._handle_release(key)
        except queue.Empty:
            pass
        self.root.after(80, self._drain_events)

    def _handle_release(self, key):
        name = getattr(key, "name", None)
        if name in MODIFIER_NAMES:
            self.held_mods.discard(modifier_label(name))

    def _handle_press(self, key, when):
        name = getattr(key, "name", None)
        if name in MODIFIER_NAMES:
            self.held_mods.add(modifier_label(name))

        if self.paused:
            return

        display, is_char, raw = describe_key(key)

        # Skip recording the standalone modifier key itself as a "key" row?
        # We keep it -- users often want to see modifier presses too.
        mods = sorted(m for m in self.held_mods
                      if m != modifier_label(name or ""))

        code = ""
        if isinstance(key, keyboard.KeyCode) and key.vk is not None:
            code = "vk%s" % key.vk
        elif name:
            code = name

        self.log.append({
            "time": when,
            "name": display,
            "mods": mods,
            "code": code,
        })
        if len(self.log) > MAX_LOG:
            self.log = self.log[-MAX_LOG:]

        self.total += 1
        self.counts[display] += 1

        # Live buffer
        if is_char and raw is not None and not (set(self.held_mods) & {"Ctrl", "Cmd", "Alt"}):
            self.typed += raw
            self.chars += 1
        elif name == "space":
            self.typed += " "
            self.chars += 1
        elif name == "enter":
            self.typed += "\n"
        elif name == "tab":
            self.typed += "\t"
        elif name == "backspace":
            self.typed = self.typed[:-1]
        if len(self.typed) > LIVE_LIMIT:
            self.typed = self.typed[-LIVE_LIMIT:]

        self._render_live()
        self._render_top()
        self._append_row(self.log[-1])

    # -- rendering ---------------------------------------------------------
    def _render_live(self):
        self.live.configure(state="normal")
        self.live.delete("1.0", "end")
        self.live.insert("1.0", self.typed)
        self.live.see("end")
        self.live.configure(state="disabled")

    def _render_top(self):
        if not self.counts:
            self.top_lbl.configure(text="No keys yet.")
            return
        top = self.counts.most_common(14)
        self.top_lbl.configure(
            text="   ".join("%s ×%d" % (k, n) for k, n in top))

    def _append_row(self, entry):
        self.tree.insert("", 0, values=(
            entry["time"].strftime("%H:%M:%S"),
            entry["name"],
            " + ".join(entry["mods"]) if entry["mods"] else "—",
            entry["code"],
        ))
        # Trim displayed rows to MAX_LOG.
        children = self.tree.get_children()
        if len(children) > MAX_LOG:
            for iid in children[MAX_LOG:]:
                self.tree.delete(iid)

    def _tick(self):
        self.stat_vars["total"].set(str(self.total))
        self.stat_vars["chars"].set(str(self.chars))
        elapsed = (datetime.now() - self.start_time).total_seconds()
        mins = elapsed / 60.0
        kpm = round(self.total / mins) if mins > 0.02 else 0
        self.stat_vars["kpm"].set(str(kpm))
        self.stat_vars["time"].set("%d:%02d" % (int(elapsed) // 60, int(elapsed) % 60))
        self.root.after(1000, self._tick)

    # -- controls ----------------------------------------------------------
    def toggle_pause(self):
        self.paused = not self.paused
        if self.paused:
            self.btn_pause.configure(text="Resume", bg=PANEL_ALT, fg=TEXT)
            self.dot.itemconfig(self._dot_id, fill=TEXT_DIM)
            self.status_lbl.configure(text="Paused — not recording")
            self.root.title("TrackKeys — paused")
        else:
            self.btn_pause.configure(text="Pause", bg=ACCENT, fg="#ffffff")
            self.dot.itemconfig(self._dot_id, fill=SUCCESS)
            self.status_lbl.configure(text="Recording")
            self.root.title("TrackKeys — recording (system-wide)")

    def clear_all(self):
        if not messagebox.askyesno("Clear", "Clear the live text, stats and log?"):
            return
        self.total = 0
        self.chars = 0
        self.log = []
        self.counts = Counter()
        self.typed = ""
        self.start_time = datetime.now()
        self._render_live()
        self._render_top()
        for iid in self.tree.get_children():
            self.tree.delete(iid)

    def export_csv(self):
        if not self.log:
            messagebox.showinfo("Export", "Nothing to export yet.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile="trackkeys-%s.csv" % datetime.now().strftime("%Y%m%d-%H%M%S"),
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["timestamp", "key", "modifiers", "code"])
                for e in self.log:
                    writer.writerow([
                        e["time"].isoformat(),
                        e["name"],
                        " + ".join(e["mods"]),
                        e["code"],
                    ])
            messagebox.showinfo("Export", "Saved %d rows to:\n%s" % (len(self.log), path))
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc))

    def on_close(self):
        if self.listener is not None:
            self.listener.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    TrackKeys(root)
    root.mainloop()


if __name__ == "__main__":
    main()
