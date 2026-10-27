"""Shared pieces of every Estante screen: paper, ink, the footer, the final quantize."""
from PIL import ImageDraw

from .palette import BLACK, GREEN, RED, WHITE, quantize
from .state import message_text
from .text import SANS, ellipsize, font, text_width

PAPER = WHITE
INK = BLACK

QUIET = "QUIET"
BRAND = "ESTANTE"


def draw_footer(draw: ImageDraw.ImageDraw, pal, x0, x1, y_rule, y_text, default_left, badge, scale=1.0):
    """Rule, then the left note (QUIET tag, message or default) and the ESTANTE mark."""
    s = scale

    def px(v):
        return max(1, round(v * s))

    badge = badge or {}
    draw.line([(x0, y_rule), (x1, y_rule)], fill=pal[INK], width=px(1))
    x = x0
    if badge.get("dnd"):
        tag_font = font(SANS, px(11), 700)
        pad = px(6)
        tw = text_width(tag_font, QUIET)
        half = px(11) // 2 + pad // 2
        draw.rounded_rectangle(
            [x, y_text - half, x + tw + 2 * pad, y_text + half], radius=px(4), fill=pal[INK]
        )
        draw.text((x + pad, y_text), QUIET, font=tag_font, fill=pal[PAPER], anchor="lm")
        x += tw + 2 * pad + px(10)
    brand_font = font(SANS, px(14), 700)
    message = message_text(badge)
    if message:
        r = px(6) / 2
        draw.ellipse([x, y_text - r, x + 2 * r, y_text + r], fill=pal[RED])
        x += px(6) + px(8)
        text, colour = message, pal[RED]
    else:
        text, colour = default_left, pal[INK]
    left_font, show_brand = _fit_left(text, x1 - x, text_width(brand_font, BRAND) + px(16), px)
    draw.text(
        (x, y_text), ellipsize(text, left_font, x1 - x), font=left_font, fill=colour, anchor="lm"
    )
    if show_brand:
        draw.text((x1, y_text), BRAND, font=brand_font, fill=pal[GREEN], anchor="rm")


def _fit_left(text, room, brand_room, px):
    """Font for the left note and whether ESTANTE still fits beside it.

    A waiting message matters more than the mark: keep Inter 15 beside the mark
    when it fits, else give the mark's room to the message, then step down to 12.
    """
    full = font(SANS, px(15), 500)
    if text_width(full, text) <= room - brand_room:
        return full, True
    for size in (15, 14, 13, 12):
        fnt = font(SANS, px(size), 500)
        if text_width(fnt, text) <= room:
            return fnt, False
    return font(SANS, px(12), 500), False


def finish(canvas_rgb, pal):
    """The last step of every render: snap the canvas onto the driver palette, no dither."""
    return quantize(canvas_rgb, pal, dither=False)
