"""Vendored fonts and text fitting: shrink, then wrap, then ellipsize."""
import functools
import math
import os

from PIL import ImageFont

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
SERIF = "EBGaramond.ttf"
SANS = "Inter.ttf"
ELLIPSIS = "\u2026"
_INTER_OPSZ = 14


@functools.lru_cache(maxsize=128)
def font(name, size, weight=None):
    fnt = ImageFont.truetype(os.path.join(FONT_DIR, name), size)
    if weight is not None:
        axes = [weight] if name == SERIF else [_INTER_OPSZ, weight]
        try:
            fnt.set_variation_by_axes(axes)
        except (OSError, AttributeError, ValueError):
            pass
    return fnt


def text_width(fnt, text):
    return int(math.ceil(fnt.getlength(text)))


def ellipsize(text, fnt, max_width):
    if text_width(fnt, text) <= max_width:
        return text
    cut = len(text)
    while cut > 0:
        candidate = text[:cut].rstrip(" ,;:-") + ELLIPSIS
        if text_width(fnt, candidate) <= max_width:
            return candidate
        cut -= 1
    return ELLIPSIS


def _wrap(words, fnt, max_width):
    lines = []
    current = ""
    for word in words:
        trial = f"{current} {word}" if current else word
        if text_width(fnt, trial) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _sizes(max_size, min_size):
    sizes = list(range(max_size, min_size - 1, -2))
    if not sizes or sizes[-1] != min_size:
        sizes.append(min_size)
    return sizes


def fit_text(text, name, max_size, min_size, max_width, max_lines, weight=None):
    words = (text or "").split()
    clean = " ".join(words)
    sizes = _sizes(max_size, min_size)
    for size in sizes:
        fnt = font(name, size, weight)
        if text_width(fnt, clean) <= max_width:
            return [clean], fnt
    for size in sizes:
        fnt = font(name, size, weight)
        lines = _wrap(words, fnt, max_width)
        if len(lines) <= max_lines and all(text_width(fnt, l) <= max_width for l in lines):
            return lines, fnt
    fnt = font(name, min_size, weight)
    lines = _wrap(words, fnt, max_width)
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + [" ".join(lines[max_lines - 1:])]
    return [ellipsize(l, fnt, max_width) for l in lines], fnt
