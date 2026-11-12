#!/usr/bin/env python3
"""Render the Estante screen from the recorded fixtures, time it and check the framebuffer.

Runs with whatever Pillow the interpreter has (on the Pi: /opt/estante/.venv/bin/python).
Serves tests/fixtures through the local fake Audiobookshelf on 127.0.0.1, renders the
1920x1080 composition as estante-ctl does (Now listening beside the audiobooks wall, a
message badge on), once on the dark theme and once on paper dimmed to half, converts each
frame to 16 bpp framebuffer bytes and times both steps. Warns when a frame takes longer
than the budget (3 s, the 3B+ target). When the framebuffer's sysfs directory exists it
reads its geometry: a depth Estante cannot write fails, a size other than 1920x1080 warns
(the HDMI mode is not forced until the reboot after the install). Never writes to the
screen. Prints `selftest: ok` and exits 0, or prints the first failure and exits 1.

Usage:
    python3 scripts/selftest.py [--fixtures DIR] [--sysfs DIR] [--budget SECONDS]
"""
import argparse
import os
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "plugins"))
sys.path.insert(0, os.path.join(REPO, "tests"))

AUDIOBOOKS_ID = "b14de8f5-99a5-4cba-b970-6a2cb65f2993"
SIZE = (1920, 1080)
BUDGET_S = 3.0
COVER_WIDTH = 120
NOW_COVER_WIDTH = 520
DEFAULT_SYSFS = "/sys/class/graphics/fb0"
FB_DEPTHS = (16, 32)


def screen_views(fixtures):
    """Yield (name, ScreenView) for each frame to render, built from the fixtures."""
    from fake_abs import FakeAbs

    from estante_lib.abs import AbsClient, CoverCache
    from estante_lib.model import (
        build_shelf,
        finished_in_year,
        pick_now_listening,
        progress_index,
        shelf_counts,
    )
    from estante_lib.render_now import NowListeningView
    from estante_lib.render_screen import ScreenView, load_image
    from estante_lib.render_wall import WallView
    from estante_lib.theme import DARK, PAPER

    if not os.path.isdir(fixtures):
        raise RuntimeError(f"no fixtures at {fixtures}")
    badge = {"message_waiting": True, "dnd": False, "count": 1, "from": "Jasmine"}
    with FakeAbs(fixtures) as fake, tempfile.TemporaryDirectory() as tmp:
        client = AbsClient(fake.base_url, "TESTTOKEN", timeout=5)
        covers = CoverCache(tmp, client)

        nl = pick_now_listening(client)
        path = covers.path(nl.item_id, nl.updated_at, NOW_COVER_WIDTH) if nl else None
        cover = load_image(path) if path else None
        me = client.me()
        year = datetime.now(timezone.utc).year
        now = NowListeningView(nl, cover, finished_in_year(me, year, timezone.utc), year, badge)

        books = build_shelf(client.library_items(AUDIOBOOKS_ID), progress_index(me))
        loaded = {}
        for book in books:
            path = covers.path(book.item_id, book.updated_at, COVER_WIDTH)
            img = load_image(path, square=COVER_WIDTH) if path else None
            if img is not None:
                loaded[book.item_id] = img
        wall = WallView("The shelf", "Audiobooks, by author", books, loaded, shelf_counts(books))
        yield "dark", ScreenView(now, wall, DARK)
        yield "paper dimmed", ScreenView(now, wall, PAPER, 0.5)


def check(name, img):
    if img.size != SIZE:
        raise AssertionError(f"{name}: size {img.size}, expected {SIZE}")
    if img.mode != "RGB":
        raise AssertionError(f"{name}: mode {img.mode}, expected RGB")


def render_and_time(fixtures):
    """[(name, seconds)] for each frame: the composition plus its 16 bpp framebuffer bytes."""
    from estante_lib.display_fb import FbInfo
    from estante_lib.render_screen import render_screen, to_fb_bytes

    info = FbInfo(SIZE[0], SIZE[1], 16, SIZE[0] * 2)
    timings = []
    for name, view in screen_views(fixtures):
        start = time.perf_counter()
        img = render_screen(view, SIZE)
        pixels = to_fb_bytes(img, info)
        seconds = time.perf_counter() - start
        check(name, img)
        if len(pixels) != info.stride * info.height:
            raise AssertionError(f"{name}: {len(pixels)} framebuffer bytes, expected {info.stride * info.height}")
        print(f"render {name}: {seconds:.2f} s")
        timings.append((name, seconds))
    return timings


def check_framebuffer(sysfs):
    """A warning line, or None; raises when the framebuffer cannot take Estante's frames."""
    from estante_lib.display_fb import read_info

    if not os.path.isdir(sysfs):
        print(f"no framebuffer at {sysfs}, skipped")
        return None
    info = read_info(sysfs)
    if info.bpp not in FB_DEPTHS:
        raise AssertionError(f"framebuffer at {sysfs} is {info.bpp} bpp; Estante writes 16 or 32 bpp")
    print(f"framebuffer {info.width}x{info.height} at {info.bpp} bpp, stride {info.stride}")
    if (info.width, info.height) != SIZE:
        return (
            f"selftest: warning: framebuffer is {info.width}x{info.height}, not 1920x1080; "
            "the HDMI mode is forced only after the reboot the installer asks for"
        )
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fixtures", default=os.path.join(REPO, "tests", "fixtures"))
    ap.add_argument("--sysfs", default=DEFAULT_SYSFS, help="framebuffer sysfs directory")
    ap.add_argument("--budget", type=float, default=BUDGET_S, help="seconds a frame may take")
    args = ap.parse_args(argv)
    warnings = []
    try:
        import PIL

        timings = render_and_time(args.fixtures)
        print(f"rendered {', '.join(name for name, _ in timings)} with Pillow {PIL.__version__}")
        name, slowest = max(timings, key=lambda t: t[1])
        if slowest > args.budget:
            warnings.append(
                f"selftest: warning: render {name} took {slowest:.2f} s, over the {args.budget:.1f} s budget"
            )
        warning = check_framebuffer(args.sysfs)
        if warning:
            warnings.append(warning)
    except Exception as e:  # report the first failure of any kind, then exit 1
        if os.environ.get("ESTANTE_SELFTEST_TRACE"):
            traceback.print_exc()
        print(f"selftest: FAILED: {e}")
        return 1
    for line in warnings:
        print(line)
    print("selftest: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
