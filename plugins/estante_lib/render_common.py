"""Shared pieces of every Estante render: the footer with its badge and the ESTANTE mark."""
from PIL import ImageDraw

from .state import message_text
from .text import SANS, ellipsize, font, text_width

QUIET = "QUIET"
BRAND = "ESTANTE"

LEFT_SIZES = (18, 17, 16, 15, 14)
BRAND_SIZE = 16
QUIET_SIZE = 13


def draw_footer(
    draw: ImageDraw.ImageDraw, theme, x0, x1, y_rule, y_text, default_left, badge, scale=1.0, mark=True
):
    """Rule, then the left note (QUIET tag, message or default) and, with `mark`, ESTANTE."""
    s = scale

    def px(v):
        return max(1, round(v * s))

    badge = badge or {}
    draw.line([(x0, y_rule), (x1, y_rule)], fill=theme.muted, width=px(1))
    x = x0
    if badge.get("dnd"):
        tag_font = font(SANS, px(QUIET_SIZE), 700)
        pad = px(7)
        tw = text_width(tag_font, QUIET)
        half = px(QUIET_SIZE) // 2 + pad // 2
        draw.rounded_rectangle(
            [x, y_text - half, x + tw + 2 * pad, y_text + half], radius=px(4), fill=theme.ink
        )
        draw.text((x + pad, y_text), QUIET, font=tag_font, fill=theme.bg, anchor="lm")
        x += tw + 2 * pad + px(12)
    message = message_text(badge)
    if message:
        r = px(8) / 2
        draw.ellipse([x, y_text - r, x + 2 * r, y_text + r], fill=theme.accent)
        x += px(8) + px(10)
        text, colour = message, theme.accent
    else:
        text, colour = default_left, theme.ink
    brand_font = font(SANS, px(BRAND_SIZE), 700)
    brand_room = text_width(brand_font, BRAND) + px(20) if mark else 0
    left_font, show_brand = _fit_left(text, x1 - x, brand_room, px)
    draw.text(
        (x, y_text), ellipsize(text, left_font, x1 - x), font=left_font, fill=colour, anchor="lm"
    )
    if mark and show_brand:
        draw.text((x1, y_text), BRAND, font=brand_font, fill=theme.mark, anchor="rm")


def _fit_left(text, room, brand_room, px):
    """Font for the left note and whether ESTANTE still fits beside it.

    A waiting message matters more than the mark: keep the full size beside the mark
    when it fits, else give the mark's room to the message, then step the size down.
    """
    full = font(SANS, px(LEFT_SIZES[0]), 500)
    if text_width(full, text) <= room - brand_room:
        return full, True
    for size in LEFT_SIZES:
        fnt = font(SANS, px(size), 500)
        if text_width(fnt, text) <= room:
            return fnt, False
    return font(SANS, px(LEFT_SIZES[-1]), 500), False
