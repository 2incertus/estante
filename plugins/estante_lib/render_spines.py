"""The shelf of spines: one library as books on wooden boards, a section of four boards at a time.

Unread books stand as spines in their cover's dominant colour with the title reading bottom
to top; finished and in-progress books face out with their covers, the one in progress with a
red ribbon and a progress line. Books come in runs (a series, or one author's other books),
each with a label on the board under it; a run that fits a board is never split.
"""
import collections
import colorsys
import functools
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageOps

from .model import STATUS_LISTENING, STATUS_READ, STATUS_UNREAD, ShelfBook, surname
from .render_common import draw_footer
from .text import ELLIPSIS, SANS, SERIF, ellipsize, font, text_width

BASE_W, BASE_H = 1230, 1080
TITLE_Y, COUNTS_Y, SUBTITLE_Y, RULE_Y = 48, 36, 66, 86
FOOTER_RULE_Y, FOOTER_TEXT_Y = 1044, 1064

# boards: four per section at a 230 px pitch, books stand on the board's top edge
BOARD_Y = (110, 340, 570, 800)
BOARDS = len(BOARD_Y)
USABLE_H = 206
BOARD_H = 14
BOARD_COLOUR = (58, 48, 40)
BOARD_EDGE = (96, 80, 66)
# the boards are the same wood on both themes, so their labels share one colour: a muted tan
LABEL_COLOUR = (176, 160, 138)
LABEL_SIZE = 11
LABEL_TRACKING = 1
LABEL_MIN = 40

GAP = 2
RUN_GAP = 14
# a run wider than a whole board starts on a fresh board unless this much room is left
WRAP_MIN = 200
# how many of the following runs may fill the hole a run leaves when it moves to the next board
LOOKAHEAD = 6

SPINE_MIN_W, SPINE_MAX_W = 22, 52
SPINE_MIN_H, SPINE_MAX_H = 178, 206
TITLE_SIZE, TITLE_SIZE_NARROW, NARROW_W = 22, 18, 30
AUTHOR_SIZE, AUTHOR_MIN_W = 11, 34
TITLE_ROOM = 60
TITLE_PAD = 8
AUTHOR_PAD = 6
AUTHOR_GAP = 10
INK_LIGHT = (245, 242, 232)
INK_DARK = (28, 26, 24)

FACE_W = 150
COVER = 138
PLINTH = (10, 10, 12)
RIBBON_W, RIBBON_H, RIBBON_NOTCH = 12, 60, 8

KIND_SPINE = "spine"
KIND_FACE = "face"
SEP = "  ·  "


@dataclass(frozen=True)
class Piece:
    book: ShelfBook
    kind: str
    width: int
    height: int


@dataclass
class ShelfSectionView:
    """One library's shelf; `section` is 0-based and wraps around the sections there are.

    `badge` is carried for the composition; the shelf footer never repeats it (the Now
    listening column shows the message and QUIET).
    """

    title: str
    subtitle: str
    books: "list[ShelfBook]"
    runs: list
    hours: "dict[str, float]"
    pct: "dict[str, float]"
    covers: "dict[str, Image.Image]"
    colours: "dict[str, tuple]"
    counts: dict
    section: int = 0
    shelf_minutes: int = 2
    badge: dict = field(default_factory=dict)


# pieces and packing


def spine_height(item_id):
    """178..206 from the item id, the same on every machine."""
    digest = hashlib.sha1(item_id.encode("utf-8")).digest()
    return SPINE_MIN_H + int.from_bytes(digest[:4], "big") % (SPINE_MAX_H - SPINE_MIN_H + 1)


def spine_width(hours):
    return int(max(SPINE_MIN_W, min(SPINE_MAX_W, 18 + (hours or 0) * 0.9)))


def piece_for(book, hours):
    if book.status == STATUS_UNREAD:
        return Piece(book, KIND_SPINE, spine_width(hours.get(book.item_id)), spine_height(book.item_id))
    return Piece(book, KIND_FACE, FACE_W, USABLE_H)


def _row_width(pieces, gap):
    return sum(p.width for p in pieces) + gap * max(0, len(pieces) - 1)


def _slot(pieces, gap=GAP):
    """Room a placed run takes on its board: its books, or the label's minimum."""
    return max(_row_width(pieces, gap), LABEL_MIN)


