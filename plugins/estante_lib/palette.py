"""Spectra 6 palettes and the exact quantize pass of pimoroni inky 2.4.0 (InkyE673)."""
from PIL import Image

# Copied from pimoroni inky 2.4.0 inky_e673.py (first six entries).
DESATURATED = [(0, 0, 0), (255, 255, 255), (255, 255, 0), (255, 0, 0), (0, 0, 255), (0, 255, 0)]
SATURATED = [(0, 0, 0), (161, 164, 165), (208, 190, 71), (156, 72, 75), (61, 59, 94), (58, 91, 70)]
# Roughly what the panel looks like; for human previews only.
PREVIEW = [(26, 28, 31), (236, 233, 224), (232, 205, 62), (168, 34, 30), (38, 78, 160), (40, 96, 52)]

BLACK, WHITE, YELLOW, RED, BLUE, GREEN = 0, 1, 2, 3, 4, 5


def driver_palette(saturation=0.5):
    """The palette inky blends for set_image, exactly as inky._palette_blend."""
    sat = float(saturation)
    return [
        tuple(int(sat * s + (1.0 - sat) * d) for s, d in zip(sc, dc))
        for sc, dc in zip(SATURATED, DESATURATED)
    ]


def _flat(pal):
    return [v for colour in pal for v in colour]


def palette_image(pal):
    img = Image.new("P", (1, 1))
    img.putpalette(_flat(pal))
    return img


def quantize(img, pal, dither=True):
    mode = Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
    return img.convert("RGB").quantize(6, palette=palette_image(pal), dither=mode)


def to_rgb(p_img, pal):
    out = p_img.copy()
    out.putpalette(_flat(pal))
    return out.convert("RGB")


def fade(img, paper, amount=0.78):
    rgb = img.convert("RGB")
    return Image.blend(rgb, Image.new("RGB", rgb.size, tuple(paper)), amount)


def dominant_index(img, pal, exclude=(WHITE,)):
    """Most common ink of `img` (no dither), skipping `exclude`; WHITE when nothing is left."""
    counts = quantize(img, pal, dither=False).histogram()[:6]
    for i in exclude:
        counts[i] = 0
    best = max(range(6), key=lambda i: counts[i])
    return best if counts[best] else WHITE


def inky_pass(rgb, saturation=0.5):
    """Replicate InkyE673.set_image for a non-"P" image: dithered quantize to the driver palette."""
    return rgb.convert("RGB").quantize(
        6, palette=palette_image(driver_palette(saturation)), dither=Image.Dither.FLOYDSTEINBERG
    )
