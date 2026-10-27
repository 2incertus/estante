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
from estante_lib.model import finished_in_year, pick_now_listening  # noqa: E402
from estante_lib.palette import PREVIEW, driver_palette, inky_pass, to_rgb  # noqa: E402
from estante_lib.render_now import NowListeningView, render_now_listening  # noqa: E402
from estante_lib.state import DEFAULT_BADGE, state_dir  # noqa: E402

NOW_COVER_WIDTH = 400


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
    ap.add_argument("--page", type=int, default=0)
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
        name, img = render_now(client, covers, badge)
    except AbsError as e:
        print(str(e), file=sys.stderr)
        return 2
    if args.badge or args.dnd:
        name += "-badge"
    os.makedirs(args.out, exist_ok=True)
    return 0 if write_and_check(img, name, args.out) else 1


if __name__ == "__main__":
    sys.exit(main())
