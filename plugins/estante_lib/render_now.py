"""The Now listening screen: cover on the left, the book and its progress on the right."""
import math
from dataclasses import dataclass
from datetime import datetime

from PIL import Image, ImageDraw, ImageEnhance, ImageOps

from .model import NowListening, chapter_line, series_line, time_left
from .palette import BLUE, RED, driver_palette, quantize, to_rgb
from .render_common import INK, PAPER, draw_footer, finish
from .text import SANS, SERIF, ellipsize, fit_text, font

LABEL_NOW = "NOW LISTENING"
LABEL_LAST = "LAST LISTENED"
LABEL_NONE = "NOTHING PLAYING"
NOTHING_YET = "Nothing played yet"
MAX_TICKS = 96

BASE_W, BASE_H = 800, 480
COVER_X, COVER_Y, COVER_BOX = 40, 40, 400
COL_X0, COL_X1 = 470, 760
LABEL_Y = 62
TITLE_BASELINE = 118
BAR_Y, BAR_H = 300, 12
CHAPTER_Y, LEFT_Y = 348, 374
FOOTER_RULE_Y, FOOTER_TEXT_Y = 410, 432
# Text above the bar must end before this line so it never touches the bar.
TEXT_FLOOR = 290


@dataclass
class NowListeningView:
    nl: "NowListening | None"
    cover: "Image.Image | None"
    finished_this_year: int
    year: int
    badge: dict


def _finished_date(finished_at):
    if not finished_at:
        return ""
    return datetime.fromtimestamp(finished_at / 1000).strftime("%-d %b %Y")


def _paste_cover(canvas, cover, pal, box, origin):
    art = ImageOps.contain(cover.convert("RGB"), (box, box))
    art = ImageEnhance.Color(art).enhance(1.25)
    art = to_rgb(quantize(art, pal, dither=True), pal)
    x = origin[0] + (box - art.width) // 2
    y = origin[1] + (box - art.height) // 2
    canvas.paste(art, (x, y))


def _ticks(nl, x0, x1):
    n = nl.chapter_count
    if n < 2 or not nl.duration:
        return []
    step = math.ceil(n / MAX_TICKS)
    xs = []
    for i in range(step, n, step):
        start = nl.chapters[i].get("start", 0) or 0
        frac = min(1.0, max(0.0, start / nl.duration))
        xs.append(x0 + round((x1 - x0) * frac))
    return xs


def render_now_listening(view, size=(BASE_W, BASE_H), saturation=0.5):
    w, h = size
    s = min(w / BASE_W, h / BASE_H)

    def p(v):
        return round(v * s)

    def fs(v):
        return max(1, round(v * s))

    pal = driver_palette(saturation)
    canvas = Image.new("RGB", (w, h), pal[PAPER])
    draw = ImageDraw.Draw(canvas)
    # Hard-edged glyphs: antialiased edges would snap to green and blue specks in finish().
    draw.fontmode = "1"
    nl = view.nl
    x0, x1 = p(COL_X0), p(COL_X1)
    col_w = x1 - x0

    if nl is None:
        label = LABEL_NONE
    elif nl.finished:
        label = LABEL_LAST
    else:
        label = LABEL_NOW
    draw.text((x0, p(LABEL_Y)), label, font=font(SANS, fs(15), 600), fill=pal[RED], anchor="lm")

    if nl is None:
        draw.text(
            (x0, p(TITLE_BASELINE)), NOTHING_YET, font=font(SERIF, fs(26)), fill=pal[INK], anchor="ls"
        )
    else:
        if view.cover is not None:
            _paste_cover(canvas, view.cover, pal, p(COVER_BOX), (p(COVER_X), p(COVER_Y)))
        _draw_text_column(draw, pal, nl, x0, x1, p, fs)
        _draw_progress(draw, pal, nl, x0, x1, p, fs, col_w)

    footer_left = f"{view.finished_this_year} finished in {view.year}"
    draw_footer(draw, pal, x0, x1, p(FOOTER_RULE_Y), p(FOOTER_TEXT_Y), footer_left, view.badge, scale=s)
    return finish(canvas, pal)


def _draw_text_column(draw, pal, nl, x0, x1, p, fs):
    col_w = x1 - x0
    lines, title_font = fit_text(nl.title, SERIF, fs(52), fs(30), col_w, 2, 500)
    baseline = p(TITLE_BASELINE)
    gap = round(title_font.size * 1.05)
    for i, line in enumerate(lines):
        draw.text((x0, baseline + i * gap), line, font=title_font, fill=pal[INK], anchor="ls")
    title_bottom = baseline + (len(lines) - 1) * gap + title_font.getmetrics()[1]

    # (text, font, colour, line advance below the previous baseline)
    rows = []
    if nl.author:
        rows.append((nl.author, font(SERIF, fs(26)), pal[INK]))
    if nl.narrator:
        rows.append((f"read by {nl.narrator}", font(SERIF, fs(20)), pal[INK]))
    series = series_line(nl.series)
    if series:
        rows.append((series, font(SERIF, fs(19)), pal[BLUE]))

    floor = p(TEXT_FLOOR)
    top = title_bottom + p(14)
    placed = []
    for text, fnt, colour in rows:
        ascent, descent = fnt.getmetrics()
        if not placed:
            base = top + ascent
        else:
            base = placed[-1][0] + round(fnt.size * 1.5)
        if base + descent > floor:
            break
        placed.append((base, text, fnt, colour))
    for base, text, fnt, colour in placed:
        draw.text((x0, base), ellipsize(text, fnt, col_w), font=fnt, fill=colour, anchor="ls")


def _draw_progress(draw, pal, nl, x0, x1, p, fs, col_w):
    y, bh = p(BAR_Y), max(4, p(BAR_H))
    edge = max(1, p(2))
    fill_w = col_w if nl.finished else max(p(4), int(col_w * nl.pct))
    draw.rectangle([x0, y, x0 + fill_w - 1, y + bh - 1], fill=pal[RED])
    draw.rectangle([x0, y, x1 - 1, y + bh - 1], outline=pal[INK], width=edge)
    for tx in _ticks(nl, x0, x1):
        draw.line([(tx, y + p(15)), (tx, y + p(20))], fill=pal[INK], width=1)

    small = font(SANS, fs(18), 500)
    if nl.chapter_idx >= 0:
        chapter = chapter_line(nl.chapter_title, nl.chapter_idx, nl.chapter_count)
        draw.text((x0, p(CHAPTER_Y)), ellipsize(chapter, small, col_w), font=small, fill=pal[INK], anchor="lm")
    if nl.finished:
        when = _finished_date(nl.finished_at)
        status = f"Finished {when}" if when else "Finished"
    else:
        status = f"{time_left(nl.duration, nl.current_time)} left  ·  {int(nl.pct * 100)}%"
    draw.text((x0, p(LEFT_Y)), ellipsize(status, small, col_w), font=small, fill=pal[INK], anchor="lm")