def plan_sections(runs, hours, width=BASE_W, boards=BOARDS, gap=GAP, run_gap=RUN_GAP):
    """Sections -> boards -> [(run, pieces)], packed left to right, top to bottom.

    A run that fits on the rest of the board goes there. One that does not moves to the next
    board, but first the hole it leaves is offered to the next few runs (LOOKAHEAD) that fit
    in it, so a long series never leaves a board almost empty. Only a run wider than a whole
    board is split, piece by piece, over as many boards as it needs. An empty shelf is one
    empty section.
    """
    queue = [(run, [piece_for(b, hours) for b in run.books]) for run in runs if run.books]
    filled = []
    board = []
    x = 0

    def start_of():
        return x + run_gap if board else 0

    def place(run, pieces):
        nonlocal x
        x = start_of() + _slot(pieces, gap)
        board.append((run, pieces))

    def close():
        nonlocal board, x
        filled.append(board)
        board, x = [], 0

    def backfill(i):
        j = i + 1
        while j < min(len(queue), i + 1 + LOOKAHEAD):
            run, pieces = queue[j]
            if start_of() + _slot(pieces, gap) <= width:
                place(run, pieces)
                del queue[j]
            else:
                j += 1

    i = 0
    while i < len(queue):
        run, pieces = queue[i]
        slot = _slot(pieces, gap)
        if start_of() + slot <= width:
            place(run, pieces)
        elif slot <= width:
            backfill(i)
            close()
            place(run, pieces)
        else:
            if board and width - start_of() < WRAP_MIN:
                backfill(i)
                close()
            start = start_of()
            frag, fx = [], start
            for piece in pieces:
                need = piece.width + (gap if frag else 0)
                if frag and fx + need > width:
                    board.append((run, frag))
                    close()
                    frag, fx, start, need = [], 0, 0, piece.width
                frag.append(piece)
                fx += need
            board.append((run, frag))
            x = start + _slot(frag, gap)
        i += 1
    if board:
        filled.append(board)
    if not filled:
        return [[]]
    return [filled[k:k + boards] for k in range(0, len(filled), boards)]


_PLANS = collections.OrderedDict()
_PLANS_KEEP = 4


def _fingerprint(runs, hours):
    h = hashlib.sha1()
    for run in runs:
        h.update(f"{run.key}\0{run.label}\0".encode("utf-8", "replace"))
        for b in run.books:
            line = f"{b.item_id}\0{b.status}\0{b.title}\0{b.author}\0{hours.get(b.item_id, 0):.4f}\0"
            h.update(line.encode("utf-8", "replace"))
    return h.hexdigest()


def planned(runs, hours):
    """plan_sections, kept in memory by a hash of the runs and hours (the Pi re-renders often)."""
    key = _fingerprint(runs, hours)
    if key in _PLANS:
        _PLANS.move_to_end(key)
        return _PLANS[key]
    plan = plan_sections(runs, hours)
    _PLANS[key] = plan
    while len(_PLANS) > _PLANS_KEEP:
        _PLANS.popitem(last=False)
    return plan


def section_of(runs, item_id, hours):
    """The 0-based section that holds `item_id`, or None when it is not on the shelf."""
    for s, section in enumerate(planned(runs, hours)):
        for board in section:
            for _, pieces in board:
                if any(p.book.item_id == item_id for p in pieces):
                    return s
    return None


def section_count(runs, hours):
    return len(planned(runs, hours))


def heaviest_section(runs, hours):
    """(0-based index, spine count) of the section with the most spines: the slowest to draw."""
    best = (0, 0)
    for index, section in enumerate(planned(runs, hours)):
        spines = sum(1 for board in section for _, pieces in board for p in pieces if p.kind == KIND_SPINE)
        if spines > best[1]:
            best = (index, spines)
    return best


# colours


def _luma(rgb):
    r, g, b = rgb
    return (r * 299 + g * 587 + b * 114) / 1000


def spine_colour(cover, title):
    """The cover's dominant colour, saturation-weighted, near-white and near-black played down.

    Without a cover, a muted colour from the title's hash.
    """
    if cover is None:
        digest = hashlib.sha1((title or "").encode("utf-8")).digest()
        hue = digest[0] / 255
        sat = 0.25 + digest[1] / 255 * 0.2
        val = 0.38 + digest[2] / 255 * 0.2
        return tuple(round(c * 255) for c in colorsys.hsv_to_rgb(hue, sat, val))
    small = cover.convert("RGB").resize((24, 24), Image.Resampling.LANCZOS)
    quant = small.quantize(colors=6, method=Image.Quantize.MEDIANCUT).convert("RGB")
    best, score = None, -1.0
    for n, rgb in quant.getcolors(24 * 24):
        hi, lo = max(rgb), min(rgb)
        sat = (hi - lo) / (hi + 1e-6)
        lum = _luma(rgb)
        s = n * (0.4 + sat) * (1.0 if 40 < lum < 215 else 0.35)
        if s > score:
            score, best = s, rgb
    return tuple(best)


