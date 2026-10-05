"""
TrackKeys Mirror -- desktop GUI.

A graphical front-end over engine.Engine. Launch it with no console window by
double-clicking (Windows runs .pyw with pythonw.exe) or:

    pythonw gui.pyw

Pick which VirtualBox VMs to mirror, start/stop VMs, choose the mouse source
monitor, and start/stop mirroring -- no config file editing, no terminal.
Mirroring can also be toggled anywhere with Ctrl+Alt+F. Selections are
remembered between runs.

WINDOWS + VIRTUALBOX ONLY.
"""

import json
import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from engine import Engine

# Theme (matches the rest of TrackKeys).
BG = "#14161c"
PANEL = "#1c1f28"
PANEL_ALT = "#232734"
BORDER = "#333846"
TEXT = "#e8e9ee"
TEXT_DIM = "#9096a8"
ACCENT = "#5b8def"
DANGER = "#e35d6a"
SUCCESS = "#4caf7d"

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "gui_settings.json")


def load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


class App:
    def __init__(self, root):
        self.root = root
        self.engine = Engine()
        self.vm_vars = {}          # vm name -> BooleanVar
        self.mirroring = False
        self._applied_monitor = None
        self._ready = False
        self.tray = None
        self._icon_img = None
        self.settings = load_settings()
        self._saved_selected = set(self.settings.get("selected_vms", []))

        # rate tracking
        self._last_keys = 0
        self._last_moves = 0
        self._last_time = None

        self._build_ui()
        self._populate_monitors()
        self._apply_settings_pre_start()

        self._set_window_icon()
        self.engine.start()
        self._ready = True
        self._start_tray()
        self.root.after(100, self._poll)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- UI ----------------------------------------------------------------
    def _build_ui(self):
        r = self.root
        r.title("TrackKeys Mirror")
        r.configure(bg=BG)
        r.geometry("740x660")
        r.minsize(620, 540)

        outer = tk.Frame(r, bg=BG)
        outer.pack(fill="both", expand=True, padx=16, pady=14)

        head = tk.Frame(outer, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text="TrackKeys Mirror", bg=BG, fg=TEXT,
                 font=("Helvetica", 17, "bold")).pack(side="left")
        self.dot = tk.Canvas(head, width=12, height=12, bg=BG,
                             highlightthickness=0)
        self.dot.pack(side="right")
        self._dot_id = self.dot.create_oval(2, 2, 11, 11, fill=TEXT_DIM,
                                             outline="")
        self.status_lbl = tk.Label(head, text="starting...", bg=BG, fg=TEXT_DIM,
                                    font=("Helvetica", 10))
        self.status_lbl.pack(side="right", padx=8)

        body = tk.Frame(outer, bg=BG)
        body.pack(fill="both", expand=True, pady=(12, 0))

        # Left: VM selection
        left = tk.Frame(body, bg=PANEL, highlightbackground=BORDER,
                        highlightthickness=1)
        left.pack(side="left", fill="both", expand=True)
        lh = tk.Frame(left, bg=PANEL)
        lh.pack(fill="x", padx=12, pady=(10, 6))
        tk.Label(lh, text="VIRTUAL MACHINES", bg=PANEL, fg=TEXT_DIM,
                 font=("Helvetica", 9, "bold")).pack(side="left")
        tk.Button(lh, text="Refresh", command=self.engine.refresh_vms,
                  bg=PANEL_ALT, fg=TEXT, relief="flat", bd=0, padx=10, pady=3,
                  cursor="hand2").pack(side="right")

        canvas = tk.Canvas(left, bg=PANEL, highlightthickness=0)
        sb = ttk.Scrollbar(left, orient="vertical", command=canvas.yview)
        self.vm_frame = tk.Frame(canvas, bg=PANEL)
        self.vm_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.vm_frame, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(12, 0),
                    pady=(0, 10))
        sb.pack(side="right", fill="y", pady=(0, 10))
        tk.Label(self.vm_frame, text="Looking for VMs...", bg=PANEL,
                 fg=TEXT_DIM).pack(anchor="w", pady=6)

        # Right: controls
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="y", padx=(12, 0))

        tk.Label(right, text="MOUSE SOURCE MONITOR", bg=BG, fg=TEXT_DIM,
                 font=("Helvetica", 9, "bold")).pack(anchor="w")
        self.monitor_var = tk.StringVar(value="Primary (auto)")
        self.monitor_map = {"Primary (auto)": "primary"}
        self.monitor_menu = tk.OptionMenu(right, self.monitor_var,
                                          "Primary (auto)")
        self.monitor_menu.configure(bg=PANEL_ALT, fg=TEXT, relief="flat",
                                    highlightthickness=0, bd=0, width=22,
                                    activebackground=BORDER)
        self.monitor_menu["menu"].configure(bg=PANEL_ALT, fg=TEXT)
        self.monitor_menu.pack(anchor="w", pady=(4, 12), fill="x")
        self.monitor_var.trace_add("write", self._on_monitor_change)

        self.mouse_var = tk.BooleanVar(value=True)
        tk.Checkbutton(right, text="Mirror mouse", variable=self.mouse_var,
                       command=self._on_mouse_toggle, bg=BG, fg=TEXT,
                       selectcolor=PANEL_ALT, activebackground=BG,
                       activeforeground=TEXT, highlightthickness=0,
                       bd=0).pack(anchor="w")

        self.hybrid_var = tk.BooleanVar(value=True)
        tk.Checkbutton(right, text="Smooth drags (hybrid mouse)",
                       variable=self.hybrid_var, command=self._on_hybrid_toggle,
                       bg=BG, fg=TEXT, selectcolor=PANEL_ALT,
                       activebackground=BG, activeforeground=TEXT,
                       highlightthickness=0, bd=0).pack(anchor="w")

        self.headless_var = tk.BooleanVar(value=False)
        tk.Checkbutton(right, text="Start VMs headless", variable=self.headless_var,
                       command=self._on_headless_toggle, bg=BG, fg=TEXT,
                       selectcolor=PANEL_ALT, activebackground=BG,
                       activeforeground=TEXT, highlightthickness=0,
                       bd=0).pack(anchor="w", pady=(0, 14))

        self.start_btn = tk.Button(right, text="Start Mirroring",
                                   command=self._toggle_mirror, bg=ACCENT,
                                   fg="#ffffff", relief="flat", bd=0,
                                   font=("Helvetica", 11, "bold"),
                                   padx=14, pady=9, cursor="hand2")
        self.start_btn.pack(anchor="w", fill="x")
        tk.Label(right, text="or press Ctrl+Alt+F anywhere", bg=BG, fg=TEXT_DIM,
                 font=("Helvetica", 8)).pack(anchor="w", pady=(4, 16))

        self.stats_lbl = tk.Label(right, text="keys: 0   moves: 0", bg=BG,
                                  fg=TEXT_DIM, font=("Helvetica", 9),
                                  justify="left")
        self.stats_lbl.pack(anchor="w")

        tk.Label(outer, text="LOG", bg=BG, fg=TEXT_DIM,
                 font=("Helvetica", 9, "bold")).pack(anchor="w", pady=(12, 2))
        self.log = tk.Text(outer, height=8, bg=PANEL_ALT, fg=TEXT, relief="flat",
                           highlightbackground=BORDER, highlightthickness=1,
                           font=("Consolas", 9), wrap="word", padx=8, pady=6)
        self.log.pack(fill="both", expand=False)
        self.log.configure(state="disabled")

    def _populate_monitors(self):
        try:
            from capture import list_monitors
            mons = list_monitors()
        except Exception:
            mons = []
        menu = self.monitor_menu["menu"]
        menu.delete(0, "end")
        self.monitor_map = {"Primary (auto)": "primary"}
        menu.add_command(label="Primary (auto)",
                         command=lambda: self.monitor_var.set("Primary (auto)"))
        for i, (left, top, w, h) in enumerate(mons, start=1):
            label = "Monitor %d: %dx%d @(%d,%d)" % (i, w, h, left, top)
            self.monitor_map[label] = i
            menu.add_command(label=label,
                             command=lambda lbl=label: self.monitor_var.set(lbl))

    def _apply_settings_pre_start(self):
        """Restore saved settings onto the widgets / engine before start()."""
        s = self.settings
        self.mouse_var.set(bool(s.get("mouse_enabled", True)))
        self.headless_var.set(bool(s.get("launch_headless", False)))
        mode = s.get("mouse_mode", "hybrid")
        self.hybrid_var.set(mode != "absolute")
        self.engine.mouse_enabled = self.mouse_var.get()
        self.engine.mouse_mode = mode
        self.engine.launch_type = "headless" if self.headless_var.get() else "gui"

        saved_monitor = s.get("monitor", "primary")
        for label, value in self.monitor_map.items():
            if value == saved_monitor:
                self.monitor_var.set(label)
                break

        geom = s.get("window")
        if geom:
            try:
                self.root.geometry(geom)
            except Exception:
                pass

    # -- settings ----------------------------------------------------------
    def _save_settings(self):
        if not self._ready:
            return
        data = {
            "selected_vms": self._selected_vms(),
            "monitor": self.monitor_map.get(self.monitor_var.get(), "primary"),
            "mouse_enabled": self.mouse_var.get(),
            "mouse_mode": "hybrid" if self.hybrid_var.get() else "absolute",
            "launch_headless": self.headless_var.get(),
            "window": self.root.winfo_geometry(),
        }
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
        except Exception:
            pass

    # -- actions -----------------------------------------------------------
    def _selected_vms(self):
        return [n for n, v in self.vm_vars.items() if v.get()]

    def _toggle_mirror(self):
        if not self.mirroring:
            names = self._selected_vms()
            if not names:
                self._append_log("Select at least one VM first.")
                return
            monitor = self.monitor_map.get(self.monitor_var.get(), "primary")
            if monitor != self._applied_monitor:
                self.engine.set_monitor(monitor)
                self._applied_monitor = monitor
            self.engine.set_targets(names)
            self.engine.set_enabled(True)
            self._save_settings()
        else:
            self.engine.set_enabled(False)

    def _on_mouse_toggle(self):
        self.engine.set_mouse_enabled(self.mouse_var.get())
        self._save_settings()

    def _on_headless_toggle(self):
        self.engine.set_launch_type(self.headless_var.get())
        self._save_settings()

    def _on_hybrid_toggle(self):
        self.engine.set_mouse_mode("hybrid" if self.hybrid_var.get()
                                   else "absolute")
        self._save_settings()

    def _on_monitor_change(self, *_):
        if not self._ready:
            return
        monitor = self.monitor_map.get(self.monitor_var.get(), "primary")
        if self.mirroring and monitor != self._applied_monitor:
            self.engine.set_monitor(monitor)
            self._applied_monitor = monitor
        self._save_settings()

    def _launch_vm(self, name):
        self.engine.launch_vm(name)

    def _poweroff_vm(self, name):
        if messagebox.askyesno("Power off",
                               "Hard power-off %s?\n(Unsaved work in the guest "
                               "will be lost.)" % name):
            self.engine.poweroff_vm(name)

    # -- status polling ----------------------------------------------------
    def _poll(self):
        try:
            while True:
                s = self.engine.status.get_nowait()
                self._on_status(s)
        except Exception:
            pass
        self.root.after(100, self._poll)

    def _on_status(self, s):
        kind = s.get("kind")
        if kind in ("log", "error"):
            self._append_log(("! " if kind == "error" else "") + s["msg"])
        elif kind == "vms":
            self._rebuild_vms(s["vms"])
        elif kind == "enabled":
            self._set_mirroring(s["value"])
        elif kind == "stats":
            self._update_stats(s)
        elif kind == "stopped":
            self._append_log("Engine stopped.")

    def _update_stats(self, s):
        import time
        self._set_mirroring(s["enabled"])
        now = time.monotonic()
        kps = mps = 0
        if self._last_time is not None:
            dt = now - self._last_time
            if dt > 0:
                kps = max(0, round((s["keys"] - self._last_keys) / dt))
                mps = max(0, round((s["moves"] - self._last_moves) / dt))
        self._last_keys, self._last_moves, self._last_time = (
            s["keys"], s["moves"], now)
        alive = sum(1 for _, a, _, _ in s["targets"] if a)
        total = len(s["targets"])
        self.stats_lbl.configure(
            text="keys: %d (%d/s)\nmoves: %d (%d/s)\nVMs: %d/%d attached"
            % (s["keys"], kps, s["moves"], mps, alive, total))

    def _rebuild_vms(self, vms):
        prev = {n: v.get() for n, v in self.vm_vars.items()}
        for w in self.vm_frame.winfo_children():
            w.destroy()
        self.vm_vars = {}
        if not vms:
            tk.Label(self.vm_frame, text="No VMs registered in VirtualBox.",
                     bg=PANEL, fg=TEXT_DIM).pack(anchor="w", pady=6)
            return
        for name, running in vms:
            default = prev[name] if name in prev else (name in self._saved_selected)
            var = tk.BooleanVar(value=default)
            self.vm_vars[name] = var
            row = tk.Frame(self.vm_frame, bg=PANEL)
            row.pack(fill="x", anchor="w", pady=1)
            tk.Checkbutton(row, text=name, variable=var,
                           command=self._save_settings, bg=PANEL, fg=TEXT,
                           selectcolor=PANEL_ALT, activebackground=PANEL,
                           activeforeground=TEXT, highlightthickness=0,
                           bd=0).pack(side="left")
            if running:
                tk.Button(row, text="Power off",
                          command=lambda n=name: self._poweroff_vm(n),
                          bg=PANEL, fg=DANGER, relief="flat", bd=0,
                          font=("Helvetica", 8), cursor="hand2"
                          ).pack(side="right", padx=(6, 8))
                tk.Label(row, text="running", bg=PANEL, fg=SUCCESS,
                         font=("Helvetica", 8)).pack(side="right")
            else:
                tk.Button(row, text="Start",
                          command=lambda n=name: self._launch_vm(n),
                          bg=PANEL, fg=ACCENT, relief="flat", bd=0,
                          font=("Helvetica", 8), cursor="hand2"
                          ).pack(side="right", padx=(6, 8))
                tk.Label(row, text="stopped", bg=PANEL, fg=TEXT_DIM,
                         font=("Helvetica", 8)).pack(side="right")

    def _set_mirroring(self, value):
        self.mirroring = value
        if value:
            self.dot.itemconfig(self._dot_id, fill=SUCCESS)
            self.status_lbl.configure(text="mirroring")
            self.start_btn.configure(text="Stop Mirroring", bg=DANGER)
        else:
            self.dot.itemconfig(self._dot_id, fill=TEXT_DIM)
            self.status_lbl.configure(text="idle")
            self.start_btn.configure(text="Start Mirroring", bg=ACCENT)

    def _append_log(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # -- icon / tray -------------------------------------------------------
    def _set_window_icon(self):
        try:
            from PIL import ImageTk
            from icon import make_image
            self._icon_img = ImageTk.PhotoImage(make_image(64))
            self.root.iconphoto(True, self._icon_img)
        except Exception:
            pass  # icon is cosmetic; keep going without it

    def _start_tray(self):
        try:
            import pystray
            from icon import make_image
        except Exception:
            self._append_log("System tray unavailable "
                             "(pip install pystray pillow).")
            return
        image = make_image(64)

        def toggle_text(_item):
            return "Stop mirroring" if self.mirroring else "Start mirroring"

        menu = pystray.Menu(
            pystray.MenuItem("Show window", self._tray_show, default=True),
            pystray.MenuItem(toggle_text, self._tray_toggle),
            pystray.MenuItem("Quit", self._tray_quit),
        )
        self.tray = pystray.Icon("TrackKeysMirror", image,
                                 "TrackKeys Mirror", menu)
        threading.Thread(target=self.tray.run, name="tray",
                         daemon=True).start()

    def _tray_show(self, icon=None, item=None):
        self.root.after(0, self._show_window)

    def _tray_toggle(self, icon=None, item=None):
        # engine calls are thread-safe; safe from the tray thread.
        self.engine.set_enabled(not self.mirroring)

    def _tray_quit(self, icon=None, item=None):
        self.root.after(0, self._real_quit)

    def _show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    # -- shutdown ----------------------------------------------------------
    def _on_close(self):
        self._save_settings()
        if self.tray is not None:
            # Hide to tray instead of quitting; quit from the tray menu.
            self.root.withdraw()
            self._append_log("Minimized to tray. Use the tray icon to restore "
                             "or quit.")
        else:
            self._real_quit()

    def _real_quit(self):
        self._save_settings()
        self.status_lbl.configure(text="stopping...")
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.engine.stop()
        self.root.after(700, self.root.destroy)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
