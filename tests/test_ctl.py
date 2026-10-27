import json
import logging
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "ctl"))

import estante_ctl  # noqa: E402
from estante_lib.abs import AbsClient  # noqa: E402
from estante_lib.state import read_state, write_state  # noqa: E402
from fake_inkypi import AUDIOBOOKS_ID, LIGHT_NOVELS_ID, FakeInkyPi  # noqa: E402

IN_PROGRESS_ID = "4aa1594d-b6ce-441f-9ab3-951ff1340009"
PROGRESS_PATH = f"/api/me/progress/{IN_PROGRESS_ID}"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SNAPSHOT_KEYS = {"screen", "screens", "badge", "busy", "last_display", "next_rotation", "rotate_minutes"}


def wait_until(predicate, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    raise AssertionError(f"condition not met within {timeout} s")


class Reply:
    def __init__(self, status, body, content_type):
        self.status = status
        self.body = body
        self.content_type = content_type


class Http:
    def __init__(self, server):
        host, port = server.server_address[:2]
        self.base = f"http://{host}:{port}"

    def request(self, method, path, body=None, raw=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return Reply(resp.status, json.loads(resp.read()), resp.headers.get("Content-Type"))
        except urllib.error.HTTPError as e:
            return Reply(e.code, json.loads(e.read()), e.headers.get("Content-Type"))

    def post(self, path, body=None, raw=None):
        return self.request("POST", path, body, raw)

    def get(self, path):
        return self.request("GET", path)


@pytest.fixture
def fake_inkypi():
    with FakeInkyPi() as fake:
        yield fake


@pytest.fixture
def tmp_state(tmp_path):
    return tmp_path / "state" / "state.json"


@pytest.fixture
def make_conductor(fake_inkypi, fake_abs, tmp_state):
    started = []

    def make(with_abs=True, start=True, **overrides):
        cfg = dict(estante_ctl.DEFAULTS)
        cfg.update(
            inkypi_url=fake_inkypi.url,
            inkypi_device_json=fake_inkypi.device_json,
            rotate_minutes=0.02,
            poll_minutes=0.01,
            min_redraw_minutes=0,
            state_dir=str(tmp_state.parent),
            listen="127.0.0.1",
            port=0,
        )
        cfg.update(overrides)

        def factory():
            return AbsClient(fake_abs.base_url, "TESTTOKEN", timeout=5) if with_abs else None

        conductor = estante_ctl.Conductor(
            cfg, estante_ctl.InkyPi(cfg["inkypi_url"]), factory, str(tmp_state)
        )
        if start:
            conductor.start()
        started.append(conductor)
        return conductor

    yield make
    for c in started:
        c.stop()
        if c.ident is not None:
            c.join(timeout=5)
        assert not c.is_alive()


@pytest.fixture
def running_conductor(make_conductor):
    return make_conductor()


@pytest.fixture
def conductor(make_conductor):
    """A conductor whose rotation never fires during a test."""
    return make_conductor(rotate_minutes=10, poll_minutes=10)


@pytest.fixture
def http(conductor, fake_inkypi):
    server = estante_ctl.serve(conductor.cfg, conductor)
    wait_until(lambda: fake_inkypi.displays, 5)
    wait_until(lambda: not conductor.snapshot()["busy"], 5)
    yield Http(server)
    server.shutdown()
    server.server_close()


def names(fake):
    return [d[2] for d in fake.displays]


def test_startup_shows_first_screen(fake_inkypi, running_conductor):
    wait_until(lambda: fake_inkypi.displays, 5)
    assert fake_inkypi.displays[0] == ("Default", "now_listening", "Now listening")


def test_rotation_cycles_in_playlist_order(fake_inkypi, running_conductor):
    wait_until(lambda: len(fake_inkypi.displays) >= 4, 10)
    assert names(fake_inkypi)[:4] == ["Now listening", "The shelf", "Light novels", "Now listening"]
    assert [d[1] for d in fake_inkypi.displays[:3]] == ["now_listening", "the_shelf", "the_shelf"]


def test_swap_shows_next_now(http, fake_inkypi, conductor):
    before = conductor.snapshot()["next_rotation"]
    assert before is not None
    time.sleep(1.1)
    reply = http.post("/swap")
    assert reply.status == 202
    assert reply.body == {"queued": "swap", "screen": None}
    assert reply.content_type == "application/json"
    wait_until(lambda: len(fake_inkypi.displays) >= 2, 2)
    assert fake_inkypi.displays[1] == ("Default", "the_shelf", "The shelf")
    wait_until(lambda: not conductor.snapshot()["busy"], 2)
    snap = conductor.snapshot()
    assert snap["screen"] == "The shelf"
    assert snap["next_rotation"] > before


def test_show_by_slug_and_name(http, fake_inkypi, conductor):
    reply = http.post("/show", {"screen": "light_novels"})
    assert reply.status == 202
    assert reply.body == {"queued": "show", "screen": "Light novels"}
    wait_until(lambda: len(fake_inkypi.displays) >= 2, 3)
    assert fake_inkypi.displays[1] == ("Default", "the_shelf", "Light novels")
    wait_until(lambda: not conductor.snapshot()["busy"], 2)

    reply = http.post("/show", {"screen": "The shelf"})
    assert reply.status == 202
    assert reply.body["screen"] == "The shelf"
    wait_until(lambda: len(fake_inkypi.displays) >= 3, 3)
    assert fake_inkypi.displays[2][2] == "The shelf"

    assert http.post("/show", {"screen": "the shelf"}).body["screen"] == "The shelf"
    assert http.post("/show", {"screen": "the_shelf"}).body["screen"] == "The shelf"

    reply = http.post("/show", {"screen": "nope"})
    assert reply.status == 404
    assert reply.body == {
        "error": "unknown screen",
        "screens": ["Now listening", "The shelf", "Light novels"],
    }
    assert http.post("/show", raw=b"{not json").status == 400
    assert http.post("/show", {}).status == 400
    assert http.post("/show", {"screen": 3}).status == 400


def test_badge_change_redraws_current_and_persists(http, fake_inkypi, conductor, tmp_state):
    payload = {"message_waiting": True, "dnd": False, "from": "Jasmine"}
    reply = http.post("/badge", payload)
    assert reply.status == 202
    assert reply.body == {
        "badge": {"message_waiting": True, "dnd": False, "count": 0, "from": "Jasmine"},
        "redraw": True,
    }
    wait_until(lambda: len(fake_inkypi.displays) >= 2, 3)
    assert names(fake_inkypi)[:2] == ["Now listening", "Now listening"]
    assert read_state(str(tmp_state))["badge"]["message_waiting"] is True
    assert read_state(str(tmp_state))["badge"]["from"] == "Jasmine"
    wait_until(lambda: not conductor.snapshot()["busy"], 2)

    reply = http.post("/badge", payload)
    assert reply.status == 202
    assert reply.body["redraw"] is False
    time.sleep(0.8)
    assert len(fake_inkypi.displays) == 2

    assert http.post("/badge", raw=b"[oops").status == 400
    assert http.post("/badge", [1, 2]).status == 400


def test_shelf_page_advances_on_each_show(fake_inkypi, tmp_state, make_conductor):
    write_state({"page_counts": {AUDIOBOOKS_ID: 7}}, str(tmp_state))
    conductor = make_conductor(rotate_minutes=10, poll_minutes=10)
    server = estante_ctl.serve(conductor.cfg, conductor)
    http = Http(server)
    try:
        wait_until(lambda: fake_inkypi.displays, 5)
        seen = []
        for i in range(3):
            wait_until(lambda: not conductor.snapshot()["busy"], 3)
            count = len(fake_inkypi.displays)
            assert http.post("/show", {"screen": "the_shelf"}).status == 202
            wait_until(lambda: len(fake_inkypi.displays) > count, 3)
            seen.append(read_state(str(tmp_state))["pages"][AUDIOBOOKS_ID])
            if i == 0:
                wait_until(lambda: not conductor.snapshot()["busy"], 3)
                count = len(fake_inkypi.displays)
                reply = http.post("/badge", {"message_waiting": True})
                assert reply.body["redraw"] is True
                wait_until(lambda: len(fake_inkypi.displays) > count, 3)
                assert fake_inkypi.displays[-1][2] == "The shelf"
                assert read_state(str(tmp_state))["pages"][AUDIOBOOKS_ID] == 0
        assert seen == [0, 1, 2]
        assert LIGHT_NOVELS_ID not in read_state(str(tmp_state))["pages"]
    finally:
        server.shutdown()
        server.server_close()


def test_shelf_page_wraps_at_page_count(fake_inkypi, tmp_state, make_conductor):
    write_state({"page_counts": {AUDIOBOOKS_ID: 2}, "pages": {AUDIOBOOKS_ID: 1}}, str(tmp_state))
    conductor = make_conductor(rotate_minutes=10, poll_minutes=10)
    wait_until(lambda: fake_inkypi.displays, 5)
    wait_until(lambda: not conductor.snapshot()["busy"], 3)
    conductor.request("show", "The shelf")
    wait_until(lambda: len(fake_inkypi.displays) >= 2, 3)
    assert read_state(str(tmp_state))["pages"][AUDIOBOOKS_ID] == 0


def test_now_listening_watch_redraws_on_key_change(fake_inkypi, fake_abs, make_conductor):
    progress = json.loads((FIXTURES / f"progress_{IN_PROGRESS_ID}.json").read_text())
    make_conductor(rotate_minutes=10, poll_minutes=0.01, min_redraw_minutes=0.04)
    wait_until(lambda: fake_inkypi.displays, 5)
    time.sleep(1.0)
    assert len(fake_inkypi.displays) == 1

    moved = dict(progress, currentTime=progress["currentTime"] + 3000)
    fake_abs.override(PROGRESS_PATH, moved)
    wait_until(lambda: len(fake_inkypi.displays) >= 2, 4)
    assert fake_inkypi.displays[1] == ("Default", "now_listening", "Now listening")

    moved_again = dict(progress, currentTime=progress["currentTime"] + 6000)
    fake_abs.override(PROGRESS_PATH, moved_again)
    time.sleep(1.5)
    assert len(fake_inkypi.displays) == 2

    wait_until(lambda: len(fake_inkypi.displays) >= 3, 4)
    time.sleep(3.0)
    assert len(fake_inkypi.displays) == 3


def test_watch_disabled_without_abs_creds(fake_inkypi, make_conductor):
    make_conductor(with_abs=False)
    wait_until(lambda: len(fake_inkypi.displays) >= 2, 5)
    assert names(fake_inkypi)[:2] == ["Now listening", "The shelf"]


def test_rotation_survives_inkypi_500(fake_inkypi, running_conductor):
    wait_until(lambda: fake_inkypi.displays, 5)
    fake_inkypi.fail_next(1)
    wait_until(lambda: fake_inkypi.failed, 5)
    count = len(fake_inkypi.displays)
    wait_until(lambda: len(fake_inkypi.displays) > count, 5)
    assert running_conductor.is_alive()


def test_screens_retries_on_partial_json(tmp_path):
    with FakeInkyPi() as fake:
        full = Path(fake.device_json).read_text()
    path = tmp_path / "device.json"
    path.write_text("{")

    def finish():
        time.sleep(0.3)
        path.write_text(full)

    t = threading.Thread(target=finish)
    t.start()
    screens = estante_ctl.InkyPi("http://127.0.0.1:9").screens(str(path), "Default")
    t.join()
    assert [s.name for s in screens] == ["Now listening", "The shelf", "Light novels"]
    assert [s.slug for s in screens] == ["now_listening", "the_shelf", "light_novels"]
    assert screens[1].settings == {"library_id": AUDIOBOOKS_ID}


def test_screens_missing_playlist_or_file(tmp_path, fake_inkypi):
    inky = estante_ctl.InkyPi(fake_inkypi.url)
    assert inky.screens(fake_inkypi.device_json, "Nope") == []
    assert inky.screens(str(tmp_path / "absent.json"), "Default") == []


def test_inkypi_display_false_on_error(fake_inkypi):
    inky = estante_ctl.InkyPi(fake_inkypi.url)
    assert inky.ready() is True
    fake_inkypi.fail_next(1)
    assert inky.display("Default", "the_shelf", "The shelf") is False
    assert inky.display("Default", "the_shelf", "The shelf") is True
    assert estante_ctl.InkyPi("http://127.0.0.1:9").ready() is False


def test_state_snapshot_fields(http, conductor):
    reply = http.get("/state")
    assert reply.status == 200
    assert reply.content_type == "application/json"
    snap = reply.body
    assert set(snap) == SNAPSHOT_KEYS
    assert snap["screen"] == "Now listening"
    assert snap["screens"] == ["Now listening", "The shelf", "Light novels"]
    assert snap["badge"] == {"message_waiting": False, "dnd": False, "count": 0, "from": ""}
    assert snap["busy"] is False
    assert snap["last_display"]["screen"] == "Now listening"
    assert snap["last_display"]["plugin_id"] == "now_listening"
    assert isinstance(snap["next_rotation"], str)
    assert snap["rotate_minutes"] == 10


def test_unknown_route_and_wrong_method(http):
    assert http.get("/nope").status == 404
    assert http.post("/nope").status == 404
    assert http.get("/swap").status == 405
    assert http.post("/state").status == 405
    assert http.request("DELETE", "/badge").status == 405


def test_helpers(tmp_path, monkeypatch):
    assert estante_ctl.slug("Now listening") == "now_listening"
    assert estante_ctl.slug("  Light Novels! ") == "light_novels"
    env = tmp_path / ".env"
    env.write_text('# comment\nABS_URL="http://abs.local:13378"\nABS_TOKEN=\'abc\'\n\nexport X=1\nJUNK\n')
    assert estante_ctl.parse_env_file(str(env)) == {
        "ABS_URL": "http://abs.local:13378",
        "ABS_TOKEN": "abc",
        "X": "1",
    }
    assert estante_ctl.parse_env_file(str(tmp_path / "missing")) == {}
    cfg_file = tmp_path / "estante.json"
    cfg_file.write_text(json.dumps({"rotate_minutes": 0.5, "port": 9999}))
    cfg = estante_ctl.load_config(str(cfg_file))
    assert cfg["rotate_minutes"] == 0.5 and cfg["port"] == 9999
    assert cfg["playlist"] == "Default"
    monkeypatch.setenv("ESTANTE_CONFIG", str(cfg_file))
    assert estante_ctl.load_config(None)["port"] == 9999
    monkeypatch.delenv("ESTANTE_CONFIG")
    defaults = estante_ctl.load_config(None)
    assert {k: v for k, v in defaults.items() if k != "inkypi_env"} == {
        k: v for k, v in estante_ctl.DEFAULTS.items() if k != "inkypi_env"
    }


def test_inkypi_env_derived_from_real_src(tmp_path):
    # InkyPi's installer links /usr/local/inkypi/src to the checkout's src, and InkyPi
    # reads .env from the checkout root, found through the real path.
    root = tmp_path / "InkyPi"
    (root / "src" / "config").mkdir(parents=True)
    (root / "src" / "config" / "device.json").write_text("{}")
    other = tmp_path / "usr-local-inkypi"
    other.mkdir()
    (other / "src").symlink_to(root / "src")
    cfg_file = tmp_path / "estante.json"
    cfg_file.write_text(json.dumps({"inkypi_device_json": str(other / "src" / "config" / "device.json")}))
    cfg = estante_ctl.load_config(str(cfg_file))
    assert cfg["inkypi_env"] == str((root / ".env").resolve())

    cfg_file.write_text(json.dumps({"inkypi_env": "/etc/explicit.env"}))
    assert estante_ctl.load_config(str(cfg_file))["inkypi_env"] == "/etc/explicit.env"
    assert estante_ctl.DEFAULTS["inkypi_env"] == ""


def test_example_config_matches_defaults():
    example = json.loads((REPO / "ctl" / "estante.example.json").read_text())
    assert example == estante_ctl.DEFAULTS


def test_malformed_status_line_does_not_stop_rotation(fake_inkypi, make_conductor):
    fake_inkypi.malformed_next(1)
    conductor = make_conductor()
    wait_until(lambda: fake_inkypi.malformed, 5)
    wait_until(lambda: len(fake_inkypi.displays) >= 2, 6)
    assert conductor.is_alive()
    fake_inkypi.malformed_next(3)
    wait_until(lambda: len(fake_inkypi.malformed) >= 4, 8)
    times = [t for t, _ in fake_inkypi.malformed[1:4]]
    # each failed attempt pushes the next one out by min(60 s, 1.2 s rotation), not one tick
    assert all(b - a >= 1.0 for a, b in zip(times, times[1:]))
    wait_until(lambda: not conductor.snapshot()["busy"], 3)
    assert conductor._next_due > conductor.clock()
    count = len(fake_inkypi.displays)
    wait_until(lambda: len(fake_inkypi.displays) > count, 5)
    assert conductor.is_alive()


def test_redraw_never_replaces_a_queued_show(http, fake_inkypi, conductor):
    fake_inkypi.delay_s = 1.0
    assert http.post("/swap").status == 202
    wait_until(lambda: conductor.snapshot()["busy"], 2)
    assert http.post("/show", {"screen": "light_novels"}).status == 202
    reply = http.post("/badge", {"message_waiting": True})
    assert reply.body["redraw"] is True
    wait_until(lambda: len(fake_inkypi.displays) >= 3, 5)
    assert names(fake_inkypi)[:3] == ["Now listening", "The shelf", "Light novels"]
    time.sleep(1.5)
    assert len(fake_inkypi.displays) == 3


def test_show_replaces_a_queued_redraw(conductor):
    conductor.request("redraw")
    assert conductor._pending == ("redraw", None)
    conductor.request("show", "the_shelf")
    assert conductor._pending == ("show", "The shelf")
    conductor.request("swap")
    assert conductor._pending == ("swap", None)
    conductor.request("redraw")
    assert conductor._pending == ("swap", None)


def test_startup_show_clears_a_boot_time_redraw(fake_inkypi, make_conductor, tmp_state):
    conductor = make_conductor(start=False, rotate_minutes=10, poll_minutes=10)
    badge, changed = conductor.set_badge({"message_waiting": True})
    assert changed is True
    conductor.start()
    wait_until(lambda: fake_inkypi.displays, 5)
    time.sleep(1.0)
    assert names(fake_inkypi) == ["Now listening"]
    assert read_state(str(tmp_state))["badge"]["message_waiting"] is True


def test_startup_keeps_a_boot_time_show(fake_inkypi, make_conductor):
    conductor = make_conductor(start=False, rotate_minutes=10, poll_minutes=10)
    conductor.request("show", "Light novels")
    conductor.set_badge({"dnd": True})
    conductor.start()
    wait_until(lambda: len(fake_inkypi.displays) >= 2, 5)
    assert names(fake_inkypi)[:2] == ["Now listening", "Light novels"]


def test_unexpected_handler_error_answers_json_500(http, conductor, monkeypatch):
    def boom(incoming):
        raise RuntimeError("state dir is read-only")

    monkeypatch.setattr(conductor, "set_badge", boom)
    reply = http.post("/badge", {"dnd": True})
    assert reply.status == 500
    assert reply.content_type == "application/json"
    assert reply.body == {"error": "state dir is read-only"}


def test_wait_for_exit_reports_a_dead_conductor(make_conductor):
    def broken_wait():
        raise RuntimeError("boom")

    conductor = make_conductor(start=False)
    conductor._wait_ready = broken_wait
    conductor.start()
    conductor.join(timeout=5)
    assert estante_ctl.wait_for_exit(threading.Event(), conductor, interval=0.05) == 1
    done = threading.Event()
    done.set()
    assert estante_ctl.wait_for_exit(done, conductor, interval=0.05) == 0


def _single_screen_playlist(fake, index):
    data = json.loads(Path(fake.device_json).read_text())
    playlist = data["playlist_config"]["playlists"][0]
    playlist["plugins"] = playlist["plugins"][index:index + 1]
    Path(fake.device_json).write_text(json.dumps(data))


def test_single_screen_that_always_fails_backs_off(fake_inkypi, make_conductor, tmp_state):
    _single_screen_playlist(fake_inkypi, 1)
    fake_inkypi.fail_next(10**6)
    conductor = make_conductor()
    wait_until(lambda: fake_inkypi.failed, 5)
    started = time.monotonic()
    wait_until(lambda: not conductor.snapshot()["busy"], 3)
    first_due = conductor._next_due
    wait_until(lambda: len(fake_inkypi.failed) >= 3, 8)
    # two backoffs of min(60 s, 1.2 s rotation) between the first and third attempt, not ticks
    assert time.monotonic() - started >= 2.0
    assert conductor.is_alive()
    wait_until(lambda: not conductor.snapshot()["busy"], 3)
    assert conductor._next_due > first_due > 0
    assert fake_inkypi.displays == []
    assert AUDIOBOOKS_ID not in read_state(str(tmp_state))["pages"]


def test_failing_screen_is_skipped_each_cycle(fake_inkypi, make_conductor):
    fake_inkypi.fail_instances.add("The shelf")
    conductor = make_conductor()
    wait_until(lambda: len(fake_inkypi.displays) >= 4, 12)
    assert fake_inkypi.attempts[:6] == [
        "Now listening", "The shelf", "Light novels",
        "Now listening", "The shelf", "Light novels",
    ]
    assert names(fake_inkypi)[:4] == ["Now listening", "Light novels", "Now listening", "Light novels"]
    assert conductor.is_alive()


def test_swap_after_a_failure_shows_the_screen_after_it(http, fake_inkypi, conductor):
    fake_inkypi.fail_instances.add("The shelf")
    assert http.post("/swap").status == 202
    wait_until(lambda: fake_inkypi.failed, 3)
    wait_until(lambda: not conductor.snapshot()["busy"], 3)
    assert conductor.snapshot()["screen"] == "Now listening"
    assert http.post("/swap").status == 202
    wait_until(lambda: len(fake_inkypi.displays) >= 2, 3)
    assert fake_inkypi.displays[1][2] == "Light novels"
    assert fake_inkypi.attempts[:3] == ["Now listening", "The shelf", "Light novels"]


def test_failed_show_retries_that_screen_on_its_next_turn(fake_inkypi, make_conductor):
    conductor = make_conductor(rotate_minutes=0.03, poll_minutes=10)
    wait_until(lambda: fake_inkypi.displays, 5)
    wait_until(lambda: not conductor.snapshot()["busy"], 3)
    fake_inkypi.fail_next(1)
    conductor.request("show", "Light novels")
    wait_until(lambda: fake_inkypi.failed, 3)
    wait_until(lambda: len(fake_inkypi.displays) >= 2, 5)
    assert fake_inkypi.attempts[:3] == ["Now listening", "Light novels", "Light novels"]
    assert names(fake_inkypi)[:2] == ["Now listening", "Light novels"]


def test_failed_shelf_show_keeps_the_page(fake_inkypi, tmp_state, make_conductor):
    write_state({"page_counts": {AUDIOBOOKS_ID: 7}, "pages": {AUDIOBOOKS_ID: 3}}, str(tmp_state))
    conductor = make_conductor(rotate_minutes=10, poll_minutes=10)
    wait_until(lambda: fake_inkypi.displays, 5)
    wait_until(lambda: not conductor.snapshot()["busy"], 3)
    for screen in ("The shelf", "Light novels"):
        count = len(fake_inkypi.failed)
        fake_inkypi.fail_next(1)
        conductor.request("show", screen)
        wait_until(lambda: len(fake_inkypi.failed) > count, 3)
        wait_until(lambda: not conductor.snapshot()["busy"], 3)
    pages = read_state(str(tmp_state))["pages"]
    assert pages == {AUDIOBOOKS_ID: 3}
    conductor.request("show", "The shelf")
    wait_until(lambda: len(fake_inkypi.displays) >= 2, 3)
    assert read_state(str(tmp_state))["pages"][AUDIOBOOKS_ID] == 4


def test_page_advance_error_neither_blocks_nor_refires(fake_inkypi, make_conductor):
    conductor = make_conductor(start=False)
    busy_at_advance = []

    def broken_advance(screen):
        busy_at_advance.append(conductor._busy)
        raise OSError("read-only state dir")

    conductor._advance_page = broken_advance
    conductor.start()
    wait_until(lambda: len(fake_inkypi.displays) >= 3, 8)
    assert names(fake_inkypi)[:3] == ["Now listening", "The shelf", "Light novels"]
    assert busy_at_advance and all(busy_at_advance)
    # one advance attempt per shelf show, not one per tick
    assert len(busy_at_advance) <= 3


def test_abs_credentials_are_read_lazily(fake_inkypi, fake_abs, make_conductor, caplog):
    caplog.set_level(logging.INFO, logger="estante-ctl")
    creds = {"ok": False}
    conductor = make_conductor(start=False, rotate_minutes=10, poll_minutes=0.01, min_redraw_minutes=0)

    def factory():
        return AbsClient(fake_abs.base_url, "TESTTOKEN", timeout=5) if creds["ok"] else None

    conductor.abs_factory = factory
    conductor.start()
    wait_until(lambda: fake_inkypi.displays, 5)
    time.sleep(1.5)
    assert len(fake_inkypi.displays) == 1
    disabled = [r for r in caplog.records if "watch disabled" in r.getMessage()]
    assert len(disabled) == 1
    creds["ok"] = True
    wait_until(lambda: len(fake_inkypi.displays) >= 2, 4)
    assert names(fake_inkypi)[:2] == ["Now listening", "Now listening"]
    enabled = [r for r in caplog.records if "watch enabled" in r.getMessage()]
    assert len(enabled) == 1


def test_display_timeout_and_listen_default():
    # a cold mosaic render fetches up to 900 covers before InkyPi answers
    assert estante_ctl.DISPLAY_TIMEOUT_S == 600
    assert estante_ctl.DEFAULTS["listen"] == "127.0.0.1"
