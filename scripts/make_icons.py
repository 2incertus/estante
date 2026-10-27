#!/usr/bin/env python3
"""Draw the two plugin icons (512x512 RGBA) shown on InkyPi's plugin grid.

Both sit on a paper rounded square in the panel's preview colours: Now listening is
a progress bar part filled with red over chapter ticks, The shelf is a 3x3 grid of
the six inks.

Usage:
    .venv/bin/python scripts/make_icons.py
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "plugins"))

from PIL import Image, ImageDraw  # noqa: E402

from estante_lib.palette import BLACK, BLUE, GREEN, PREVIEW, RED, WHITE, YELLOW  # noqa: E402

SIZE = 512
SCALE = 4  # drawn large, then downsampled for smooth edges
RADIUS = 96
INK, PAPER = PREVIEW[BLACK], PREVIEW[WHITE]
SHELF_INKS = [
    [BLACK, RED, BLUE],
    [YELLOW, WHITE, GREEN],
    [BLUE, BLACK, RED],
]


def _canvas():
    img = Image.new("RGBA", (SIZE * SCALE, SIZE * SCALE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    s = SCALE
    draw.rounded_rectangle(
        [8 * s, 8 * s, (SIZE - 8) * s - 1, (SIZE - 8) * s - 1],
        radius=RADIUS * s,
        fill=PAPER,
        outline=INK,
        width=12 * s,
    )
    return img, draw


def now_listening_icon():
    img, draw = _canvas()
    s = SCALE
    x0, x1, y0, y1 = 88, 424, 182, 266
    fill_to = x0 + round((x1 - x0) * 0.62)
    draw.rectangle([x0 * s, y0 * s, fill_to * s, y1 * s], fill=PREVIEW[RED])
    draw.rectangle([x0 * s, y0 * s, x1 * s, y1 * s], outline=INK, width=14 * s)
    for i in range(1, 6):
        x = x0 + (x1 - x0) * i // 6
        draw.rectangle([(x - 4) * s, 294 * s, (x + 4) * s, 330 * s], fill=INK)
    return img.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def shelf_icon():
    img, draw = _canvas()
    s = SCALE
    cell, gap = 104, 20
    top = (SIZE - 3 * cell - 2 * gap) // 2
    for r, row in enumerate(SHELF_INKS):
        for c, ink in enumerate(row):
            x = top + c * (cell + gap)
            y = top + r * (cell + gap)
            box = [x * s, y * s, (x + cell) * s - 1, (y + cell) * s - 1]
            draw.rectangle(box, fill=PREVIEW[ink], outline=INK if ink == WHITE else None, width=8 * s)
    return img.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def main():
    for plugin, make in (("now_listening", now_listening_icon), ("the_shelf", shelf_icon)):
        path = os.path.join(REPO, "plugins", plugin, "icon.png")
        make().save(path)
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
