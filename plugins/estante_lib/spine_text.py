"""How a title is set on a spine: the size ladder, wrapping to two lines and fast width checks.

No images here: render_spines draws what spine_layout decides, and packing uses the widths.
"""
import functools
from dataclasses import dataclass

from .text import ELLIPSIS, SERIF, font, text_width

# a title is one line at TITLE_SIZE (on spines NARROW_W or wider), else one line at
# TITLE_SIZE_SMALL, else two lines at TITLE_SIZE_SMALL on a spine widened to hold them
TITLE_SIZE, TITLE_SIZE_SMALL, NARROW_W = 30, 26, 42
TITLE_ROOM = 70
TITLE_PAD = 8
# the space between the descender of the first line and the caps of the second
LINE_GAP = 5


def _title_font(size):
    return font(SERIF, size, 500)


def _ink_extent(fnt):
    """(top, bottom) of the font's ink about the baseline: cap or ascender top to descender."""
    return fnt.getbbox("Hgy", anchor="ls")[1::2]


def _line_pitch():
    top, bottom = _ink_extent(_title_font(TITLE_SIZE_SMALL))
    return bottom - top + LINE_GAP


# baseline to baseline for two lines at TITLE_SIZE_SMALL, about 30 px
LINE_W = _line_pitch()


@functools.lru_cache(maxsize=4096)
def _advance(fnt, ch):
    return fnt.getlength(ch)


def _approx_width(fnt, text):
    """The sum of the glyphs' advances, without kerning: cheap once each glyph is known, and
    within a few px of the real width (each real measurement costs about 0.13 ms here)."""
    return sum(_advance(fnt, ch) for ch in text)


# how far the estimate may be from the room before it alone decides
FIT_SLACK = 0.08


def _fits(text, fnt, room):
    estimate = _approx_width(fnt, text)
    if estimate > room * (1 + FIT_SLACK):
        return False
    if estimate < room * (1 - FIT_SLACK):
        return True
    return text_width(fnt, text) <= room


def _fast_ellipsize(text, fnt, max_width):
    """text.ellipsize's result, found from the glyph advances and then checked against the real
    width: a 120-character title costs two or three width measurements instead of one per
    character (they are slow on the Pi)."""
    if _fits(text, fnt, max_width):
        return text

    def shown(cut):
        return text[:cut].rstrip(" ,;:-") + ELLIPSIS

    def fits(cut):
        return text_width(fnt, shown(cut)) <= max_width

    budget = max_width - _advance(fnt, ELLIPSIS)
    lo, run = 0, 0.0
    for ch in text[:-1]:
        run += _advance(fnt, ch)
        if run > budget:
            break
        lo += 1
    if lo and fits(lo):
        while lo + 1 < len(text) and fits(lo + 1):
            lo += 1
    else:
        while lo > 0 and not fits(lo):
            lo -= 1
    return shown(lo) if lo > 0 else ELLIPSIS


@dataclass(frozen=True)
class SpineLayout:
    size: int
    lines: tuple
    width: int


def _two_lines(words, fnt, room):
    """Words filled into a first line of at most `room` px (a word longer than that is cut
    with the ellipsis), the rest on a second line, ellipsized when it does not fit."""
    first = words[0]
    rest = words[1:]
    if not _fits(first, fnt, room):
        first = _fast_ellipsize(first, fnt, room)
    else:
        while rest and _fits(f"{first} {rest[0]}", fnt, room):
            first = f"{first} {rest[0]}"
            rest = rest[1:]
    if not rest:
        return (first,)
    return first, _fast_ellipsize(" ".join(rest), fnt, room)


@functools.lru_cache(maxsize=2048)
def spine_layout(title, base_width, height):
    """How a title sits on a spine `base_width` x `height`, in this order: one line at
    TITLE_SIZE if it fits the room (the height less TITLE_ROOM) and the spine is NARROW_W or
    wider; one line at TITLE_SIZE_SMALL; two lines at TITLE_SIZE_SMALL wrapped between words,
    the second ellipsized when the title is longer still. Two lines widen the spine to
    2 * LINE_W + 2 * TITLE_PAD. Kept by title and size: packing asks for every book."""
    words = (title or "").split()
    text = " ".join(words)
    room = height - TITLE_ROOM
    small = _title_font(TITLE_SIZE_SMALL)
    # what does not fit at the smaller size does not fit at the larger: measure that first
    one_small = _fits(text, small, room)
    if one_small and base_width >= NARROW_W and _fits(text, _title_font(TITLE_SIZE), room):
        return SpineLayout(TITLE_SIZE, (text,), base_width)
    if one_small or len(words) < 2:
        return SpineLayout(TITLE_SIZE_SMALL, (_fast_ellipsize(text, small, room),), base_width)
    lines = _two_lines(words, small, room)
    if len(lines) == 1:
        return SpineLayout(TITLE_SIZE_SMALL, lines, base_width)
    return SpineLayout(TITLE_SIZE_SMALL, lines, max(base_width, 2 * LINE_W + 2 * TITLE_PAD))


def clear_caches():
    """Forget every title layout and glyph advance (a cold render measures everything again)."""
    spine_layout.cache_clear()
    _advance.cache_clear()