class ColourCache:
    """Spine colours by item id, computed once per cover file and kept in a JSON file."""

    def __init__(self, path):
        self.path = path
        self.dirty = False
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
        except (OSError, ValueError):
            loaded = {}
        self._data = loaded if isinstance(loaded, dict) else {}

    def get(self, item_id, updated_at, cover_path):
        """The colour for this cover version, or None when there is no readable cover."""
        entry = self._data.get(item_id)
        if isinstance(entry, dict) and entry.get("updated_at") == updated_at:
            rgb = entry.get("rgb")
            if isinstance(rgb, list) and len(rgb) == 3:
                return tuple(rgb)
        if not cover_path:
            return None
        try:
            with Image.open(cover_path) as img:
                rgb = spine_colour(img.convert("RGB"), "")
        except (OSError, ValueError, Image.DecompressionBombError):
            return None
        self._data[item_id] = {"updated_at": updated_at, "rgb": list(rgb)}
        self.dirty = True
        return rgb

    def save(self):
        """Write the file when something new was computed (atomic)."""
        if not self.dirty:
            return
        folder = os.path.dirname(self.path) or "."
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".spines-", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, separators=(",", ":"), sort_keys=True)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        self.dirty = False


# drawing


def _text_colour(rgb):
    return INK_LIGHT if _luma(rgb) < 140 else INK_DARK


def _shade(rgb, delta):
    return tuple(max(0, min(255, c + delta)) for c in rgb)


def _title_font(piece):
    return font(SERIF, TITLE_SIZE if piece.width >= NARROW_W else TITLE_SIZE_NARROW, 500)


def _fast_ellipsize(text, fnt, max_width):
    """text.ellipsize's result, found by bisection: a 120-character title costs a handful of
    width measurements instead of one per character (they are slow on the Pi)."""
    if text_width(fnt, text) <= max_width:
        return text

    def fits(cut):
        return text_width(fnt, text[:cut].rstrip(" ,;:-") + ELLIPSIS) <= max_width

    lo, hi = 0, len(text) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fits(mid):
            lo = mid
        else:
            hi = mid - 1
    while lo > 0 and not fits(lo):
        lo -= 1
    return text[:lo].rstrip(" ,;:-") + ELLIPSIS if lo > 0 else ELLIPSIS


def spine_title(piece):
    """The title as the spine shows it: ellipsized to the spine height minus 60 px."""
    title = " ".join((piece.book.title or "").split())
    return _fast_ellipsize(title, _title_font(piece), piece.height - TITLE_ROOM)


_SPINES = collections.OrderedDict()
_SPINES_KEEP = 600


def clear_caches():
    """Forget every drawn spine, label and plan (tests and the self-test time a cold render)."""
    _SPINES.clear()
    _PLANS.clear()
    _label_mask.cache_clear()


