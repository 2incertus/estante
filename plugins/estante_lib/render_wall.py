"""The cover wall: every book of one library as a tile, in shelf order, on one screen."""
import math
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageOps

from .model import STATUS_LISTENING, STATUS_READ, STATUS_UNREAD, ShelfBook
from .render_common import draw_footer
from .text import SANS, SERIF, ellipsize, font, text_width

BASE_W, BASE_H = 1230, 1080
TITLE_Y, COUNTS_Y, SUBTITLE_Y, RULE_Y = 48, 36, 66, 86
GRID_TOP, GRID_H = 110, 920
FOOTER_RULE_Y, FOOTER_TEXT_Y = 1044, 1064
TILE_GAP = 3
FRAME = 3
# Unread recedes into the page on both themes: greyscale, then 70% of the way to the background.
UNREAD_TOWARD_BG = 0.70


@dataclass
class WallView:
    """One library's wall. The wall never shows the badge (`badge_free`): the column carries it."""

    title: str
    subtitle: str
    books: "list[ShelfBook]"
    covers: "dict[str, Image.Image]"
    counts: dict
    badge_free: bool = True


def grid_for(n, area, gap=TILE_GAP):
    """(cols, rows, cell) for n square cells filling `area`; a cell is never thinner than gap + 1."""
    w, h = area
    if n <= 0:
        return 1, 1, min(w, h)
    cols = math.ceil(math.sqrt(n * w / h))
    rows = math.ceil(n / cols)
    return cols, rows, max(gap + 1, min(w // cols, h // rows))


def _unread(art, theme):
    """An unread book: greyscale, blended 70% toward the background so it sits behind the read ones."""
    grey = art.convert("L").convert("RGB")
    return Image.blend(grey, Image.new("RGB", art.size, theme.bg), UNREAD_TOWARD_BG)


def _tile(cover, tile, status, theme, blanks):
    if cover is None:
        if status not in blanks:
            art = Image.new("RGB", (tile, tile), theme.paper_tile)
            blanks[status] = _unread(art, theme) if status == STATUS_UNREAD else art
        return blanks[status]
    art = ImageOps.fit(cover.convert("RGB"), (tile, tile), Image.Resampling.LANCZOS)
    return _unread(art, theme) if status == STATUS_UNREAD else art


def _header(draw, theme, view, w, p, fs):
    counts = view.counts
    counts_text = (
        f"{counts.get(STATUS_READ, 0)} read  ·  {counts.get(STATUS_LISTENING, 0)} listening"
        f"  ·  {counts.get(STATUS_UNREAD, 0)} to go"
    )
    counts_font = font(SANS, fs(20), 500)
    sub_font = font(SANS, fs(16))
    title_font = font(SERIF, fs(44), 500)
    draw.text((w, p(COUNTS_Y)), counts_text, font=counts_font, fill=theme.ink, anchor="rm")
    draw.text((w, p(SUBTITLE_Y)), view.subtitle, font=sub_font, fill=theme.series, anchor="rm")
    widest = max(text_width(counts_font, counts_text), text_width(sub_font, view.subtitle))
    room = w - widest - p(32)
    draw.text(
        (0, p(TITLE_Y)), ellipsize(view.title, title_font, room), font=title_font, fill=theme.ink, anchor="lm"
    )
    y = p(RULE_Y)
    draw.rectangle([0, y, w - 1, y + max(1, p(2)) - 1], fill=theme.ink)


def render_wall(view, theme, size=(BASE_W, BASE_H), scale=1.0):
    """The wall as an RGB image of `size`, coordinates scaled by `scale`."""
    w, h = size
    s = scale

    def p(v):
        return round(v * s)

    def fs(v):
        return max(1, round(v * s))

    canvas = Image.new("RGB", (w, h), theme.bg)
    draw = ImageDraw.Draw(canvas)
    _header(draw, theme, view, w, p, fs)

    gap = max(1, p(TILE_GAP))
    cols, rows, cell = grid_for(len(view.books), (w, p(GRID_H)), gap)
    tile = cell - gap
    x0 = max(0, (w - (cols * cell - gap)) // 2)
    y0 = p(GRID_TOP)
    blanks = {}
    frames = []
    for i, book in enumerate(view.books):
        r, c = divmod(i, cols)
        x, y = x0 + c * cell, y0 + r * cell
        canvas.paste(_tile(view.covers.get(book.item_id), tile, book.status, theme, blanks), (x, y))
        if book.status == STATUS_LISTENING:
            frames.append((x, y))
    # Frames go on last: the next tile would cover a frame drawn in the gap before it.
    edge = max(1, p(FRAME))
    for x, y in frames:
        draw.rectangle(
            [x - edge, y - edge, x + tile - 1 + edge, y + tile - 1 + edge], outline=theme.frame, width=edge
        )

    total = view.counts.get("total", len(view.books))
    pct = round(100 * view.counts.get(STATUS_READ, 0) / total) if total else 0
    draw_footer(
        draw, theme, 0, w, p(FOOTER_RULE_Y), p(FOOTER_TEXT_Y), f"{pct}% of the shelf read", None, scale=s
    )
    return canvas
