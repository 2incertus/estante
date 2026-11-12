#!/usr/bin/env python3
"""Render the Estante screen from the recorded fixtures, time it and check the framebuffer.

Runs with whatever Pillow the interpreter has (on the Pi: /opt/estante/.venv/bin/python).
Serves tests/fixtures through the local fake Audiobookshelf on 127.0.0.1, renders the
1920x1080 composition as estante-ctl does (Now listening beside the first section of the
audiobooks shelf, a message badge on), once on the dark theme and once on paper dimmed to
half, with the spine colours computed beforehand as the conductor keeps them cached, turns each
frame 180 degrees as the installed config does and converts it to 16 bpp framebuffer
bytes, and times the whole path. Warns when a frame takes longer than the budget (3 s,
the 3B+ target), and when the heaviest shelf section (most spines) alone, drawn from
scratch with its colours cached, takes longer than 1.5 s. When the framebuffer's sysfs
directory exists it reads its geometry: a depth Estante cannot write fails, a size other than 1920x1080 warns
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
# the heaviest shelf section alone, spine colours cached, spine images not: the 3B+ target
SECTION_BUDGET_S = 1.5
COVER_WIDTH = 120
NOW_COVER_WIDTH = 520
DEFAULT_SYSFS = "/sys/class/graphics/fb0"
FB_DEPTHS = (16, 32)
# the installer's default: the K3 stands upside down, so every frame is turned
ROTATE = 180


def screen_views(fixtures):
    """Yield (name, ScreenView) for each frame to render, built from the fixtures."""
    from fake_abs import FakeAbs

    from estante_lib.abs import AbsClient, CoverCache
    from estante_lib.model import (
        finished_in_year,
        pick_now_listening,
        progress_index,
        shelf_counts,
        shelf_parts,
    )
    from estante_lib.render_now import NowListeningView
    from estante_lib.render_screen import ScreenView, load_image
    from estante_lib.render_spines import ColourCache, ShelfSectionView, shelf_art
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

        books, runs, hours, pct = shelf_parts(client.library_items(AUDIOBOOKS_ID), progress_index(me))
        loaded, colours = shelf_art(
            books,
            lambda b: covers.path(b.item_id, b.updated_at, COVER_WIDTH),
            ColourCache(os.path.join(tmp, "spines.json")),
            square=COVER_WIDTH,
        )
        shelf = ShelfSectionView(
            "The shelf", "Audiobooks, by series and author", books, runs, hours, pct, loaded, colours,
            shelf_counts(books), 0, 2, badge,
        )
        yield "dark", ScreenView(now, shelf, DARK)
        yield "paper dimmed", ScreenView(now, shelf, PAPER, 0.5)


def check(name, img):
    if img.size != SIZE:
        raise AssertionError(f"{name}: size {img.size}, expected {SIZE}")
    if img.mode != "RGB":
        raise AssertionError(f"{name}: mode {img.mode}, expected RGB")


def render_and_time(fixtures):
    """[(name, seconds)] for each frame: the composition, the 180 degree turn and the 16 bpp bytes."""
    from estante_lib.display_fb import FbInfo
    from estante_lib.render_screen import render_screen, to_fb_bytes

    info = FbInfo(SIZE[0], SIZE[1], 16, SIZE[0] * 2)
    timings = []
    last = None
    for name, view in screen_views(fixtures):
        last = view
        start = time.perf_counter()
        img = render_screen(view, SIZE)
        composed = time.perf_counter()
        turned = img.rotate(ROTATE)
        pixels = to_fb_bytes(turned, info)
        seconds = time.perf_counter() - start
        check(name, img)
        if len(pixels) != info.stride * info.height:
            raise AssertionError(f"{name}: {len(pixels)} framebuffer bytes, expected {info.stride * info.height}")
        print(
            f"render {name}: compose {composed - start:.2f} s, rotate {ROTATE} and convert "
            f"{seconds - (composed - start):.2f} s, total {seconds:.2f} s"
        )
        timings.append((name, seconds))
    return timings, time_section(last)


def time_section(view):
    """Seconds for the heaviest shelf section (most spines) alone, drawn from scratch with the
    colours cached: the slowest frame the shelf has."""
    import dataclasses

    from estante_lib.render_spines import clear_caches, heaviest_section, render_section

    index, spines = heaviest_section(view.shelf.runs, view.shelf.hours)
    shelf = dataclasses.replace(view.shelf, section=index)
    clear_caches()
    start = time.perf_counter()
    _, info = render_section(shelf, view.theme)
    seconds = time.perf_counter() - start
    print(
        f"shelf section {index + 1} of {info['sections']}: {spines} spines, "
        f"{info['count']} books, {seconds:.2f} s"
    )
    return seconds


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

        timings, section = render_and_time(args.fixtures)
        print(f"rendered {', '.join(name for name, _ in timings)} with Pillow {PIL.__version__}")
        name, slowest = max(timings, key=lambda t: t[1])
        if slowest > args.budget:
            warnings.append(
                f"selftest: warning: render {name} took {slowest:.2f} s, over the {args.budget:.1f} s budget"
            )
        if section > SECTION_BUDGET_S:
            warnings.append(
                f"selftest: warning: a shelf section took {section:.2f} s, "
                f"over the {SECTION_BUDGET_S:.1f} s budget"
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
