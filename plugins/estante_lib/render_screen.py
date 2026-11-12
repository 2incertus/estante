"""The 1920x1080 composition: Now listening on the left, the shelf of spines on the right."""
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageOps

from .render_now import NowListeningView, render_now_column
from .render_spines import ShelfSectionView, render_section
from .text import SANS, font
from .theme import Theme

BASE_W, BASE_H = 1920, 1080
COLUMN_W, SHELF_X, SHELF_W = 620, 650, 1230
DIVIDER_X, DIVIDER_Y0, DIVIDER_Y1 = 630, 40, 1040
DIM_MIN, DIM_MAX = 0.1, 1.0


@dataclass
class ScreenView:
    now: NowListeningView
    shelf: ShelfSectionView
    theme: Theme
    dim: float = 1.0


def render_screen(view, size=(BASE_W, BASE_H)):
    """The whole screen as an RGB image; every coordinate scales by min(W/1920, H/1080)."""
    w, h = size
    s = min(w / BASE_W, h / BASE_H)

    def p(v):
        return round(v * s)

    theme = view.theme
    canvas = Image.new("RGB", (w, h), theme.bg)
    height = p(BASE_H)
    canvas.paste(render_now_column(view.now, theme, (p(COLUMN_W), height), s), (0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.line([(p(DIVIDER_X), p(DIVIDER_Y0)), (p(DIVIDER_X), p(DIVIDER_Y1))], fill=theme.muted, width=1)
    shelf, _ = render_section(view.shelf, theme, (p(SHELF_W), height), s)
    canvas.paste(shelf, (p(SHELF_X), 0))
    dim = min(DIM_MAX, max(DIM_MIN, float(view.dim)))
    if dim < 1.0:
        canvas = ImageEnhance.Brightness(canvas).enhance(dim)
    return canvas


MARK = "ESTANTE"


def render_mark(theme, size=(BASE_W, BASE_H)):
    """The boot frame: the theme background with ESTANTE (Inter 40, weight 700) in the centre."""
    w, h = size
    s = min(w / BASE_W, h / BASE_H)
    canvas = Image.new("RGB", (w, h), theme.bg)
    draw = ImageDraw.Draw(canvas)
    draw.text((w // 2, h // 2), MARK, font=font(SANS, max(1, round(40 * s)), 700), fill=theme.mark, anchor="mm")
    return canvas


def load_image(path, square=None):
    """An RGB copy of the image at `path` (a cached cover), or None when it cannot be read.

    With `square`, centre-cropped to a square of that side, as a face-out cover crops it
    anyway, so the covers held in memory stay small.
    """
    try:
        with Image.open(path) as img:
            rgb = img.convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    if square:
        rgb = ImageOps.fit(rgb, (square, square), Image.Resampling.LANCZOS)
    return rgb


_HI_R = [v & 0xF8 for v in range(256)]
_HI_G = [v >> 5 for v in range(256)]
_LO_G = [(v << 3) & 0xE0 for v in range(256)]
_LO_B = [v >> 3 for v in range(256)]


def to_fb_bytes(img, info):
    """`img` in the framebuffer's pixel format, resized (Lanczos) to the framebuffer when needed.

    32 bpp is Pillow's BGRX raw mode. 16 bpp is RGB565 little-endian (red in bits 15-11,
    the vc4drmfb layout). Pillow has no RGB to RGB565 raw packer: `tobytes("raw", "BGR;16")`
    raises "No packer found" on 11.3 and 13.0 (the old BGR;16 image mode was deprecated in
    11 and removed in 12). So 16 bpp builds the two bytes of each pixel with lookup tables
    (the bit fields never overlap, so adding them is an OR) and interleaves them as an LA
    image: all of it runs in C, about 50 ms for a 1080p frame on the ryzen.
    """
    img = img.convert("RGB")
    if img.size != (info.width, info.height):
        img = img.resize((info.width, info.height), Image.Resampling.LANCZOS)
    if info.bpp == 32:
        return img.tobytes("raw", "BGRX")
    if info.bpp == 16:
        r, g, b = img.split()
        hi = ImageChops.add(r.point(_HI_R), g.point(_HI_G))
        lo = ImageChops.add(g.point(_LO_G), b.point(_LO_B))
        return Image.merge("LA", (lo, hi)).tobytes("raw", "LA")
    raise ValueError(f"unsupported framebuffer depth: {info.bpp} bpp")
