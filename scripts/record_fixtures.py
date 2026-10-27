#!/usr/bin/env python3
"""Record sanitized Audiobookshelf fixtures for the Estante tests.

Issues GET requests only. The token is read from the --env file and is never
printed or written anywhere. Every key in SANITIZE_KEYS is deleted from every
recorded document before it is saved.

Usage:
    python3 scripts/record_fixtures.py --env ~/config/planetario.env --out tests/fixtures
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

AUDIOBOOKS_ID = "b14de8f5-99a5-4cba-b970-6a2cb65f2993"
LIGHT_NOVELS_ID = "65bcd45d-1cce-496c-950e-4f3aabf5f5eb"
JADE_CITY_ID = "616bf0b9-2c0c-472a-b975-72f35077d199"
AUDIOBOOK_COVERS = 12
LIGHT_NOVEL_COVERS = 6

SANITIZE_KEYS = {
    "token", "email", "path", "relPath", "ino", "folderId", "libraryFiles",
    "audioFiles", "tracks", "description", "descriptionPlain", "permissions",
    "deviceInfo", "ipAddress",
}

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES_ROOT = os.path.join(REPO, "tests", "fixtures")


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


def sanitize(node):
    if isinstance(node, dict):
        return {k: sanitize(v) for k, v in node.items() if k not in SANITIZE_KEYS}
    if isinstance(node, list):
        return [sanitize(v) for v in node]
    return node


class Getter:
    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.count = 0

    def get(self, path, params=None):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url, method="GET", headers={"Authorization": "Bearer " + self._token}
        )
        self.count += 1
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise SystemExit(f"GET {path} failed with HTTP {e.code}")

    def get_json(self, path, params=None):
        body = self.get(path, params)
        if body is None:
            raise SystemExit(f"GET {path} returned 404")
        return json.loads(body)


def series_order(series_name):
    m = re.match(r"^(.*?)\s*#\s*([0-9]+(?:\.[0-9]+)?)", series_name or "")
    if m:
        return (m.group(1).strip().lower(), float(m.group(2)))
    return ((series_name or "").lower(), 0.0)


def shelf_order(item):
    # Mirrors estante_lib.model.shelf_sort_key; kept local so this script
    # stays a standalone tool.
    md = item["media"]["metadata"]
    author = (md.get("authorNameLF") or "").lower() or "\uffff"
    title = (md.get("titleIgnorePrefix") or md.get("title") or "").lower()
    return (author, series_order(md.get("seriesName") or ""), title)


def save_json(out, name, payload):
    with open(os.path.join(out, name), "w", encoding="utf-8", newline="\n") as f:
        json.dump(sanitize(payload), f, ensure_ascii=True, indent=1)
        f.write("\n")


def save_cover(getter, out, item_id, width, name):
    body = getter.get(f"/api/items/{item_id}/cover", {"width": width, "format": "jpeg"})
    if body is None:
        print(f"no cover for {item_id}")
        return
    with open(os.path.join(out, "covers", name), "wb") as f:
        f.write(body)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", required=True, help="file holding ABS_URL and ABS_TOKEN")
    ap.add_argument("--out", required=True, help="directory under tests/fixtures")
    args = ap.parse_args(argv)

    out = os.path.realpath(args.out)
    root = os.path.realpath(FIXTURES_ROOT)
    if out != root and not out.startswith(root + os.sep):
        print(f"refusing: --out must be under {root}", file=sys.stderr)
        return 2

    env = read_env(args.env)
    if not env.get("ABS_URL") or not env.get("ABS_TOKEN"):
        print("refusing: ABS_URL and ABS_TOKEN are not set in --env", file=sys.stderr)
        return 2
    getter = Getter(env["ABS_URL"], env["ABS_TOKEN"])
    os.makedirs(os.path.join(out, "covers"), exist_ok=True)

    save_json(out, "libraries.json", getter.get_json("/api/libraries")["libraries"])

    shelves = {}
    for lib_id, name in ((AUDIOBOOKS_ID, "audiobooks.json"), (LIGHT_NOVELS_ID, "light_novels.json")):
        data = getter.get_json(f"/api/libraries/{lib_id}/items", {"limit": 0, "minified": 1})
        save_json(out, name, data)
        shelves[lib_id] = sorted(data["results"], key=shelf_order)

    me = getter.get_json("/api/me")
    save_json(out, "me.json", me)
    in_progress = getter.get_json("/api/me/items-in-progress", {"limit": 3})
    save_json(out, "in_progress.json", in_progress)

    detail_ids = []
    if in_progress.get("libraryItems"):
        detail_ids.append(in_progress["libraryItems"][0]["id"])
    detail_ids.append(JADE_CITY_ID)
    # The latest finished book backs the "LAST LISTENED" fallback.
    finished = [p for p in me.get("mediaProgress", []) if p.get("isFinished")]
    if finished:
        last = max(finished, key=lambda p: (p.get("finishedAt") or 0, p.get("lastUpdate") or 0))
        if last["libraryItemId"] not in detail_ids:
            detail_ids.append(last["libraryItemId"])
    for item_id in detail_ids:
        save_json(out, f"item_{item_id}.json", getter.get_json(f"/api/items/{item_id}"))
        prog = getter.get(f"/api/me/progress/{item_id}")
        if prog is not None:
            save_json(out, f"progress_{item_id}.json", json.loads(prog))

    picks = shelves[AUDIOBOOKS_ID][:AUDIOBOOK_COVERS] + shelves[LIGHT_NOVELS_ID][:LIGHT_NOVEL_COVERS]
    for item in picks:
        save_cover(getter, out, item["id"], 80, f"{item['id']}.jpg")
    if in_progress.get("libraryItems"):
        first = in_progress["libraryItems"][0]["id"]
        save_cover(getter, out, first, 400, f"{first}-w400.jpg")

    print(f"recorded fixtures into {out} with {getter.count} GET requests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
