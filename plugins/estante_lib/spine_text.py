"""How a title is set on a spine: the size ladder, wrapping to two lines and fast width checks.

No images here: render_spines draws what spine_layout decides, and packing uses the widths.
"""
import functools
import re
from dataclasses import dataclass

from .text import ELLIPSIS, SERIF, font, text_width

# the ladder is in spine_layout: TITLE_SIZE on spines NARROW_W or wider, then TITLE_SIZE_SMALL
# on one line or two, then two lines at TITLE_SIZE_TINY
TITLE_SIZE, TITLE_SIZE_SMALL, TITLE_SIZE_TINY, NARROW_W = 30, 26, 22, 42
TITLE_ROOM = 70
TITLE_PAD = 8
# the space between the descender of the first line and the caps of the second
LINE_GAP = 5


def _title_font(size):
    return font(SERIF, size, 500)


def _ink_extent(fnt):
    """(top, bottom) of the font's ink about the baseline: cap or ascender top to descender."""
    return fnt.getbbox("Hgy", anchor="ls")[1::2]


def line_pitch(size):
    """Baseline to baseline for two lines at `size`: the ink's height and LINE_GAP."""
    top, bottom = _ink_extent(_title_font(size))
    return bottom - top + LINE_GAP


# baseline to baseline for two lines at TITLE_SIZE_SMALL, about 30 px; it sets the width of
# every two-line spine, at either size
LINE_W = line_pitch(TITLE_SIZE_SMALL)
TWO_LINE_W = 2 * LINE_W + 2 * TITLE_PAD


@functools.lru_cache(maxsize=4096)
def _advance(fnt, ch):
    return fnt.getlength(ch)


def _approx_width(fnt, text):
    """The sum of the glyphs' advances, without kerning: cheap once each glyph is known, and
    within a few px of the real width (each real measurement costs about 0.13 ms here)."""
    return sum(_advance(fnt, ch) for ch in text)


# the estimate alone decides below LOW and above HIGH times the room; kerning only ever makes
# it too high (by up to about 12 %), rounding makes it too low by under 3 %
FIT_LOW, FIT_HIGH = 0.92, 1.15


def _fits(text, fnt, room):
    estimate = _approx_width(fnt, text)
    if estimate <= room * FIT_LOW:
        return True
    if estimate >= room * FIT_HIGH:
        return False
    return text_width(fnt, text) <= room


def _fast_ellipsize(text, fnt, max_width):
    """text.ellipsize's result, found from the glyph advances and then checked against the real
    width: a 120-character title costs a few width measurements instead of one per character
    (they are slow on the Pi). The whole text is measured for real once, so text that fits is
    never cut."""
    if _approx_width(fnt, text) <= max_width * FIT_LOW or text_width(fnt, text) <= max_width:
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


# title cleaning

_EDITION_RE = re.compile(
    r"\s*[(\[]\s*(?:unabridged|abridged|full-cast edition|dramatized adaptation)\s*[)\]]\s*$", re.I
)
_TRAILING_PAREN_RE = re.compile(r"\s*\(([^()]*)\)\s*$")
_BOOK_NUMBER_RE = re.compile(r"\b(?:book|vol\.|volume)\s*\d+", re.I)
_LIFESPAN_RE = re.compile(r"\s*\(\s*\d{3,4}\s*-\s*\d{3,4}\s*\)\s*$")
_INVERTED_ARTICLE_RE = re.compile(
    r"^(.+?),\s*(The|A|An|O|Os|As|Um|Uma|Le|La|Les|Der|Die|Das)$", re.I
)
_SERIES_NUMBER_RE = re.compile(r"\s*#\s*[\d.]+\s*$")


def _collapse(text):
    return " ".join((text or "").split())


def _series_names(series):
    """The series names in an Audiobookshelf seriesName, without their "#n"."""
    names = []
    for part in re.split(r"(?<=[0-9]),\s+", series or ""):
        name = _collapse(_SERIES_NUMBER_RE.sub("", part)).lower()
        if name:
            names.append(name)
    return names


def _names_the_series(inner, series_names):
    low = _collapse(inner).lower()
    return bool(_BOOK_NUMBER_RE.search(low)) or any(low.startswith(name) for name in series_names)


@functools.lru_cache(maxsize=2048)
def shelf_title(title, author="", series=""):
    """The title as a spine shows it, scraped noise removed: a trailing edition tag
    ("(Unabridged)", "[Unabridged]", "(Full-Cast Edition)", "(Dramatized Adaptation)",
    "(Abridged)"); a trailing parenthetical naming the series (a book or volume number, or the
    book's series name); a LibriVox " by <the author> (1839 - 1908)"; and an inverted article
    ("Alienista, O" -> "O Alienista")."""
    text = _collapse(title)
    series_names = _series_names(series)
    while True:
        before = text
        text = _EDITION_RE.sub("", text)
        m = _TRAILING_PAREN_RE.search(text)
        if m and m.start() > 0 and _names_the_series(m.group(1), series_names):
            text = text[: m.start()]
        text = _collapse(text)
        if text == before:
            break
    who = _collapse(author).lower()
    if who:
        bare = _LIFESPAN_RE.sub("", text)
        suffix = f" by {who}"
        if bare.lower().endswith(suffix) and len(bare) > len(suffix):
            text = bare[: -len(suffix)]
    m = _INVERTED_ARTICLE_RE.match(text)
    if m:
        text = f"{m.group(2)} {m.group(1)}"
    return _collapse(text)


