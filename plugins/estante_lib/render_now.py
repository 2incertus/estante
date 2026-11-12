"""The Now listening column: label, cover, the book, its progress and the footer."""
import math
from dataclasses import dataclass
from datetime import datetime

from PIL import Image, ImageDraw, ImageOps

from .model import NowListening, chapter_line, series_line, time_left
from .render_common import draw_footer
from .text import SANS, SERIF, ellipsize, fit_text, font

LABEL_NOW = "NOW LISTENING"
LABEL_LAST = "LAST LISTENED"
LABEL_NONE = "NOTHING PLAYING"
NOTHING_YET = "Nothing played yet"
MAX_TICKS = 96

BASE_W, BASE_H = 620, 1080
X0, X1 = 50, 570
LABEL_Y = 56
COVER_X, COVER_Y, COVER_BOX = 50, 90, 520
TITLE_BASELINE = 650
BAR_Y, BAR_H = 900, 14
TICK_TOP, TICK_LEN = 3, 6
CHAPTER_Y, LEFT_Y = 948, 980
FOOTER_RULE_Y, FOOTER_TEXT_Y = 1024, 1052
# Text below the title must end before this line so it never touches the bar.
TEXT_FLOOR = 880


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


def _paste_cover(canvas, cover, box, origin):
    art = ImageOps.contain(cover.convert("RGB"), (box, box), Image.Resampling.LANCZOS)
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


def render_now_column(view, theme, size=(BASE_W, BASE_H), scale=1.0):
    """The left column of the screen as an RGB image of `size`, coordinates scaled by `scale`."""
    w, h = size
    s = scale

    def p(v):
        return round(v * s)

    def fs(v):
        return max(1, round(v * s))

    canvas = Image.new("RGB", (w, h), theme.bg)
    draw = ImageDraw.Draw(canvas)
    nl = view.nl
    x0, x1 = p(X0), p(X1)

    if nl is None:
        label = LABEL_NONE
    elif nl.finished:
        label = LABEL_LAST
    else:
        label = LABEL_NOW
    draw.text((x0, p(LABEL_Y)), label, font=font(SANS, fs(18), 600), fill=theme.accent, anchor="lm")

    if nl is None:
        draw.text(
            (x0, p(TITLE_BASELINE)), NOTHING_YET, font=font(SERIF, fs(36)), fill=theme.ink, anchor="ls"
        )
    else:
        if view.cover is not None:
            _paste_cover(canvas, view.cover, p(COVER_BOX), (p(COVER_X), p(COVER_Y)))
        _draw_text_rows(draw, theme, nl, x0, x1, p, fs)
        _draw_progress(draw, theme, nl, x0, x1, p, fs)

    footer_left = f"{view.finished_this_year} finished in {view.year}"
    draw_footer(
        draw, theme, x0, x1, p(FOOTER_RULE_Y), p(FOOTER_TEXT_Y), footer_left, view.badge, scale=s, mark=False
    )
    return canvas


def _draw_text_rows(draw, theme, nl, x0, x1, p, fs):
    col_w = x1 - x0
    lines, title_font = fit_text(nl.title, SERIF, fs(48), fs(30), col_w, 2, 500)
    baseline = p(TITLE_BASELINE)
    gap = round(title_font.size * 1.05)
    for i, line in enumerate(lines):
        draw.text((x0, baseline + i * gap), line, font=title_font, fill=theme.ink, anchor="ls")
    title_bottom = baseline + (len(lines) - 1) * gap + title_font.getmetrics()[1]

    rows = []
    if nl.author:
        rows.append((nl.author, font(SERIF, fs(28)), theme.ink))
    if nl.narrator:
        rows.append((f"read by {nl.narrator}", font(SERIF, fs(22)), theme.ink))
    series = series_line(nl.series)
    if series:
        rows.append((series, font(SERIF, fs(22)), theme.series))

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


def _draw_progress(draw, theme, nl, x0, x1, p, fs):
    col_w = x1 - x0
    y, bh = p(BAR_Y), max(4, p(BAR_H))
    edge = max(1, p(2))
    fill_w = col_w if nl.finished else max(p(4), int(col_w * nl.pct))
    draw.rectangle([x0, y, x0 + fill_w - 1, y + bh - 1], fill=theme.accent)
    draw.rectangle([x0, y, x1 - 1, y + bh - 1], outline=theme.ink, width=edge)
    tick_y0 = y + bh + p(TICK_TOP)
    for tx in _ticks(nl, x0, x1):
        draw.line([(tx, tick_y0), (tx, tick_y0 + p(TICK_LEN))], fill=theme.muted, width=1)

    small = font(SANS, fs(20), 500)
    if nl.chapter_idx >= 0:
        chapter = chapter_line(nl.chapter_title, nl.chapter_idx, nl.chapter_count)
        draw.text((x0, p(CHAPTER_Y)), ellipsize(chapter, small, col_w), font=small, fill=theme.ink, anchor="lm")
    if nl.finished:
        when = _finished_date(nl.finished_at)
        status = f"Finished {when}" if when else "Finished"
    else:
        status = f"{time_left(nl.duration, nl.current_time)} left  ·  {int(nl.pct * 100)}%"
    draw.text((x0, p(LEFT_Y)), ellipsize(status, small, col_w), font=small, fill=theme.ink, anchor="lm")
