#!/usr/bin/env python3
"""Render the Estante screen from the recorded fixtures and check it.

Runs with whatever Pillow the interpreter has. Serves tests/fixtures through the local
fake Audiobookshelf, renders the 1920x1080 composition (Now listening beside the
audiobooks wall, then beside the light-novel wall) and checks each is a 1920x1080 RGB
frame. Never touches the screen or the network beyond 127.0.0.1. Prints `selftest: ok`
and exits 0, or prints the first failure and exits 1. Also warns (without failing) when
InkyPi's device.json ($INKYPI/src/config/device.json, INKYPI defaults to
/usr/local/inkypi) holds an image enhancement other than 1.0 (a v1 leftover until the
installer is rewritten).

Usage:
    python3 scripts/selftest.py [--fixtures DIR]
"""
import argparse
import json
import os
import sys
import tempfile
import traceback
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "plugins"))
sys.path.insert(0, os.path.join(REPO, "tests"))

AUDIOBOOKS_ID = "b14de8f5-99a5-4cba-b970-6a2cb65f2993"
LIGHT_NOVELS_ID = "65bcd45d-1cce-496c-950e-4f3aabf5f5eb"
SIZE = (1920, 1080)
NEUTRAL_IMAGE_SETTINGS = ("saturation", "contrast", "sharpness", "brightness")


def _open(path):
    from PIL import Image

    if not path:
        return None
    with Image.open(path) as img:
        return img.convert("RGB")


def render_screens(fixtures):
    """Yield (name, RGB image) for each composition, rendered from the fixtures."""
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
    from estante_lib.render_screen import ScreenView, render_screen
    from estante_lib.render_wall import WallView
    from estante_lib.theme import DARK

    if not os.path.isdir(fixtures):
        raise RuntimeError(f"no fixtures at {fixtures}")
    badge = {"message_waiting": True, "dnd": False, "count": 1, "from": "Jasmine"}
    with FakeAbs(fixtures) as fake, tempfile.TemporaryDirectory() as tmp:
        client = AbsClient(fake.base_url, "TESTTOKEN", timeout=5)
        covers = CoverCache(tmp, client)

        nl = pick_now_listening(client)
        cover = _open(covers.path(nl.item_id, nl.updated_at, 400)) if nl else None
        year = datetime.now(timezone.utc).year
        finished = finished_in_year(client.me(), year, timezone.utc)
        now = NowListeningView(nl, cover, finished, year, badge)

        for name, library_id, title, subtitle in (
            ("audiobooks screen", AUDIOBOOKS_ID, "Audiobooks", "Audiobooks, by author"),
            ("light novels screen", LIGHT_NOVELS_ID, "Light novels", "Light Novels, by author"),
        ):
            books = build_shelf(client.library_items(library_id), progress_index(client.me()))
            loaded = {}
            for book in books:
                img = _open(covers.path(book.item_id, book.updated_at, 80))
                if img is not None:
                    loaded[book.item_id] = img
            wall = WallView(title, subtitle, books, loaded, shelf_counts(books))
            yield name, render_screen(ScreenView(now, wall, DARK), SIZE)


def check(name, img):
    if img.size != SIZE:
        raise AssertionError(f"{name}: size {img.size}, expected {SIZE}")
    if img.mode != "RGB":
        raise AssertionError(f"{name}: mode {img.mode}, expected RGB")


def image_settings_warning(inkypi_dir):
    """A warning line when InkyPi's image enhancements are not neutral, else None."""
    path = os.path.join(inkypi_dir, "src", "config", "device.json")
    try:
        with open(path, encoding="utf-8") as f:
            settings = (json.load(f) or {}).get("image_settings") or {}
    except (OSError, ValueError, AttributeError):
        return None
    off = []
    for name in NEUTRAL_IMAGE_SETTINGS:
        value = settings.get(name, 1.0)
        try:
            neutral = float(value) == 1.0
        except (TypeError, ValueError):
            neutral = False
        if not neutral:
            off.append(f"{name} {value}")
    if not off:
        return None
    return (
        f"selftest: warning: InkyPi image settings in {path} are not 1.0 ({', '.join(off)}); "
        "flat areas will dither on the panel"
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fixtures", default=os.path.join(REPO, "tests", "fixtures"))
    args = ap.parse_args(argv)
    try:
        import PIL

        names = []
        for name, img in render_screens(args.fixtures):
            check(name, img)
            names.append(name)
        print(f"rendered {', '.join(names)} with Pillow {PIL.__version__}")
        warning = image_settings_warning(os.environ.get("INKYPI") or "/usr/local/inkypi")
        if warning:
            print(warning)
    except Exception as e:  # report the first failure of any kind, then exit 1
        if os.environ.get("ESTANTE_SELFTEST_TRACE"):
            traceback.print_exc()
        print(f"selftest: FAILED: {e}")
        return 1
    print("selftest: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
