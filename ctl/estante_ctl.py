#!/usr/bin/env python3
"""estante-ctl: the conductor that draws Estante on the HDMI framebuffer.

One always-on composition: Now listening on the left, the cover wall of one
library on the right. Polls Audiobookshelf, answers Ouvido's /swap, /show and
/badge and Home Assistant's /screen, and writes finished frames to /dev/fb0.
Stdlib only here; Pillow lives behind estante_lib's render modules.
"""
import argparse
import copy
import json
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins"))

from estante_lib.abs import AbsClient, AbsError, CoverCache  # noqa: E402
from estante_lib.display_fb import FbInfo, FileFramebuffer, Framebuffer  # noqa: E402
from estante_lib.model import (  # noqa: E402
    build_shelf,
    finished_in_year,
    pick_now_listening,
    progress_index,
    shelf_counts,
)
from estante_lib.render_now import NowListeningView  # noqa: E402
from estante_lib.render_screen import (  # noqa: E402
    ScreenView,
    load_image,
    render_mark,
    render_screen,
    to_fb_bytes,
)
from estante_lib.render_wall import WallView  # noqa: E402
from estante_lib.state import normalize_badge, read_state, update_state  # noqa: E402
from estante_lib.theme import get_theme  # noqa: E402

log = logging.getLogger("estante-ctl")

DEFAULTS = {
    "listen": "0.0.0.0",
    "port": 8360,
    "abs_url": "",
    "abs_token": "",
    "theme": "dark",
    "walls": [{"name": "audiobooks", "library_id": "", "title": "The shelf"}],
    "poll_seconds": 60,
    "wall_refresh_minutes": 15,
    "state_dir": "/var/lib/estante",
    "canvas": [1920, 1080],
    "rotate": 0,
    "fb_device": "/dev/fb0",
    "fb_sysfs": "/sys/class/graphics/fb0",
    "fb_file": "",
    "fb_size": [1920, 1080],
    "fb_bpp": 16,
    "cover_width": 120,
    "now_cover_width": 520,
}

WALL_TITLES = {"audiobooks": "The shelf", "light_novels": "Light novels"}
ACTIONS = ("swap", "show", "redraw")
TICK_S = 0.5
COVER_LOG_EVERY = 50
# three misses in a row that each took this long: ABS is hanging, so the
# cover load stops until the next wall refresh
COVER_SLOW_S = 2.0
COVER_SLOW_LIMIT = 3
# the largest request body read; Ouvido and HA send a few dozen bytes
MAX_BODY = 65536
DEFAULT_SCREEN = {"on": True, "dim": 1.0}


def load_config(path=None):
    cfg = copy.deepcopy(DEFAULTS)
    path = path or os.environ.get("ESTANTE_CONFIG")
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
        except FileNotFoundError:
            log.warning("config %s not found, using defaults", path)
            loaded = {}
        if not isinstance(loaded, dict):
            raise ValueError(f"config {path} must be a JSON object")
        cfg.update(loaded)
    if cfg["rotate"] not in (0, 180):
        raise ValueError(f"rotate must be 0 or 180, not {cfg['rotate']!r}")
    _check_walls(cfg["walls"])
    get_theme(cfg["theme"])
    return cfg


def _check_walls(walls):
    if not isinstance(walls, list) or not walls:
        raise ValueError(f"walls must be a non-empty list of {{name, library_id}}, not {walls!r}")
    for entry in walls:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"]:
            raise ValueError(f"every entry of walls needs a non-empty name, not {entry!r}")


def state_file(cfg):
    return os.path.join(cfg["state_dir"], "state.json")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(name):
    return (name or "").strip().lower().replace("_", " ")


@dataclass
class Wall:
    name: str
    library_id: str
    title: str
    library_name: str = ""

    @property
    def subtitle(self):
        return f"{self.library_name}, by author"


