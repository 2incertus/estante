from PIL import Image

from estante_lib.palette import (
    BLACK,
    BLUE,
    DESATURATED,
    GREEN,
    PREVIEW,
    RED,
    SATURATED,
    WHITE,
    YELLOW,
    dominant_index,
    driver_palette,
    fade,
    inky_pass,
    palette_image,
    quantize,
    to_rgb,
)

def px(img):
    """Flattened pixel values on any Pillow >= 11."""
    get = getattr(img, "get_flattened_data", None) or img.getdata
    return list(get())


# jq -r '.libraryItems[0].id' tests/fixtures/in_progress.json -> the w400 cover's id
OVERGEARED_W400 = "4aa1594d-b6ce-441f-9ab3-951ff1340009-w400.jpg"


def test_index_constants():
    assert (BLACK, WHITE, YELLOW, RED, BLUE, GREEN) == (0, 1, 2, 3, 4, 5)


def test_palettes_are_six_rgb_triples():
    for pal in (DESATURATED, SATURATED, PREVIEW):
        assert len(pal) == 6 and all(len(c) == 3 for c in pal)


def test_driver_palette_half_saturation():
    # Pinned from pimoroni inky 2.4.0 itself: Inky._palette_blend(0.5) from inky_e673.py,
    # run on the released source. Green is (29, 173, 35): int(58*.5+0*.5),
    # int(91*.5+255*.5), int(70*.5+0*.5). The plan text says (29, 45, 35), which
    # no blend of SATURATED and DESATURATED can produce.
    assert driver_palette(0.5) == [(0, 0, 0), (208, 209, 210), (231, 222, 35), (205, 36, 37), (30, 29, 174), (29, 173, 35)]


def test_driver_palette_extremes():
    assert driver_palette(0.0) == DESATURATED
    assert driver_palette(1.0) == SATURATED


def test_palette_image_holds_the_palette():
    pal = driver_palette(0.5)
    img = palette_image(pal)
    assert img.mode == "P" and img.size == (1, 1)
    assert img.getpalette()[:18] == [v for c in pal for v in c]


def test_quantize_exact_colours_roundtrip():
    pal = driver_palette(0.5)
    img = Image.new("RGB", (6, 1))
    img.putdata(pal)
    q = quantize(img, pal)
    assert q.mode == "P"
    assert px(q) == [0, 1, 2, 3, 4, 5]
    assert q.getpalette()[:18] == [v for c in pal for v in c]


def test_to_rgb_maps_indexes_to_palette():
    pal = driver_palette(0.5)
    p = Image.new("P", (6, 1))
    p.putdata([5, 4, 3, 2, 1, 0])
    assert px(to_rgb(p, pal)) == list(reversed(pal))


def test_inky_pass_is_noop_on_rendered_palette_image(fixtures_dir):
    pal = driver_palette(0.5)
    cover = Image.open(fixtures_dir / "covers" / OVERGEARED_W400).convert("RGB")
    p = quantize(cover, pal)                      # our dithered output
    rgb = to_rgb(p, pal)
    out = inky_pass(rgb)
    assert out.mode == "P"
    assert px(out) == px(p)
    assert max(px(out)) <= 5
    assert len(set(px(p))) >= 4             # a real cover, not a flat image


def test_inky_pass_dithers_off_palette_input():
    grey = Image.new("RGB", (40, 40), (120, 120, 120))
    out = inky_pass(grey)
    assert len(set(px(out))) > 1            # Floyd-Steinberg mixes inks


def test_quantize_without_dither_is_flat():
    grey = Image.new("RGB", (40, 40), (120, 120, 120))
    assert len(set(px(quantize(grey, driver_palette(0.5), dither=False)))) == 1


def _mean_distance(img, colour):
    pixels = px(img.convert("RGB"))
    return sum(sum(abs(a - b) for a, b in zip(p, colour)) for p in pixels) / len(pixels)


def test_fade_moves_toward_paper():
    paper = (236, 233, 224)
    src = Image.new("RGB", (10, 10), (30, 29, 174))
    faded = fade(src, paper)
    assert faded.size == src.size
    assert _mean_distance(faded, paper) < _mean_distance(src, paper) * 0.3
    assert _mean_distance(fade(src, paper, 0.0), paper) == _mean_distance(src, paper)


def test_dominant_index_ignores_white():
    pal = driver_palette(0.5)
    img = Image.new("RGB", (10, 10), pal[WHITE])
    for x in range(10):
        img.putpixel((x, 0), pal[RED])
    assert dominant_index(img, pal) == RED
    assert dominant_index(Image.new("RGB", (10, 10), pal[WHITE]), pal) == WHITE
