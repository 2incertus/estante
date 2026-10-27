#!/usr/bin/env python3
"""estante-ctl: the conductor that decides what the Estante panel shows.

Rotates the playlist screens through InkyPi, answers the Ouvido daemon's
/swap, /show and /badge calls, and redraws Now listening when what it shows
has visibly changed. Stdlib only; runs on the Pi's system Python.
"""
import argparse
import http.client
import json
import logging
import os
import re
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins"))

from estante_lib.abs import AbsClient  # noqa: E402
from estante_lib.model import pick_now_listening, visible_key  # noqa: E402
from estante_lib.state import normalize_badge, read_state, update_state  # noqa: E402

log = logging.getLogger("estante-ctl")

DEFAULTS = {
    "listen": "127.0.0.1",
    "port": 8360,
    "inkypi_url": "http://127.0.0.1:80",
    "inkypi_device_json": "/usr/local/inkypi/src/config/device.json",
    # Empty: derived from the real location of InkyPi's src (see load_config).
    "inkypi_env": "",
    "playlist": "Default",
    "rotate_minutes": 30,
    "poll_minutes": 5,
    "min_redraw_minutes": 10,
    "state_dir": "/var/lib/estante",
}

NOW_LISTENING = "now_listening"
THE_SHELF = "the_shelf"
TICK_S = 0.2
READY_RETRY_S = 5
READY_LOG_S = 60
SCREENS_RETRY_S = 30
FAILED_DISPLAY_RETRY_S = 60
# a cold mosaic render fetches up to 900 covers before InkyPi answers
DISPLAY_TIMEOUT_S = 600


def load_config(path=None):
    cfg = dict(DEFAULTS)
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
    if not cfg.get("inkypi_env"):
        cfg["inkypi_env"] = derive_inkypi_env(cfg["inkypi_device_json"])
    return cfg


def derive_inkypi_env(device_json):
    """InkyPi's .env: the checkout root next to the real src, as InkyPi itself finds it.

    device.json lives at <src>/config/device.json and /usr/local/inkypi/src is a symlink
    to the checkout's src, so two levels up through the real path is the checkout root.
    """
    root = os.path.realpath(os.path.join(os.path.dirname(device_json), "..", ".."))
    return os.path.join(root, ".env")


def parse_env_file(path):
    env = {}
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return env
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        env[key.strip()] = value
    return env


def slug(name):
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Screen:
    plugin_id: str
    name: str
    slug: str
    settings: dict


class InkyPi:
    def __init__(self, url):
        self.url = url.rstrip("/")

    def ready(self):
        try:
            with urllib.request.urlopen(self.url + "/", timeout=5) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def display(self, playlist, plugin_id, instance):
        body = json.dumps(
            {"playlist_name": playlist, "plugin_id": plugin_id, "plugin_instance": instance}
        ).encode()
        req = urllib.request.Request(
            self.url + "/display_plugin_instance",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=DISPLAY_TIMEOUT_S) as resp:
                resp.read()
                return 200 <= resp.status < 300
        except urllib.error.HTTPError as e:
            log.warning("InkyPi display %s failed: HTTP %s", instance, e.code)
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
            log.warning("InkyPi display %s failed: %s", instance, e)
        except Exception:  # never let a display error escape into the conductor
            log.exception("InkyPi display %s failed", instance)
        return False

    def screens(self, device_json_path, playlist):
        data = None
        for attempt in (1, 2):
            try:
                with open(device_json_path, encoding="utf-8") as f:
                    data = json.load(f)
                break
            except json.JSONDecodeError:
                if attempt == 2:
                    log.warning("%s is not valid JSON", device_json_path)
                    return []
                time.sleep(0.5)
            except OSError as e:
                log.warning("cannot read %s: %s", device_json_path, e)
                return []
        playlists = ((data or {}).get("playlist_config") or {}).get("playlists") or []
        for pl in playlists:
            if pl.get("name") == playlist:
                return [
                    Screen(
                        plugin_id=p.get("plugin_id") or "",
                        name=p.get("name") or "",
                        slug=slug(p.get("name") or ""),
                        settings=dict(p.get("plugin_settings") or {}),
                    )
                    for p in pl.get("plugins") or []
                ]
        return []