# the ladder

@dataclass(frozen=True)
class SpineLayout:
    """`lines` at `size` px within `room` px of the spine's length on a spine `width` wide;
    `author` says whether the surname may go at the top (one-line titles that leave it room)."""

    size: int
    lines: tuple
    width: int
    author: bool = False
    room: int = 0


_SEPARATORS = (": ", " - ", " | ")
_LINE_END_RE = re.compile(r"(?:\s[-|–]|[:;,])$")
_LINE_START_RE = re.compile(r"^[-|–]\s")


def _tokens(text, fnt, room):
    """(joiner, piece) pairs: the words, and a word longer than `room` broken after each of
    its hyphens ("Slaughterhouse-" "Five") so it can wrap there."""
    out = []
    for word in text.split():
        pieces = [word]
        if "-" in word.strip("-") and not _fits(word, fnt, room):
            parts = word.split("-")
            pieces = [p + "-" for p in parts[:-1]] + [parts[-1]]
            pieces = [p for p in pieces if p]
        for n, piece in enumerate(pieces):
            out.append(("" if n else " ", piece))
    return out


def _two_lines(text, fnt, room):
    """(lines, cut): the text filled into a first line of at most `room` px, the rest on a
    second line. A piece longer than the room is cut with the ellipsis, and so is a second
    line that does not fit; `cut` says whether anything was. Line 1 never ends with a
    separator and line 2 never starts with one."""
    tokens = _tokens(text, fnt, room)
    if not tokens:
        return ("",), False
    first = tokens[0][1]
    rest = tokens[1:]
    cut = False
    if not _fits(first, fnt, room):
        first = _fast_ellipsize(first, fnt, room)
        cut = True
    else:
        while rest and _fits(first + rest[0][0] + rest[0][1], fnt, room):
            first = first + rest[0][0] + rest[0][1]
            rest = rest[1:]
    first = _LINE_END_RE.sub("", first).rstrip()
    if not rest:
        return (first,), cut
    second = _LINE_START_RE.sub("", "".join(j + p for j, p in rest).strip())
    shown = _fast_ellipsize(second, fnt, room)
    return (first, shown), cut or shown != second


def _drop_subtitle(text):
    """The text before its last separator (": ", " - ", " | "), or None when it has none."""
    at = max(text.rfind(sep) for sep in _SEPARATORS)
    return text[:at].rstrip() if at > 0 else None


def _one_or_two(text, base_width, room_a, room_f):
    """Ladder steps 1 to 4 for `text`, or None when two lines at TITLE_SIZE_SMALL still cut it."""
    small = _title_font(TITLE_SIZE_SMALL)
    # what does not fit at the smaller size does not fit at the larger: measure that first
    if _fits(text, small, room_a):
        if base_width >= NARROW_W and _fits(text, _title_font(TITLE_SIZE), room_a):
            return SpineLayout(TITLE_SIZE, (text,), base_width, True, room_a)
        return SpineLayout(TITLE_SIZE_SMALL, (text,), base_width, True, room_a)
    if _fits(text, small, room_f):
        return SpineLayout(TITLE_SIZE_SMALL, (text,), base_width, False, room_f)
    lines, cut = _two_lines(text, small, room_f)
    if cut:
        return None
    return SpineLayout(TITLE_SIZE_SMALL, lines, max(base_width, TWO_LINE_W), False, room_f)


@functools.lru_cache(maxsize=2048)
def spine_layout(title, base_width, height):
    """How a (cleaned, see shelf_title) title sits on a spine `base_width` x `height`. Two
    rooms along the spine: room_a, the height less TITLE_ROOM, leaves the surname its place;
    room_f, the height less 2 * TITLE_PAD, is the whole spine. First that fits wins:
    1. one line at TITLE_SIZE in room_a, on spines NARROW_W or wider (surname allowed);
    2. one line at TITLE_SIZE_SMALL in room_a (surname allowed);
    3. one line at TITLE_SIZE_SMALL in room_f;
    4. two lines at TITLE_SIZE_SMALL in room_f, a word too long for a line broken at a hyphen;
    5. the same on the title with its last subtitle dropped (after ": ", " - " or " | "),
       again while there is one;
    6. two lines at TITLE_SIZE_TINY in room_f, on the shortest of those titles;
    7. that, its last line cut with the ellipsis.
    Two lines widen the spine to TWO_LINE_W. Kept by title and size: packing asks for every
    book."""
    text = _collapse(title)
    room_a = height - TITLE_ROOM
    room_f = height - 2 * TITLE_PAD
    shortest = text
    while shortest is not None:
        layout = _one_or_two(shortest, base_width, room_a, room_f)
        if layout is not None:
            return layout
        shorter = _drop_subtitle(shortest)
        if shorter is None:
            break
        shortest = shorter
    lines, _ = _two_lines(shortest, _title_font(TITLE_SIZE_TINY), room_f)
    width = max(base_width, TWO_LINE_W) if len(lines) > 1 else base_width
    return SpineLayout(TITLE_SIZE_TINY, lines, width, False, room_f)


def clear_caches():
    """Forget every title layout and glyph advance (a cold render measures everything again)."""
    spine_layout.cache_clear()
    shelf_title.cache_clear()
    _advance.cache_clear()