def draw_spine(piece, colour, theme):
    """A spine of `piece`'s size: paper edge on top, shaded right edge, the title bottom to top,
    the author's surname at the top when the spine is 34 px or wider and the whole surname
    fits beside the title."""
    key = (piece, tuple(colour), theme.name)
    cached = _SPINES.get(key)
    if cached is not None:
        _SPINES.move_to_end(key)
        return cached
    w, h = piece.width, piece.height
    img = Image.new("RGB", (w, h), colour)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w - 1, 3], fill=_shade(colour, 40))
    d.rectangle([w - 3, 0, w - 1, h - 1], fill=_shade(colour, -35))
    ink = _text_colour(colour)
    # drawn on a horizontal strip the length of the spine, then turned: left becomes bottom
    strip = Image.new("RGBA", (h, w), (0, 0, 0, 0))
    sd = ImageDraw.Draw(strip)
    title_font = _title_font(piece)
    title = spine_title(piece)
    # centre the ink, not the font's line box: Garamond's descender is deeper than its caps are tall
    top, bottom = title_font.getbbox("Hgy", anchor="ls")[1::2]
    baseline = round(w / 2 - (top + bottom) / 2)
    sd.text((TITLE_PAD, baseline), title, font=title_font, fill=ink, anchor="ls")
    author = surname(piece.book.author)
    if author and w >= AUTHOR_MIN_W:
        author_font = font(SANS, AUTHOR_SIZE, 500)
        room = h - AUTHOR_PAD - (TITLE_PAD + text_width(title_font, title)) - AUTHOR_GAP
        # the whole surname or nothing: a clipped "Quain..." reads as noise from the desk
        if text_width(author_font, author) <= room:
            sd.text((h - AUTHOR_PAD, w // 2), author, font=author_font, fill=ink, anchor="rm")
    turned = strip.transpose(Image.Transpose.ROTATE_90)
    img.paste(turned, (0, 0), turned)
    _SPINES[key] = img
    while len(_SPINES) > _SPINES_KEEP:
        _SPINES.popitem(last=False)
    return img


def draw_face(piece, cover, pct, theme):
    """A face-out book: the cover fitted 138x138 on a dark plinth, bottom aligned; in progress
    (pct not None) adds a notched red ribbon at the cover's top right and a progress line."""
    w, h = piece.width, piece.height
    img = Image.new("RGB", (w, h), theme.bg)
    d = ImageDraw.Draw(img)
    left, top = (w - COVER) // 2, h - 8 - COVER
    d.rectangle([left - 1, top - 2, left + COVER, h - 8], fill=PLINTH)
    if cover is not None:
        art = ImageOps.fit(cover.convert("RGB"), (COVER, COVER), Image.Resampling.LANCZOS)
    else:
        art = Image.new("RGB", (COVER, COVER), spine_colour(None, piece.book.title))
    img.paste(art, (left, top))
    if pct is not None:
        rx = left + COVER - RIBBON_W - 8
        d.polygon(
            [
                (rx, top),
                (rx + RIBBON_W - 1, top),
                (rx + RIBBON_W - 1, top + RIBBON_H),
                (rx + RIBBON_W // 2, top + RIBBON_H - RIBBON_NOTCH),
                (rx, top + RIBBON_H),
            ],
            fill=theme.accent,
        )
        filled = round(COVER * min(1.0, max(0.0, pct)))
        if filled:
            d.rectangle([left, h - 6, left + filled - 1, h - 3], fill=theme.accent)
    return img


def _label_font():
    return font(SANS, LABEL_SIZE, 600)


@functools.lru_cache(maxsize=256)
def _char_width(ch):
    return _label_font().getlength(ch)


@functools.lru_cache(maxsize=2048)
def _fit_label(text, room):
    """`text` letter-spaced in at most `room` px, ellipsized; "" when not even one letter fits."""
    prefix = [0.0]
    for ch in text:
        prefix.append(prefix[-1] + _char_width(ch))

    def spaced(n, extra=0.0, extra_chars=0):
        return prefix[n] + extra + LABEL_TRACKING * max(0, n + extra_chars - 1)

    if spaced(len(text)) <= room:
        return text
    ellipsis = _char_width(ELLIPSIS)
    for cut in range(len(text) - 1, 0, -1):
        kept = len(text[:cut].rstrip(" \u00b7,;:-"))
        if kept and spaced(kept, ellipsis, 1) <= room:
            return text[:kept] + ELLIPSIS
    return ""


@functools.lru_cache(maxsize=1024)
def _label_mask(text, room):
    """The fitted label as a greyscale mask, letter by letter, kept for the next render."""
    shown = _fit_label(text, room)
    if not shown:
        return None
    fnt = _label_font()
    mask = Image.new("L", (max(1, room), BOARD_H), 0)
    d = ImageDraw.Draw(mask)
    x = 0.0
    for ch in shown:
        d.text((x, (BOARD_H - 2) // 2), ch, font=fnt, fill=255, anchor="lm")
        x += _char_width(ch) + LABEL_TRACKING
    return mask


def _draw_label(canvas, x, y, text, room):
    """The label with its left edge at x and its middle on y, in the board's label colour."""
    mask = _label_mask(text, max(0, round(room)))
    if mask is not None:
        top = y - (BOARD_H - 2) // 2
        canvas.paste(LABEL_COLOUR, (x, top, x + mask.width, top + mask.height), mask)


def _header(draw, theme, view, subtitle, w):
    counts = view.counts
    counts_text = (
        f"{counts.get(STATUS_READ, 0)} read{SEP}{counts.get(STATUS_LISTENING, 0)} listening"
        f"{SEP}{counts.get(STATUS_UNREAD, 0)} to go"
    )
    counts_font = font(SANS, 20, 500)
    sub_font = font(SANS, 16)
    title_font = font(SERIF, 44, 500)
    draw.text((w, COUNTS_Y), counts_text, font=counts_font, fill=theme.ink, anchor="rm")
    draw.text((w, SUBTITLE_Y), subtitle, font=sub_font, fill=theme.series, anchor="rm")
    widest = max(text_width(counts_font, counts_text), text_width(sub_font, subtitle))
    draw.text(
        (0, TITLE_Y), ellipsize(view.title, title_font, w - widest - 32), font=title_font, fill=theme.ink,
        anchor="lm",
    )
    draw.rectangle([0, RULE_Y, w - 1, RULE_Y + 1], fill=theme.ink)


def _footer_text(count, total, first, last, sections, minutes):
    parts = [f"{count} of {total} on this shelf"]
    if count:
        parts.append(f"{first} to {last}")
    if sections > 1:
        parts.append(f"next shelf in {minutes} min")
    return SEP.join(parts)


def render_section(view, theme, size=(BASE_W, BASE_H), scale=1.0):
    """(image, info) for section `view.section` of the shelf; info has sections, section
    (0-based, wrapped), first_author, last_author, count, subtitle and footer."""
    plan = planned(view.runs, view.hours)
    sections = len(plan)
    index = view.section % sections
    boards = plan[index]
    w, h = BASE_W, BASE_H
    canvas = Image.new("RGB", (w, h), theme.bg)
    draw = ImageDraw.Draw(canvas)
    subtitle = f"{view.subtitle}{SEP}shelf {index + 1} of {sections}"
    _header(draw, theme, view, subtitle, w)

    shown = []
    for row, y in enumerate(BOARD_Y):
        base = y + USABLE_H
        draw.rectangle([0, base, w - 1, base + BOARD_H - 1], fill=BOARD_COLOUR)
        draw.rectangle([0, base, w - 1, base + 1], fill=BOARD_EDGE)
        if row >= len(boards):
            continue
        x = 0
        for i, (run, pieces) in enumerate(boards[row]):
            if i:
                x += RUN_GAP
            slot = _slot(pieces)
            px = x
            for piece in pieces:
                book = piece.book
                if piece.kind == KIND_SPINE:
                    colour = view.colours.get(book.item_id) or spine_colour(None, book.title)
                    art = draw_spine(piece, colour, theme)
                else:
                    pct = view.pct.get(book.item_id, 0.0) if book.status == STATUS_LISTENING else None
                    art = draw_face(piece, view.covers.get(book.item_id), pct, theme)
                canvas.paste(art, (px, base - piece.height))
                px += piece.width + GAP
                shown.append(book)
            _draw_label(canvas, x + 1, base + 2 + (BOARD_H - 2) // 2, run.label, slot - 2)
            x += slot

    total = view.counts.get("total", len(view.books))
    first = surname(shown[0].author) if shown else ""
    last = surname(shown[-1].author) if shown else ""
    footer = _footer_text(len(shown), total, first, last, sections, view.shelf_minutes)
    draw_footer(draw, theme, 0, w, FOOTER_RULE_Y, FOOTER_TEXT_Y, footer, None)
    if (w, h) != tuple(size):
        canvas = canvas.resize(tuple(size), Image.Resampling.LANCZOS)
    info = {
        "sections": sections,
        "section": index,
        "first_author": first,
        "last_author": last,
        "count": len(shown),
        "subtitle": subtitle,
        "footer": footer,
    }
    return canvas, info


def is_face(book):
    return book.status != STATUS_UNREAD


def shelf_art(books, path_of, colours, square=120):
    """(covers, spine colours) for `books`: `path_of(book)` gives a cached cover file or None.

    Only face-out books keep their cover image; every book with a cover gets a colour through
    the ColourCache `colours` (saved at the end).
    """
    covers, rgb = {}, {}
    for book in books:
        path = path_of(book)
        if not path:
            continue
        colour = colours.get(book.item_id, book.updated_at, path)
        if colour is not None:
            rgb[book.item_id] = colour
        if is_face(book):
            try:
                with Image.open(path) as img:
                    rgb_img = img.convert("RGB")
                covers[book.item_id] = ImageOps.fit(rgb_img, (square, square), Image.Resampling.LANCZOS)
            except (OSError, ValueError, Image.DecompressionBombError):
                pass
    try:
        colours.save()
    except OSError:
        pass
    return covers, rgb