def default_abs_factory(cfg):
    def factory():
        env = parse_env_file(cfg["inkypi_env"])
        if not env.get("ABS_URL") or not env.get("ABS_TOKEN"):
            return None
        return AbsClient(env["ABS_URL"], env["ABS_TOKEN"])

    return factory


class Conductor(threading.Thread):
    def __init__(self, cfg, inkypi, abs_factory, state_path, clock=time.monotonic):
        super().__init__(name="estante-conductor", daemon=True)
        self.cfg = cfg
        self.inkypi = inkypi
        self.abs_factory = abs_factory
        self.state_path = state_path
        self.clock = clock
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._screens = []
        self._current = None
        # the rotation's last attempted screen: the next turn and /swap go one past it
        self._cursor = None
        self._pending = None
        self._busy = False
        self._next_due = None
        self._next_poll = None
        self._nl_key = None
        self._nl_shown_at = None
        self._abs = None
        self._abs_enabled = None
        self._booted = False

    # public API, called from HTTP threads

    def _resolve(self, screen):
        with self._lock:
            screens = list(self._screens)
        if not screens:
            screens = self._load_screens()
        wanted = str(screen)
        for match in (
            lambda s: s.name == wanted,
            lambda s: s.name.lower() == wanted.lower(),
            lambda s: s.slug == wanted or s.slug == slug(wanted),
        ):
            for s in screens:
                if match(s):
                    return s.name
        raise KeyError([s.name for s in screens])

    def screen_names(self):
        with self._lock:
            screens = list(self._screens)
        return [s.name for s in screens or self._load_screens()]

    def request(self, action, screen=None):
        if action not in ("swap", "show", "redraw"):
            raise ValueError(f"unknown action {action}")
        name = self._resolve(screen) if screen is not None else None
        with self._lock:
            # user actions (swap, show) replace each other and a pending redraw;
            # a redraw never replaces a pending swap or show, which draws the badge anyway
            if action != "redraw" or self._pending is None or self._pending[0] == "redraw":
                self._pending = (action, name)
        return {"queued": action, "screen": name}

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

    def snapshot(self):
        state = read_state(self.state_path)
        with self._lock:
            current = self._screens[self._current].name if self._current is not None else None
            names = [s.name for s in self._screens]
            next_due = self._next_due
            busy = self._busy
        next_rotation = None
        if next_due is not None:
            wall = datetime.now(timezone.utc) + timedelta(seconds=max(0.0, next_due - self.clock()))
            next_rotation = wall.isoformat(timespec="seconds")
        return {
            "screen": current,
            "screens": names,
            "badge": state.get("badge"),
            "busy": busy,
            "last_display": state.get("display") or None,
            "next_rotation": next_rotation,
            "rotate_minutes": self.cfg["rotate_minutes"],
        }

    def stop(self):
        self._stop_event.set()

    # conductor thread

    def _load_screens(self):
        return self.inkypi.screens(self.cfg["inkypi_device_json"], self.cfg["playlist"])

    def _refresh_screens(self):
        fresh = self._load_screens()
        if not fresh:
            return
        with self._lock:
            name = self._screens[self._current].name if self._current is not None else None
            cursor = self._screens[self._cursor].name if self._cursor is not None else None
            self._screens = fresh
            names = [s.name for s in fresh]
            self._current = names.index(name) if name in names else None
            if cursor in names:
                self._cursor = names.index(cursor)
            else:
                self._cursor = self._current

    def _current_abs(self):
        """A fresh ABS client from InkyPi's .env, so a key edited after boot is picked up."""
        try:
            client = self.abs_factory()
        except Exception as e:  # a broken .env only disables the watch
            log.warning("cannot build the ABS client: %s", e)
            client = None
        enabled = client is not None
        if enabled != self._abs_enabled:
            if enabled:
                log.info("ABS credentials found: Now-listening watch enabled")
            else:
                log.info(
                    "ABS_URL or ABS_TOKEN missing in %s: Now-listening watch disabled",
                    self.cfg["inkypi_env"],
                )
            self._abs_enabled = enabled
        self._abs = client
        return client

    def _now_listening_key(self):
        if self._current_abs() is None:
            return None
        badge = read_state(self.state_path).get("badge")
        try:
            return visible_key(pick_now_listening(self._abs), badge)
        except Exception as e:  # any ABS failure only skips this check
            log.warning("now-listening check failed: %s", e)
            return None

    def _advance_page(self, screen):
        """Turn the shelf page; returns a function that turns it back, or None."""
        lib = screen.settings.get("library_id")
        if not lib:
            return None
        before = {}

        def mutate(state):
            pages = state.setdefault("pages", {})
            if lib in pages:
                before["page"] = pages[lib]
                total = max(1, int(state.get("page_counts", {}).get(lib, 1) or 1))
                pages[lib] = (int(pages[lib]) + 1) % total
            else:
                pages[lib] = 0

        update_state(mutate, self.state_path)

        def restore(state):
            pages = state.setdefault("pages", {})
            if "page" in before:
                pages[lib] = before["page"]
            else:
                pages.pop(lib, None)

        return lambda: update_state(restore, self.state_path)

    def _show(self, index, advance, retry=False):
        """Display screen `index`. On failure the rotation backs off and moves past it,
        or, with `retry` (a /show or the first screen), comes back to it next turn."""
        # busy is set before any state or ABS work and stays True until the
        # bookkeeping is done, so a reader that sees busy False also sees the
        # new screen and next rotation
        with self._lock:
            screen = self._screens[index]
            self._busy = True
        ok = False
        undo = None
        try:
            if advance and screen.plugin_id == THE_SHELF:
                try:
                    undo = self._advance_page(screen)
                except Exception as e:  # the page just does not turn
                    log.warning("cannot turn the page of %s: %s", screen.name, e)
            key = self._now_listening_key() if screen.plugin_id == NOW_LISTENING else None
            ok = self._display_and_record(index, screen, key)
        except Exception:
            log.exception("showing %s failed", screen.name)
        finally:
            if not ok:
                self._failed(index, retry, undo)
            with self._lock:
                self._busy = False
        return ok

    def _failed(self, index, retry, undo):
        if undo is not None:
            try:
                undo()  # the panel did not change, so neither does the page
            except Exception as e:
                log.warning("cannot turn the page back: %s", e)
        with self._lock:
            self._next_due = self.clock() + min(FAILED_DISPLAY_RETRY_S, self.cfg["rotate_minutes"] * 60)
            n = len(self._screens)
            self._cursor = (index - 1) % n if retry else index

    def _display_and_record(self, index, screen, key):
        ok = self.inkypi.display(self.cfg["playlist"], screen.plugin_id, screen.name)
        if not ok:
            return False
        now = self.clock()
        log.info("showing %s", screen.name)
        with self._lock:
            self._current = index
            self._cursor = index
            self._next_due = now + self.cfg["rotate_minutes"] * 60
            if screen.plugin_id == NOW_LISTENING:
                self._nl_key = key
                self._nl_shown_at = now
                self._next_poll = now + self.cfg["poll_minutes"] * 60
        record = {"screen": screen.name, "plugin_id": screen.plugin_id, "at": now_iso()}
        try:
            update_state(lambda state: state.__setitem__("display", record), self.state_path)
        except Exception as e:  # the panel did change; only /state's last_display is stale
            log.warning("cannot record the display: %s", e)
        return True

    def _wait_ready(self):
        last_log = None
        while not self._stop_event.is_set():
            if self.inkypi.ready():
                return True
            if last_log is None or self.clock() - last_log >= READY_LOG_S:
                log.info("waiting for InkyPi at %s", self.inkypi.url)
                last_log = self.clock()
            self._stop_event.wait(READY_RETRY_S)
        return False

    def _wait_screens(self):
        while not self._stop_event.is_set():
            screens = self._load_screens()
            if screens:
                with self._lock:
                    self._screens = screens
                return True
            log.info("playlist %r has no screens yet, retrying", self.cfg["playlist"])
            self._stop_event.wait(SCREENS_RETRY_S)
        return False

    def _next_index(self):
        with self._lock:
            n = len(self._screens)
            return 0 if self._cursor is None else (self._cursor + 1) % n

    def _run_pending(self, action, name):
        if action in ("swap", "show"):
            self._refresh_screens()
        if action == "swap":
            self._show(self._next_index(), advance=True)
            return
        if action == "show":
            with self._lock:
                names = [s.name for s in self._screens]
            if name not in names:
                log.warning("screen %s is gone from the playlist", name)
                return
            self._show(names.index(name), advance=True, retry=True)
            return
        with self._lock:
            index = self._current
        if index is None:
            self._show(0, advance=True, retry=True)
        else:
            self._show(index, advance=False)

    def _poll_now_listening(self, now):
        with self._lock:
            self._next_poll = now + self.cfg["poll_minutes"] * 60
            old_key, shown_at = self._nl_key, self._nl_shown_at
        key = self._now_listening_key()
        if key is None or key == old_key:
            return
        if shown_at is not None and now - shown_at < self.cfg["min_redraw_minutes"] * 60:
            return
        with self._lock:
            if self._pending is None:
                self._pending = ("redraw", None)

    def _startup(self):
        # the first show draws the current badge, so a redraw queued during boot is moot
        with self._lock:
            self._booted = True
            if self._pending is not None and self._pending[0] == "redraw":
                self._pending = None
        self._show(0, advance=True, retry=True)

    def _tick(self):
        if not self._booted:
            self._startup()
            return
        with self._lock:
            pending, self._pending = self._pending, None
            current = self._screens[self._current] if self._current is not None else None
            next_due, next_poll = self._next_due, self._next_poll
        now = self.clock()
        if pending:
            self._run_pending(*pending)
        elif next_due is not None and now >= next_due:
            self._refresh_screens()
            self._show(self._next_index(), advance=True)
        elif (
            current is not None
            and current.plugin_id == NOW_LISTENING
            and (next_poll is None or now >= next_poll)
        ):
            self._poll_now_listening(now)

    def run(self):
        try:
            self._run()
        except Exception:  # logged here; main sees the dead thread and exits non-zero
            log.exception("conductor crashed")

    def _run(self):
        self._current_abs()
        if not self._wait_ready() or not self._wait_screens():
            return
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:  # keep the panel alive whatever one step does
                log.exception("conductor step failed")
            self._stop_event.wait(TICK_S)


