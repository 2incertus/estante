#!/usr/bin/env python3
"""Render an Estante screen from the live Audiobookshelf onto a fake display.

GET requests only. The token is read from the --env file and is never printed
or written anywhere. Writes two PNGs per run: `<name>.driver.png` (the RGB
image InkyPi hands the driver) and `<name>.png` (approximate panel colours,
for humans), then checks that the driver's own quantize pass is a no-op.

Usage:
    .venv/bin/python scripts/preview.py --screen now --out out
"""
import argparse
import os
import sys
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "plugins"))

from PIL import Image  # noqa: E402

from estante_lib.abs import AbsClient, AbsError, CoverCache, creds_from_env  # noqa: E402
from estante_lib.model import (  # noqa: E402
    build_shelf,
    finished_in_year,
    pick_now_listening,
    progress_index,
)
from estante_lib.palette import PREVIEW, driver_palette, inky_pass, to_rgb  # noqa: E402
from estante_lib.render_now import NowListeningView, render_now_listening  # noqa: E402
from estante_lib.render_shelf import ShelfView, page_books, render_shelf  # noqa: E402
from estante_lib.state import DEFAULT_BADGE, state_dir  # noqa: E402

NOW_COVER_WIDTH = 400
SHELF_COVER_WIDTH = 80
# --screen: (library name in Audiobookshelf, screen title)
SHELVES = {"shelf": ("Audiobooks", "The shelf"), "novels": ("Light Novels", "Light novels")}
# --per-page: (cols, rows)
PAGE_GRIDS = {50: (10, 5), 32: (8, 4)}


def read_env(path):
    env = {}
    with open(os.path.expanduser(path), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.startswith("export "):
                key = key[len("export "):]
            env[key.strip()] = value.strip().strip("'\"")
    return env


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", default="~/config/planetario.env")
    ap.add_argument("--screen", choices=["now", "shelf", "novels"], default="now")
    ap.add_argument("--layout", choices=["pages", "mosaic"], default="pages")
    ap.add_argument("--page", type=int, default=0, help="0-based page for the pages layout")
    ap.add_argument("--per-page", type=int, choices=sorted(PAGE_GRIDS), default=50)
    ap.add_argument("--badge", action="store_true", help="message waiting from Jasmine")
    ap.add_argument("--dnd", action="store_true", help="quiet hours")
    ap.add_argument("--out", default="out")
    return ap.parse_args(argv)


def make_badge(args):
    badge = dict(DEFAULT_BADGE)
    if args.badge:
        badge.update({"message_waiting": True, "from": "Jasmine", "count": 1})
    if args.dnd:
        badge["dnd"] = True
    return badge


def open_cover(path):
    if not path:
        return None
    try:
        with Image.open(path) as img:
            return img.convert("RGB")
    except OSError:
        return None


def render_now(client, covers, badge):
    nl = pick_now_listening(client)
    cover = open_cover(covers.path(nl.item_id, nl.updated_at, NOW_COVER_WIDTH)) if nl else None
    now = datetime.now().astimezone()
    finished = finished_in_year(client.me(), now.year, now.tzinfo)
    view = NowListeningView(nl, cover, finished, now.year, badge)
    return "now", render_now_listening(view)


def render_shelf_screen(client, covers, badge, args):
    library_name, title = SHELVES[args.screen]
    library = next((lib for lib in client.libraries() if lib.get("name") == library_name), None)
    if library is None:
        raise AbsError(f"no library named {library_name}")
    books = build_shelf(client.library_items(library["id"]), progress_index(client.me()))
    cols, rows = PAGE_GRIDS[args.per_page]
    if args.layout == "mosaic":
        shown, page = books, 0
    else:
        shown, page, _ = page_books(books, args.page, cols * rows)
    loaded = {}
    for book in shown:
        cover = open_cover(covers.path(book.item_id, book.updated_at, SHELF_COVER_WIDTH))
        if cover is not None:
            loaded[book.item_id] = cover
    view = ShelfView(
        title, f"{library_name}, by author", books, loaded, page=page, layout=args.layout,
        badge=badge, cols=cols, rows=rows,
    )
    if args.layout == "mosaic":
        name = f"{args.screen}-mosaic"
    else:
        name = f"{args.screen}-p{page + 1}" + ("-large" if args.per_page != 50 else "")
    return name, render_shelf(view)


def write_and_check(img, name, out_dir):
    pal = driver_palette(0.5)
    driver = to_rgb(img, pal)
    driver_path = os.path.join(out_dir, f"{name}.driver.png")
    human_path = os.path.join(out_dir, f"{name}.png")
    driver.save(driver_path)
    to_rgb(img, PREVIEW).save(human_path)
    with Image.open(driver_path) as saved:
        passed = inky_pass(saved.convert("RGB"))
    ok = passed.size == img.size and passed.tobytes() == img.tobytes()
    print(f"wrote {human_path} and {driver_path}")
    print(f"no-op check: {'ok' if ok else 'FAILED'}")
    return ok


def main(argv=None):
    args = parse_args(argv)
    os.environ.setdefault("ESTANTE_STATE_DIR", os.path.join(args.out, "state"))
    try:
        url, token = creds_from_env(read_env(args.env))
    except (OSError, AbsError) as e:
        print(f"cannot read credentials from {args.env}: {e}", file=sys.stderr)
        return 2
    client = AbsClient(url, token)
    covers = CoverCache(os.path.join(state_dir(), "covers"), client)
    badge = make_badge(args)
    try:
        if args.screen == "now":
            name, img = render_now(client, covers, badge)
        else:
            name, img = render_shelf_screen(client, covers, badge, args)
    except AbsError as e:
        print(str(e), file=sys.stderr)
        return 2
    if args.badge or args.dnd:
        name += "-badge"
    os.makedirs(args.out, exist_ok=True)
    return 0 if write_and_check(img, name, args.out) else 1


if __name__ == "__main__":
    sys.exit(main())
