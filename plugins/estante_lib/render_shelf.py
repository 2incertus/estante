"""The shelf screen: every book as a tile, in shelf order, paged or as one mosaic."""
import math
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageEnhance, ImageOps

from .model import STATUS_LISTENING, STATUS_READ, STATUS_UNREAD, ShelfBook, page_count, shelf_counts
from .palette import BLUE, RED, WHITE, driver_palette, fade, quantize, to_rgb
from .render_common import INK, PAPER, draw_footer, finish
from .state import DEFAULT_BADGE
from .text import SANS, SERIF, ellipsize, font, text_width

PAGE_COLS = 10
PAGE_ROWS = 5
PER_PAGE = PAGE_COLS * PAGE_ROWS
MOSAIC_FLAT_BELOW = 40

BASE_W, BASE_H = 800, 480
LEFT, RIGHT = 40, 760
TITLE_Y, COUNTS_Y, SUBTITLE_Y, RULE_Y = 44, 36, 60, 76
GRID_TOP, GRID_BOTTOM = 88, 432
FOOTER_RULE_Y, FOOTER_TEXT_Y = 444, 462
TILE_GAP = 4
FRAME = 3
UNREAD_FADE = 0.78
COVER_COLOR = 1.3


@dataclass
class ShelfView:
    title: str
    subtitle: str
    books: "list[ShelfBook]"
    covers: "dict[str, Image.Image]"
    page: int = 0
    layout: str = "pages"
    badge: dict = field(default_factory=lambda: dict(DEFAULT_BADGE))
    cols: int = PAGE_COLS
    rows: int = PAGE_ROWS


def page_books(books, page, per_page=PER_PAGE):
    pages = page_count(len(books), per_page)
    page = min(max(int(page), 0), pages - 1)
    start = page * per_page
    return list(books[start:start + per_page]), page, pages


def grid_for(n, area):
    w, h = area
    if n <= 0:
        return 1, 1, min(w, h)
    cols = math.ceil(math.sqrt(n * w / h))
    rows = math.ceil(n / cols)
    return cols, rows, min(w // cols, h // rows)


def _cover_tile(cover, tile, status, pal):
    if cover is None:
        art = Image.new("RGB", (tile, tile), pal[BLUE])
    else:
        art = ImageOps.fit(cover.convert("RGB"), (tile, tile))
        art = ImageEnhance.Color(art).enhance(COVER_COLOR)
    if status == STATUS_UNREAD:
        art = fade(art, pal[PAPER], UNREAD_FADE)
    return to_rgb(quantize(art, pal, dither=True), pal)


def _read_colour(cover, pal):
    """Most common ink of a read book's cover for its flat tile, never RED or paper.

    RED is reserved for books being listened to; with no other ink the tile is BLUE.
    """
    if cover is None:
        return BLUE
    counts = quantize(cover, pal, dither=False).histogram()[:6]
    counts[WHITE] = counts[RED] = 0
    best = max(range(6), key=lambda i: counts[i])
    return best if counts[best] else BLUE


def _draw_tile(canvas, draw, pal, book, cover, x, y, tile):
    if tile >= MOSAIC_FLAT_BELOW:
        canvas.paste(_cover_tile(cover, tile, book.status, pal), (x, y))
        if book.status == STATUS_LISTENING:
            draw.rectangle(
                [x - FRAME, y - FRAME, x + tile - 1 + FRAME, y + tile - 1 + FRAME],
                outline=pal[RED],
                width=FRAME,
            )
        return
    box = [x, y, x + tile - 1, y + tile - 1]
    if book.status == STATUS_LISTENING:
        draw.rectangle(box, fill=pal[RED])
    elif book.status == STATUS_READ:
        draw.rectangle(box, fill=pal[_read_colour(cover, pal)])
    else:
        draw.rectangle(box, outline=pal[INK], width=1)


def _layout(view, area_w, area_h):
    """(books on screen, tile, cell, cols, rows, page, pages) for the view's layout."""
    if view.layout == "mosaic":
        cols, rows, cell = grid_for(len(view.books), (area_w, area_h))
        return list(view.books), cell - 1, cell, cols, rows, 0, 1
    cols, rows = max(1, view.cols), max(1, view.rows)
    books, page, pages = page_books(view.books, view.page, cols * rows)
    tile = min(area_w // cols, area_h // rows) - TILE_GAP
    return books, tile, tile + TILE_GAP, cols, rows, page, pages


def _header(draw, pal, view, counts, page, pages):
    counts_text = (
        f"{counts[STATUS_READ]} read  ·  {counts[STATUS_LISTENING]} listening"
        f"  ·  {counts[STATUS_UNREAD]} to go"
    )
    counts_font = font(SANS, 16, 500)
    draw.text((RIGHT, COUNTS_Y), counts_text, font=counts_font, fill=pal[INK], anchor="rm")
    if view.layout == "mosaic":
        second = view.subtitle
    else:
        second = f"{view.subtitle}  ·  page {page + 1} of {pages}"
    sub_font = font(SANS, 13)
    title_font = font(SERIF, 36, 500)
    widest = max(text_width(counts_font, counts_text), text_width(sub_font, second))
    room = RIGHT - LEFT - widest - 24
    draw.text((RIGHT, SUBTITLE_Y), second, font=sub_font, fill=pal[BLUE], anchor="rm")
    draw.text((LEFT, TITLE_Y), ellipsize(view.title, title_font, room), font=title_font, fill=pal[INK], anchor="lm")
    draw.line([(LEFT, RULE_Y), (RIGHT, RULE_Y)], fill=pal[INK], width=2)


def render_shelf(view, size=(BASE_W, BASE_H), saturation=0.5):
    pal = driver_palette(saturation)
    page_canvas = Image.new("RGB", (BASE_W, BASE_H), pal[PAPER])
    draw = ImageDraw.Draw(page_canvas)
    # Hard-edged glyphs: antialiased edges would snap to green and blue specks in finish().
    draw.fontmode = "1"
    counts = shelf_counts(view.books)
    area_w, area_h = RIGHT - LEFT, GRID_BOTTOM - GRID_TOP
    books, tile, cell, cols, rows, page, pages = _layout(view, area_w, area_h)

    _header(draw, pal, view, counts, page, pages)
    x0 = LEFT + (area_w - cols * cell) // 2
    y0 = GRID_TOP + (area_h - rows * cell) // 2
    for i, book in enumerate(books):
        r, c = divmod(i, cols)
        _draw_tile(page_canvas, draw, pal, book, view.covers.get(book.item_id), x0 + c * cell, y0 + r * cell, tile)

    total = counts["total"]
    pct = round(100 * counts[STATUS_READ] / total) if total else 0
    draw_footer(draw, pal, LEFT, RIGHT, FOOTER_RULE_Y, FOOTER_TEXT_Y, f"{pct}% of the shelf read", view.badge)
    if tuple(size) != (BASE_W, BASE_H):
        page_canvas = page_canvas.resize(tuple(size), Image.Resampling.NEAREST)
    return finish(page_canvas, pal)
