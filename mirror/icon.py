"""
Programmatic app icon for TrackKeys Mirror.

Draws a small keyboard motif so we don't commit a binary asset. Used by the GUI
for the window + tray icon (as a PIL image), and by the build to emit icon.ico.

Requires Pillow (only needed for the tray icon and for packaging).
"""


def make_image(size=64):
    from PIL import Image, ImageDraw

    bg = (28, 31, 40, 255)
    accent = (91, 141, 239, 255)
    key = (232, 233, 238, 255)

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size

    # Keyboard body (rounded rect with accent border).
    pad = s * 0.08
    body = [pad, s * 0.22, s - pad, s - s * 0.22]
    radius = max(2, int(s * 0.12))
    border = max(1, int(s * 0.03))
    try:
        d.rounded_rectangle(body, radius=radius, fill=bg, outline=accent,
                            width=border)
    except AttributeError:  # very old Pillow
        d.rectangle(body, fill=bg, outline=accent, width=border)

    # Key grid: 4 columns x 2 rows, plus a spacebar.
    cols, rows = 4, 2
    gx0, gy0 = pad + s * 0.10, s * 0.30
    gx1, gy1 = s - pad - s * 0.10, s - s * 0.38
    gw = (gx1 - gx0) / cols
    gh = (gy1 - gy0) / rows
    km = s * 0.025  # key margin
    for ry in range(rows):
        for cx in range(cols):
            x0 = gx0 + cx * gw + km
            y0 = gy0 + ry * gh + km
            x1 = gx0 + (cx + 1) * gw - km
            y1 = gy0 + (ry + 1) * gh - km
            try:
                d.rounded_rectangle([x0, y0, x1, y1],
                                    radius=max(1, int(s * 0.03)), fill=key)
            except AttributeError:
                d.rectangle([x0, y0, x1, y1], fill=key)

    # Spacebar.
    sb_y0 = gy1 + km
    sb_y1 = s - s * 0.26
    try:
        d.rounded_rectangle([gx0 + gw * 0.6, sb_y0, gx1 - gw * 0.6, sb_y1],
                            radius=max(1, int(s * 0.03)), fill=key)
    except AttributeError:
        d.rectangle([gx0 + gw * 0.6, sb_y0, gx1 - gw * 0.6, sb_y1], fill=key)

    return img


def save_ico(path="icon.ico"):
    """Write a multi-resolution .ico (used by PyInstaller --icon)."""
    img = make_image(256)
    img.save(path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])
    return path


if __name__ == "__main__":
    print("wrote", save_ico())