def make_handler(conductor):
    routes = {"/swap": "POST", "/show": "POST", "/badge": "POST", "/state": "GET"}

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
            raw = self.rfile.read(length) if length > 0 else b""
            return json.loads(raw)

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
            elif path == "/swap":
                self._send(202, conductor.request("swap"))
            elif path == "/show":
                self._show()
            else:
                self._badge()

        def _show(self):
            try:
                data = self._json_body()
            except ValueError:
                self._send(400, {"error": "bad JSON"})
                return
            screen = data.get("screen") if isinstance(data, dict) else None
            if not isinstance(screen, str) or not screen:
                self._send(400, {"error": "missing field: screen"})
                return
            try:
                self._send(202, conductor.request("show", screen))
            except KeyError:
                self._send(404, {"error": "unknown screen", "screens": conductor.screen_names()})

        def _badge(self):
            try:
                data = self._json_body()
            except ValueError:
                self._send(400, {"error": "bad JSON"})
                return
            if not isinstance(data, dict):
                self._send(400, {"error": "badge must be a JSON object"})
                return
            badge, changed = conductor.set_badge(data)
            self._send(202, {"badge": badge, "redraw": changed})

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
    state_path = os.path.join(cfg["state_dir"], "state.json")
    conductor = Conductor(cfg, InkyPi(cfg["inkypi_url"]), default_abs_factory(cfg), state_path)
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
    return code


if __name__ == "__main__":
    sys.exit(main())
