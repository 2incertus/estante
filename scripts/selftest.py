#!/usr/bin/env python3
"""Render the Estante screens from the recorded fixtures and check them for the panel.

Runs with whatever Pillow the interpreter has (InkyPi's venv on the Pi, .venv on the
ryzen). Serves tests/fixtures through the local fake Audiobookshelf, renders Now
listening, a page of the shelf and the light-novel mosaic, and checks each is 800x480,
uses only ink indexes 0..5 and passes through the inky driver's quantize unchanged.
Never touches the panel or the network beyond 127.0.0.1. Prints `selftest: ok` and
exits 0, or prints the first failure and exits 1.

Usage:
    python3 scripts/selftest.py [--fixtures DIR]
"""
import argparse
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
SIZE = (800, 480)


def _open(path):
    from PIL import Image

    if not path:
        return None
    with Image.open(path) as img:
        return img.convert("RGB")


def render_screens(fixtures):
    """Yield (name, P image) for each screen, rendered from the fixtures."""
    from fake_abs import FakeAbs

    from estante_lib.abs import AbsClient, CoverCache
    from estante_lib.model import build_shelf, finished_in_year, pick_now_listening, progress_index
    from estante_lib.render_now import NowListeningView, render_now_listening
    from estante_lib.render_shelf import ShelfView, page_books, render_shelf
    from estante_lib.state import DEFAULT_BADGE

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
        view = NowListeningView(nl, cover, finished, year, dict(DEFAULT_BADGE))
        yield "now listening", render_now_listening(view, SIZE)

        for name, library_id, layout, title, subtitle in (
            ("the shelf", AUDIOBOOKS_ID, "pages", "The shelf", "Audiobooks, by author"),
            ("light novels mosaic", LIGHT_NOVELS_ID, "mosaic", "Light novels", "Light Novels, by author"),
        ):
            books = build_shelf(client.library_items(library_id), progress_index(client.me()))
            shown = books if layout == "mosaic" else page_books(books, 0)[0]
            loaded = {}
            for book in shown:
                img = _open(covers.path(book.item_id, book.updated_at, 80))
                if img is not None:
                    loaded[book.item_id] = img
            view = ShelfView(title, subtitle, books, loaded, layout=layout, badge=badge)
            yield name, render_shelf(view, SIZE)


def check(name, img):
    from estante_lib.palette import driver_palette, inky_pass, to_rgb

    if img.size != SIZE:
        raise AssertionError(f"{name}: size {img.size}, expected {SIZE}")
    if img.mode != "P":
        raise AssertionError(f"{name}: mode {img.mode}, expected P")
    if any(img.histogram()[6:]):
        raise AssertionError(f"{name}: palette indexes outside 0..5")
    passed = inky_pass(to_rgb(img, driver_palette(0.5)))
    if passed.tobytes() != img.tobytes():
        raise AssertionError(f"{name}: the inky driver pass would change pixels")


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
    except Exception as e:  # report the first failure of any kind, then exit 1
        if os.environ.get("ESTANTE_SELFTEST_TRACE"):
            traceback.print_exc()
        print(f"selftest: FAILED: {e}")
        return 1
    print("selftest: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
