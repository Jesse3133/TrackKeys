# TrackKeys

**TrackKeys Mirror** — mirror your local Windows 11 keyboard and mouse, live,
into multiple Oracle VirtualBox Windows 11 guests at once.

You keep working on the host normally; a copy of every keystroke (as hardware
**scancodes**) and mouse action (as **absolute** position) is injected into each
VM from the host, through VirtualBox's own COM API. No custom software runs
inside the guests.

➡️ Everything lives in [`mirror/`](mirror/):
- [`mirror/README.md`](mirror/README.md) — setup and how to run
- [`mirror/DESIGN.md`](mirror/DESIGN.md) — architecture and rationale

> Platform: Windows 11 host + VirtualBox, Windows 11 guests. See
> `mirror/README.md` for first-time setup.
