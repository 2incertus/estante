#!/usr/bin/env python3
"""Render the live Estante screen (Now listening beside the audiobooks wall) into a PNG.

GET requests only. The Audiobookshelf address and token come from the --env file
(ABS_URL, ABS_TOKEN); the token is never printed or written anywhere. The composition
is the one estante-ctl draws: the wall resolved by name as the conductor resolves it,
covers at the conductor's widths, cached under $ESTANTE_STATE_DIR/covers (default
out/state) so a second run fetches nothing. Writes `screen-<theme>[-badge][-dnd][-dim].png`
into --out. With --fb-file it also writes the frame as 1920x1080 16 bpp framebuffer bytes
through FileFramebuffer, reads them back, decodes the RGB565 and checks every pixel
against the frame within the 5/6/5 bit rounding.

Usage:
    ESTANTE_STATE_DIR=/tmp/estante/state .venv/bin/python scripts/preview.py \\
        --env ~/config/planetario.env --theme dark --badge --out out --fb-file out/fb.raw
"""
import argparse
import os
import sys
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "plugins"))
sys.path.insert(0, os.path.join(REPO, "ctl"))

from PIL import Image, ImageChops  # noqa: E402

import estante_ctl  # noqa: E402
from estante_lib.abs import AbsClient, AbsError, CoverCache, creds_from_env  # noqa: E402
from estante_lib.display_fb import FbInfo, FileFramebuffer  # noqa: E402
from estante_lib.model import (  # noqa: E402
    build_shelf,
    finished_in_year,
    pick_now_listening,
    progress_index,
    shelf_counts,
)
from estante_lib.render_now import NowListeningView  # noqa: E402
from estante_lib.render_screen import ScreenView, load_image, render_screen, to_fb_bytes  # noqa: E402
from estante_lib.render_wall import WallView  # noqa: E402
from estante_lib.state import DEFAULT_BADGE  # noqa: E402
from estante_lib.theme import THEMES, get_theme  # noqa: E402

SIZE = (1920, 1080)
WALL = {"name": "audiobooks", "library_id": "", "title": "The shelf"}
# RGB565 keeps the top 5, 6 and 5 bits, so a decoded channel is off by at most 7
ROUNDING = 7


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


def _dim(value):
    try:
        dim = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value}") from None
    if not 0 <= dim <= 1:
        raise argparse.ArgumentTypeError(f"dim must be from 0 to 1, not {value}")
    return dim


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", default="~/config/planetario.env", help="file with ABS_URL and ABS_TOKEN")
    ap.add_argument("--theme", choices=sorted(THEMES), default="dark")
    ap.add_argument("--dim", type=_dim, default=1.0, help="brightness 0..1, as Home Assistant sends it")
    ap.add_argument("--badge", action="store_true", help="a message waiting from Jasmine")
    ap.add_argument("--dnd", action="store_true", help="quiet hours")
    ap.add_argument("--out", default="out")
    ap.add_argument("--fb-file", help="also write the frame here as 16 bpp framebuffer bytes and read it back")
    return ap.parse_args(argv)


def output_name(args):
    name = f"screen-{args.theme}"
    if args.badge:
        name += "-badge"
    if args.dnd:
        name += "-dnd"
    if args.dim < 1.0:
        name += "-dim"
    return name + ".png"


def make_badge(args):
    badge = dict(DEFAULT_BADGE)
    if args.badge:
        badge.update({"message_waiting": True, "from": "Jasmine", "count": 1})
    if args.dnd:
        badge["dnd"] = True
    return badge


def compose(client, covers, theme, badge, dim):
    """The screen estante-ctl would draw now, from the live Audiobookshelf."""
    cfg = dict(estante_ctl.DEFAULTS, walls=[dict(WALL)])
    wall = estante_ctl.resolve_walls(cfg, client)[0]
    nl = pick_now_listening(client)
    me = client.me()
    now_cover = None
    if nl:
        path = covers.path(nl.item_id, nl.updated_at, cfg["now_cover_width"])
        now_cover = load_image(path) if path else None
    local = datetime.now().astimezone()
    now_view = NowListeningView(nl, now_cover, finished_in_year(me, local.year, local.tzinfo), local.year, badge)
    books = build_shelf(client.library_items(wall.library_id), progress_index(me))
    width = cfg["cover_width"]
    loaded = {}
    for n, book in enumerate(books, 1):
        path = covers.path(book.item_id, book.updated_at, width)
        img = load_image(path, square=width) if path else None
        if img is not None:
            loaded[book.item_id] = img
        if n % 100 == 0:
            print(f"covers: {n} of {len(books)}", flush=True)
    print(f"{wall.title}: {len(books)} books, {len(loaded)} covers")
    wall_view = WallView(wall.title, wall.subtitle, books, loaded, shelf_counts(books))
    return render_screen(ScreenView(now_view, wall_view, theme, dim), SIZE)


def decode_rgb565(data, size):
    """RGB565 little-endian framebuffer bytes back to an RGB image (low bits zero)."""
    pair = Image.frombytes("LA", size, bytes(data))
    lo, hi = pair.getchannel("L"), pair.getchannel("A")
    r = hi.point([v & 0xF8 for v in range(256)])
    g = ImageChops.add(hi.point([(v & 0x07) << 5 for v in range(256)]), lo.point([(v >> 3) & 0x1C for v in range(256)]))
    b = lo.point([(v & 0x1F) << 3 for v in range(256)])
    return Image.merge("RGB", (r, g, b))


def fb_round_trip(img, path, size=SIZE, decode=decode_rgb565):
    """Write `img` through a 16 bpp FileFramebuffer, read it back; (ok, worst channel error)."""
    info = FbInfo(size[0], size[1], 16, size[0] * 2)
    fb = FileFramebuffer(path, info)
    try:
        fb.show(to_fb_bytes(img, info), info.size)
    finally:
        fb.close()
    with open(path, "rb") as f:
        data = f.read()
    frame = img.convert("RGB")
    if frame.size != size:
        frame = frame.resize(size, Image.Resampling.LANCZOS)
    back = decode(data, size)
    worst = max(hi for _, hi in ImageChops.difference(frame, back).getextrema())
    return worst <= ROUNDING, worst


def main(argv=None):
    args = parse_args(argv)
    try:
        url, token = creds_from_env(read_env(args.env))
    except (OSError, AbsError) as e:
        print(f"cannot read credentials from {args.env}: {e}", file=sys.stderr)
        return 2
    state_dir = os.environ.get("ESTANTE_STATE_DIR") or os.path.join(args.out, "state")
    client = AbsClient(url, token)
    covers = CoverCache(os.path.join(state_dir, "covers"), client)
    try:
        img = compose(client, covers, get_theme(args.theme), make_badge(args), args.dim)
    except (AbsError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 2
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, output_name(args))
    img.save(path)
    print(f"wrote {path}")
    if args.fb_file:
        ok, worst = fb_round_trip(img, args.fb_file)
        if not ok:
            print(f"fb round trip: FAILED (a channel is off by {worst})")
            return 1
        print(f"fb round trip: ok ({args.fb_file}, worst channel error {worst})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
