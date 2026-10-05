"""
TrackKeys Mirror -- desktop GUI (M2).

A graphical front-end over engine.Engine. Launch it with no console window by
double-clicking (Windows runs .pyw with pythonw.exe) or:

    pythonw gui.pyw

Pick which VirtualBox VMs to mirror, choose the mouse source monitor, and start
/ stop mirroring -- no config file editing, no terminal. Mirroring can also be
toggled anywhere with Ctrl+Alt+F.

WINDOWS + VIRTUALBOX ONLY.
"""

import tkinter as tk
from tkinter import ttk

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


class App:
    def __init__(self, root):
        self.root = root
        self.engine = Engine()
        self.vm_vars = {}          # vm name -> BooleanVar
        self.mirroring = False
        self._applied_monitor = None

        self._build_ui()
        self._populate_monitors()

        self.engine.start()
        self.root.after(100, self._poll)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- UI ----------------------------------------------------------------
    def _build_ui(self):
        r = self.root
        r.title("TrackKeys Mirror")
        r.configure(bg=BG)
        r.geometry("720x640")
        r.minsize(600, 520)

        outer = tk.Frame(r, bg=BG)
        outer.pack(fill="both", expand=True, padx=16, pady=14)

        # Header: title + status
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

        # Scrollable checkbox list
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
        self._vm_placeholder()

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

        self.mouse_var = tk.BooleanVar(value=True)
        tk.Checkbutton(right, text="Mirror mouse", variable=self.mouse_var,
                       command=self._on_mouse_toggle, bg=BG, fg=TEXT,
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

        # Stats
        self.stats_lbl = tk.Label(right, text="keys: 0   moves: 0", bg=BG,
                                  fg=TEXT_DIM, font=("Helvetica", 9),
                                  justify="left")
        self.stats_lbl.pack(anchor="w")

        # Log pane
        tk.Label(outer, text="LOG", bg=BG, fg=TEXT_DIM,
                 font=("Helvetica", 9, "bold")).pack(anchor="w", pady=(12, 2))
        self.log = tk.Text(outer, height=8, bg=PANEL_ALT, fg=TEXT, relief="flat",
                           highlightbackground=BORDER, highlightthickness=1,
                           font=("Consolas", 9), wrap="word", padx=8, pady=6)
        self.log.pack(fill="both", expand=False)
        self.log.configure(state="disabled")

    def _vm_placeholder(self):
        for w in self.vm_frame.winfo_children():
            w.destroy()
        tk.Label(self.vm_frame, text="Looking for VMs...", bg=PANEL,
                 fg=TEXT_DIM, font=("Helvetica", 10)).pack(anchor="w", pady=6)

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
        else:
            self.engine.set_enabled(False)

    def _on_mouse_toggle(self):
        self.engine.set_mouse_enabled(self.mouse_var.get())

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
            self._set_mirroring(s["enabled"])
            alive = sum(1 for _, a, _, _ in s["targets"] if a)
            total = len(s["targets"])
            self.stats_lbl.configure(
                text="keys: %d   moves: %d   VMs: %d/%d attached"
                % (s["keys"], s["moves"], alive, total))
        elif kind == "stopped":
            self._append_log("Engine stopped.")

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
            var = tk.BooleanVar(value=prev.get(name, False))
            self.vm_vars[name] = var
            row = tk.Frame(self.vm_frame, bg=PANEL)
            row.pack(fill="x", anchor="w")
            tk.Checkbutton(row, text=name, variable=var, bg=PANEL, fg=TEXT,
                           selectcolor=PANEL_ALT, activebackground=PANEL,
                           activeforeground=TEXT, highlightthickness=0,
                           bd=0).pack(side="left")
            tk.Label(row, text="running" if running else "stopped", bg=PANEL,
                     fg=SUCCESS if running else TEXT_DIM,
                     font=("Helvetica", 8)).pack(side="right", padx=8)

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

    # -- shutdown ----------------------------------------------------------
    def _on_close(self):
        self.status_lbl.configure(text="stopping...")
        self.engine.stop()
        self.root.after(700, self.root.destroy)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
