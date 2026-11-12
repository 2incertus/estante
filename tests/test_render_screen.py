import dataclasses
import time

import pytest
from PIL import ImageStat

from estante_lib.display_fb import FbInfo, rgb_to_bgrx, rgb_to_rgb565
from estante_lib.render_screen import ScreenView, render_screen, to_fb_bytes
from estante_lib.theme import DARK, PAPER


def _mean(img):
    return sum(ImageStat.Stat(img).mean) / 3


def test_composition_size_divider_and_halves(now_view, wall_view):
    for theme in (DARK, PAPER):
        img = render_screen(ScreenView(now_view, wall_view, theme))
        assert img.size == (1920, 1080) and img.mode == "RGB"
        assert img.getpixel((630, 540)) == theme.muted  # divider
        assert img.getpixel((630, 20)) == theme.bg and img.getpixel((630, 1060)) == theme.bg
        assert img.getpixel((629, 540)) == theme.bg and img.getpixel((631, 540)) == theme.bg
        assert img.getpixel((1919, 5)) == theme.bg and img.getpixel((5, 5)) == theme.bg
    # the cover sits in the column, the wall tiles start at x 650 + 7
    img = render_screen(ScreenView(now_view, wall_view, DARK))
    assert img.getpixel((300, 350)) != DARK.bg
    assert img.getpixel((650 + 7 + 20, 110 + 20)) != DARK.bg


def test_dim_halves_the_mean(now_view, wall_view):
    view = ScreenView(now_view, wall_view, PAPER)
    full = _mean(render_screen(view))
    half = _mean(render_screen(dataclasses.replace(view, dim=0.5)))
    assert abs(half / full - 0.5) < 0.02


def test_dim_is_clamped(now_view, wall_view):
    view = ScreenView(now_view, wall_view, PAPER)
    full = _mean(render_screen(view))
    floor = _mean(render_screen(dataclasses.replace(view, dim=0.0)))
    assert abs(floor / full - 0.1) < 0.02
    over = render_screen(dataclasses.replace(view, dim=3.0))
    assert over.tobytes() == render_screen(view).tobytes()


def test_other_16_9_size(now_view, wall_view):
    img = render_screen(ScreenView(now_view, wall_view, DARK), size=(1280, 720))
    assert img.size == (1280, 720)
    assert img.getpixel((420, 360)) == DARK.muted  # 630 * 2 / 3


def _sample():
    from PIL import Image

    img = Image.new("RGB", (7, 3), (18, 18, 20))
    for i, colour in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255), (0x9C, 0x5A, 0x31), (255, 255, 255)]):
        img.putpixel((i, 1), colour)
    img.putpixel((6, 2), (1, 254, 127))
    return img


def test_to_fb_bytes_565_matches_the_reference():
    img = _sample()
    out = to_fb_bytes(img, FbInfo(7, 3, 16, 14))
    assert out == rgb_to_rgb565(img.tobytes())


def test_to_fb_bytes_bgrx_matches_the_reference():
    img = _sample()
    out = to_fb_bytes(img, FbInfo(7, 3, 32, 28))
    assert out == rgb_to_bgrx(img.tobytes())


def test_to_fb_bytes_resizes_to_the_framebuffer():
    from PIL import Image

    img = Image.new("RGB", (1920, 1080), (40, 80, 120))
    out = to_fb_bytes(img, FbInfo(64, 36, 16, 128))
    assert len(out) == 64 * 36 * 2
    assert out[-2:] == rgb_to_rgb565(bytes([40, 80, 120]))


def test_to_fb_bytes_rejects_other_depths():
    with pytest.raises(ValueError):
        to_fb_bytes(_sample(), FbInfo(7, 3, 24, 21))


def test_to_fb_bytes_565_is_fast_at_1080p(now_view, wall_view):
    img = render_screen(ScreenView(now_view, wall_view, DARK))
    start = time.perf_counter()
    out = to_fb_bytes(img, FbInfo(1920, 1080, 16, 3840))
    assert time.perf_counter() - start < 0.2
    assert len(out) == 1920 * 1080 * 2


def _random_image(w, h, seed):
    import random

    from PIL import Image

    rng = random.Random(seed)
    return Image.frombytes("RGB", (w, h), bytes(rng.randrange(256) for _ in range(w * h * 3)))


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_to_fb_bytes_matches_the_references_on_random_pixels(seed):
    img = _random_image(37, 23, seed)
    rgb = img.tobytes()
    assert to_fb_bytes(img, FbInfo(37, 23, 16, 74)) == rgb_to_rgb565(rgb)
    assert to_fb_bytes(img, FbInfo(37, 23, 32, 148)) == rgb_to_bgrx(rgb)


def test_to_fb_bytes_565_bit_layout():
    # vc4drmfb rgba 5/11,6/5,5/0: red in bits 15-11, green 10-5, blue 4-0, little-endian
    from PIL import Image

    img = Image.new("RGB", (3, 1))
    for x, colour in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255)]):
        img.putpixel((x, 0), colour)
    assert to_fb_bytes(img, FbInfo(3, 1, 16, 6)) == b"\x00\xf8\xe0\x07\x1f\x00"


def test_screen_is_a_plain_rgb_image_the_caller_can_rotate(now_view, wall_view):
    img = render_screen(ScreenView(now_view, wall_view, DARK))
    turned = img.rotate(180)
    assert turned.mode == "RGB" and turned.size == (1920, 1080)
    assert turned.getpixel((1919 - 630, 1079 - 540)) == DARK.muted  # the divider, upside down
    out = to_fb_bytes(turned, FbInfo(1920, 1080, 16, 3840))
    assert out == rgb_to_rgb565(turned.tobytes())


def test_to_fb_bytes_bgrx_is_fast_at_1080p():
    img = _random_image(64, 36, 4).resize((1920, 1080))
    start = time.perf_counter()
    out = to_fb_bytes(img, FbInfo(1920, 1080, 32, 7680))
    assert time.perf_counter() - start < 0.2
    assert len(out) == 1920 * 1080 * 4