def walls_from_config(cfg):
    walls = []
    for entry in cfg["walls"]:
        name = entry["name"]
        title = entry.get("title") or WALL_TITLES.get(name) or name.replace("_", " ").capitalize()
        walls.append(Wall(name, entry.get("library_id") or "", title))
    return walls


def resolve_walls(cfg, client):
    """The configured walls with every library id and library display name filled in from ABS."""
    libraries = client.libraries()
    known = ", ".join(f"{lib.get('name')} ({lib.get('id')})" for lib in libraries)
    walls = []
    for wall in walls_from_config(cfg):
        if wall.library_id:
            match = [lib for lib in libraries if lib.get("id") == wall.library_id]
        else:
            match = [lib for lib in libraries if _norm(lib.get("name")) == _norm(wall.name)]
        if not match:
            wanted = wall.library_id or wall.name
            raise ValueError(f"no Audiobookshelf library for wall {wall.name!r} ({wanted}); libraries: {known}")
        lib = match[0]
        walls.append(Wall(wall.name, lib["id"], wall.title, lib.get("name") or ""))
    return walls


def open_framebuffer(cfg):
    """The real /dev/fb0, or with `fb_file` a regular file shaped like `fb_size` at `fb_bpp`."""
    if cfg.get("fb_file"):
        w, h = (int(v) for v in cfg["fb_size"])
        bpp = int(cfg["fb_bpp"])
        return FileFramebuffer(cfg["fb_file"], FbInfo(w, h, bpp, w * bpp // 8))
    return Framebuffer(cfg["fb_device"], cfg["fb_sysfs"])


def make_client(cfg):
    if not cfg.get("abs_url"):
        return None
    return AbsClient(cfg["abs_url"], cfg.get("abs_token") or "")


class Conductor(threading.Thread):
    def __init__(self, cfg, client, fb, state_path, clock=time.monotonic):
        super().__init__(name="estante-conductor", daemon=True)
        self.cfg = cfg
        self.client = client
        self.fb = fb
        self.state_path = state_path
        self.clock = clock
        self.theme = get_theme(cfg["theme"])
        self.canvas = tuple(int(v) for v in cfg["canvas"])
        self.covers = CoverCache(os.path.join(cfg["state_dir"], "covers"), client) if client else None
        self.render_count = 0
        self._lock = threading.Lock()
        # held around every write to the framebuffer and every blank, so a
        # screen-off can never be followed by a frame that was already rendering
        self._fb_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._walls = walls_from_config(cfg)
        self._resolved = False
        state = read_state(state_path)
        names = [w.name for w in self._walls]
        # _target is the wall asked for; _wall is the wall whose books were last
        # fetched, the one on screen. They differ while a swap waits for ABS.
        self._target = names.index(state["wall"]) if state.get("wall") in names else 0
        self._wall = self._target
        self._screen = self._screen_from_state(state.get("screen"))
        self._pending = None
        self._busy = False
        self._last_render = None
        self._next_poll = 0.0
        # what the last poll saw
        self._nl = None
        self._me = None
        self._books = None
        self._items = None
        self._items_wall = None
        self._items_at = None
        self._shown = None
        self._abs_problem = None
        self._render_problem = None
        self._covers_stalled = False
        # covers of the current wall only, so a swap frees the other wall's images
        self._wall_covers = {}
        self._covers_wall = None
        self._cover_misses = set()
        self._now_cover = None

    @staticmethod
    def _screen_from_state(saved):
        screen = dict(DEFAULT_SCREEN)
        if isinstance(saved, dict):
            if isinstance(saved.get("on"), bool):
                screen["on"] = saved["on"]
            dim = saved.get("dim")
            if isinstance(dim, (int, float)) and not isinstance(dim, bool) and 0 <= dim <= 1:
                screen["dim"] = float(dim)
        return screen

    # public API, called from HTTP threads

    def wall_names(self):
        with self._lock:
            return [w.name for w in self._walls]

    def _resolve(self, wanted):
        with self._lock:
            walls = list(self._walls)
        for i, wall in enumerate(walls):
            if wall.name == wanted or _norm(wall.name) == _norm(wanted):
                return i
        for i, wall in enumerate(walls):
            if wall.library_id and wall.library_id == wanted:
                return i
        raise KeyError(wanted)

    def request(self, action, screen=None):
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action}")
        index = self._resolve(screen) if action == "show" else None
        with self._lock:
            if action == "swap" and len(self._walls) < 2:
                action = "redraw"  # one wall: nothing to swap to, so just draw it again
            # user actions (swap, show) replace each other and a pending redraw;
            # a redraw never replaces a pending swap or show, which redraws anyway
            if action != "redraw" or self._pending is None or self._pending[0] == "redraw":
                self._pending = (action, index)
            name = self._walls[index].name if index is not None else None
        if action == "show":
            return {"queued": action, "wall": name}
        return {"queued": action}

    def set_badge(self, incoming):
        result = {}

        def mutate(state):
            current = state.get("badge") or {}
            badge = normalize_badge(current, incoming)
            result["changed"] = badge != normalize_badge(current, {})
            result["badge"] = badge
            state["badge"] = badge

        update_state(mutate, self.state_path)
        if result["changed"]:
            self.request("redraw")
        return result["badge"], result["changed"]

    def set_screen(self, on=None, dim=None):
        """Power and brightness from Home Assistant; persisted so a restart keeps them."""
        with self._fb_lock:
            with self._lock:
                was = dict(self._screen)
                if on is not None:
                    self._screen["on"] = bool(on)
                if dim is not None:
                    self._screen["dim"] = float(dim)
                screen = dict(self._screen)
            if on is False:
                self._power_down()
            elif on is True:
                self.fb.blank(False)
        try:
            update_state(lambda state: state.__setitem__("screen", dict(screen)), self.state_path)
        except OSError as e:
            # the panel already changed: answer for it, a restart falls back to the saved state
            log.warning("cannot record the screen state: %s", e)
        if screen["on"] and (screen["on"] != was["on"] or screen["dim"] != was["dim"]):
            self.request("redraw")
        return screen

    def snapshot(self):
        state = read_state(self.state_path)
        with self._lock:
            nl = self._nl
            return {
                "wall": self._walls[self._wall].name,
                "walls": [w.name for w in self._walls],
                "badge": state.get("badge"),
                "screen": dict(self._screen),
                "busy": self._busy,
                "last_render": self._last_render,
                "now_listening": (
                    {"item_id": nl.item_id, "title": nl.title, "pct": int(nl.pct * 100)} if nl else None
                ),
            }

    def stop(self):
        self._stop_event.set()

    # framebuffer, under _fb_lock

    def _power_down(self):
        if not self.fb.blank(True):
            info = self.fb.info
            self.fb.show(bytes(info.width * info.height * info.bytes_per_pixel), info.size)

    def _blit(self, img):
        """Write `img` unless the screen is off by the time it is ready; True when written."""
        if self.cfg["rotate"] == 180:
            img = img.rotate(180)
        pixels = to_fb_bytes(img, self.fb.info)
        with self._fb_lock:
            with self._lock:
                on = self._screen["on"]
            if not on:
                return False
            self.fb.show(pixels, self.fb.info.size)
        return True

    # conductor thread

    def _screen_on(self):
        with self._lock:
            return self._screen["on"]

    def _abs_failed(self, problem):
        if problem != self._abs_problem:
            log.warning("Audiobookshelf unavailable, keeping the last frame: %s", problem)
            self._abs_problem = problem

    def _abs_ok(self):
        if self._abs_problem is not None:
            log.info("Audiobookshelf answers again")
            self._abs_problem = None

    def _current_wall(self):
        with self._lock:
            return self._wall, self._walls[self._wall]

    def _fetch(self, now, force_items=False):
        """One round of ABS reads; True when what the screen shows has changed."""
        if self.client is None:
            raise AbsError("abs_url is not set")
        if not self._resolved:
            walls = resolve_walls(self.cfg, self.client)
            with self._lock:
                self._walls = walls
            self._resolved = True
        with self._lock:
            index = self._target
            wall = self._walls[index]
        nl = pick_now_listening(self.client)
        me = self.client.me()
        refresh = self.cfg["wall_refresh_minutes"] * 60
        items_due = (
            force_items
            or self._items is None
            or self._items_wall != index
            or now - self._items_at >= refresh
        )
        items = self.client.library_items(wall.library_id) if items_due else self._items
        books = build_shelf(items, progress_index(me))
        # every read succeeded: only now does the screen move to the wall asked for
        if items_due:
            self._items = items
            self._items_wall = index
            self._items_at = now
            self._cover_misses.clear()
            self._covers_stalled = False
            if self._now_cover is not None and self._now_cover[1] is None:
                self._now_cover = None
        with self._lock:
            self._nl = nl
            self._wall = index
        self._me = me
        self._books = books
        shown = (
            index,
            (nl.item_id, nl.last_update, nl.finished) if nl else None,
            tuple((b.item_id, b.status, b.updated_at) for b in books),
        )
        changed = shown != self._shown or items_due
        self._shown = shown
        return changed

    def _poll(self, now, force_items=False, force_render=False):
        with self._lock:
            self._next_poll = now + self.cfg["poll_seconds"]
            self._busy = True
        try:
            try:
                changed = self._fetch(now, force_items)
            except (AbsError, ValueError, KeyError, TypeError, AttributeError) as e:
                self._abs_failed(str(e) or type(e).__name__)
                return
            self._abs_ok()
            if changed or force_render:
                self._render_guarded()
        finally:
            with self._lock:
                self._busy = False

    def _render_guarded(self):
        """Render, logging a failure once per distinct error (traceback the first time)."""
        try:
            self._render()
        except Exception as e:
            self._shown = None  # the next poll tries this frame again
            problem = f"{type(e).__name__}: {e}"
            if problem != self._render_problem:
                log.exception("render failed, keeping the last frame")
                self._render_problem = problem
            return
        if self._render_problem is not None:
            log.info("rendering works again")
            self._render_problem = None

    def _load_wall_covers(self, index, books):
        if self._covers_wall != index:
            self._wall_covers = {}
            self._covers_wall = index
        width = self.cfg["cover_width"]
        todo = [
            b for b in books
            if (self._wall_covers.get(b.item_id) or (None,))[0] != b.updated_at
            and (b.item_id, b.updated_at) not in self._cover_misses
        ]
        if self._covers_stalled:
            todo = []
        slow_misses = 0
        for n, book in enumerate(todo, 1):
            if self._stop_event.is_set():
                break
            started = time.monotonic()
            path = self.covers.path(book.item_id, book.updated_at, width)
            img = load_image(path, square=width) if path else None
            if img is None:
                self._cover_misses.add((book.item_id, book.updated_at))
                self._wall_covers.pop(book.item_id, None)
                slow_misses = slow_misses + 1 if time.monotonic() - started > COVER_SLOW_S else 0
                if slow_misses >= COVER_SLOW_LIMIT:
                    log.warning(
                        "covers: %d slow misses in a row, Audiobookshelf seems to hang; "
                        "the rest wait for the next wall refresh", slow_misses,
                    )
                    self._covers_stalled = True
                    break
            else:
                slow_misses = 0
                self._wall_covers[book.item_id] = (book.updated_at, img)
            if n % COVER_LOG_EVERY == 0:
                log.info("covers: %d of %d loaded", n, len(todo))
        return {item_id: img for item_id, (_, img) in self._wall_covers.items()}

    def _load_now_cover(self, nl):
        if nl is None:
            return None
        key = (nl.item_id, nl.updated_at)
        if self._now_cover is None or self._now_cover[0] != key:
            path = self.covers.path(nl.item_id, nl.updated_at, self.cfg["now_cover_width"])
            self._now_cover = (key, load_image(path) if path else None)
        return self._now_cover[1]

    def _render(self):
        """Compose the screen from the last poll and blit it; the mark until ABS has answered.

        With the screen off the covers are still fetched, so turning it on draws at once.
        """
        if self._books is None:
            if self._screen_on():
                self._blit(render_mark(self.theme, self.canvas))
            return
        index, wall = self._current_wall()
        with self._lock:
            nl = self._nl
        now_cover = self._load_now_cover(nl)
        wall_covers = self._load_wall_covers(index, self._books)
        if not self._screen_on():
            return
        with self._lock:
            dim = self._screen["dim"]
        badge = read_state(self.state_path).get("badge")
        local = datetime.now().astimezone()
        now_view = NowListeningView(
            nl,
            now_cover,
            finished_in_year(self._me, local.year, local.tzinfo),
            local.year,
            badge,
        )
        wall_view = WallView(
            wall.title, wall.subtitle, self._books, wall_covers, shelf_counts(self._books),
        )
        img = render_screen(ScreenView(now_view, wall_view, self.theme, dim), self.canvas)
        if self._blit(img):
            with self._lock:
                self._last_render = now_iso()
                self.render_count += 1

    def _set_wall(self, index):
        """Aim at wall `index`; the screen moves to it on the next successful fetch."""
        with self._lock:
            self._target = index
            name = self._walls[index].name
        log.info("wall: %s", name)
        try:
            update_state(lambda state: state.__setitem__("wall", name), self.state_path)
        except OSError as e:
            log.warning("cannot record the wall: %s", e)

    def _run_pending(self, action, index):
        now = self.clock()
        if action == "swap":
            with self._lock:
                index = (self._target + 1) % len(self._walls)
        if action in ("swap", "show"):
            self._set_wall(index)
            self._poll(now, force_items=True, force_render=True)
            return
        with self._lock:
            self._busy = True
        try:
            self._render_guarded()
        finally:
            with self._lock:
                self._busy = False

    def _boot(self):
        with self._fb_lock:
            on = self._screen_on()
            if on:
                # wake a panel the console left blanked; a refusal is fine, the frame follows
                self.fb.blank(False)
            else:
                self._power_down()
        if on:
            self._blit(render_mark(self.theme, self.canvas))

    def _tick(self):
        with self._lock:
            pending, self._pending = self._pending, None
            next_poll = self._next_poll
        now = self.clock()
        if pending:
            self._run_pending(*pending)
        elif now >= next_poll:
            self._poll(now)

    def run(self):
        try:
            self._run()
        except Exception:  # logged here; main sees the dead thread and exits non-zero
            log.exception("conductor crashed")

    def _run(self):
        self._boot()
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:  # keep the screen alive whatever one step does
                log.exception("conductor step failed")
                with self._lock:
                    self._busy = False
            self._stop_event.wait(TICK_S)


class BadRequest(ValueError):
    pass


class BodyTooLarge(ValueError):
    pass


def parse_screen(data):
    """(on, dim) from a /screen body; BadRequest when either is missing its type or range."""
    if not isinstance(data, dict):
        raise BadRequest("screen must be a JSON object")
    on = data.get("on")
    dim = data.get("dim")
    if on is not None and not isinstance(on, bool):
        raise BadRequest("on must be true or false")
    if dim is not None:
        if isinstance(dim, bool) or not isinstance(dim, (int, float)) or not 0 <= dim <= 1:
            raise BadRequest("dim must be a number from 0 to 1")
    return on, dim


def make_handler(conductor):
    routes = {"/swap": "POST", "/show": "POST", "/badge": "POST", "/screen": "POST", "/state": "GET"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "estante-ctl"

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        def _send(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise BodyTooLarge(length)
            raw = self.rfile.read(length) if length > 0 else b""
            return json.loads(raw) if raw.strip() else {}

        def _dispatch(self, method):
            path = self.path.split("?", 1)[0]
            if path not in routes:
                self._send(404, {"error": "not found"})
                return
            if routes[path] != method:
                self._send(405, {"error": "method not allowed"})
                return
            if path == "/state":
                self._send(200, conductor.snapshot())
                return
            if path == "/swap":
                self._send(202, conductor.request("swap"))
                return
            try:
                data = self._json_body()
            except BodyTooLarge:
                # the body stays unread, so this connection cannot carry another request
                self.close_connection = True
                self._send(413, {"error": "body too large"})
                return
            except ValueError:
                self._send(400, {"error": "bad JSON"})
                return
            if path == "/show":
                self._show(data)
            elif path == "/badge":
                self._badge(data)
            else:
                self._screen(data)

        def _show(self, data):
            screen = data.get("screen") if isinstance(data, dict) else None
            if not isinstance(screen, str) or not screen:
                self._send(400, {"error": "missing field: screen"})
                return
            try:
                self._send(202, conductor.request("show", screen))
            except KeyError:
                self._send(404, {"error": "unknown wall", "walls": conductor.wall_names()})

        def _badge(self, data):
            if not isinstance(data, dict):
                self._send(400, {"error": "badge must be a JSON object"})
                return
            badge, changed = conductor.set_badge(data)
            self._send(202, {"badge": badge, "redraw": changed})

        def _screen(self, data):
            try:
                on, dim = parse_screen(data)
            except BadRequest as e:
                self._send(400, {"error": str(e)})
                return
            self._send(202, {"screen": conductor.set_screen(on=on, dim=dim)})

        def _guarded(self, method):
            try:
                self._dispatch(method)
            except Exception as e:
                log.exception("%s %s failed", method, self.path)
                self._send(500, {"error": str(e) or type(e).__name__})

        def do_GET(self):
            self._guarded("GET")

        def do_POST(self):
            self._guarded("POST")

        def do_PUT(self):
            self._guarded("PUT")

        def do_PATCH(self):
            self._guarded("PATCH")

        def do_DELETE(self):
            self._guarded("DELETE")

    return Handler


def serve(cfg, conductor):
    server = ThreadingHTTPServer((cfg["listen"], int(cfg["port"])), make_handler(conductor))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="estante-http", daemon=True).start()
    return server


def wait_for_exit(done, conductor, interval=1.0):
    """Block until `done` is set (0) or the conductor thread dies on its own (1)."""
    while not done.wait(interval):
        if not conductor.is_alive():
            log.error("conductor thread died, exiting so systemd restarts the service")
            return 1
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Estante conductor")
    parser.add_argument("--config", help="JSON config (default: $ESTANTE_CONFIG)")
    args = parser.parse_args(argv)
    logging.basicConfig(
        stream=sys.stdout, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    cfg = load_config(args.config)
    fb = open_framebuffer(cfg)
    log.info("framebuffer %sx%s at %s bpp, canvas %sx%s", *fb.info.size, fb.info.bpp, *cfg["canvas"])
    conductor = Conductor(cfg, make_client(cfg), fb, state_file(cfg))
    done = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    signal.signal(signal.SIGINT, lambda *_: done.set())
    conductor.start()
    server = serve(cfg, conductor)
    log.info("listening on %s:%s", cfg["listen"], cfg["port"])
    code = wait_for_exit(done, conductor)
    log.info("stopping")
    server.shutdown()
    server.server_close()
    conductor.stop()
    conductor.join(timeout=10)
    if conductor.is_alive():
        # still blocked in an ABS call: closing the fd under it could let a late
        # blit land on whatever reuses the descriptor; the process exit closes it
        log.warning("conductor still busy after 10 s, leaving the framebuffer open")
    else:
        fb.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
