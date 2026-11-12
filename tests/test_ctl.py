import json
import logging
import socket
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
from estante_lib.abs import AbsClient, AbsError  # noqa: E402
from estante_lib.display_fb import FbInfo, FileFramebuffer  # noqa: E402
from estante_lib.render_screen import render_mark, to_fb_bytes  # noqa: E402
from estante_lib.state import read_state, write_state  # noqa: E402
from estante_lib.theme import DARK  # noqa: E402

IN_PROGRESS_ID = "4aa1594d-b6ce-441f-9ab3-951ff1340009"
PROGRESS_PATH = f"/api/me/progress/{IN_PROGRESS_ID}"
AUDIOBOOKS_ID = "b14de8f5-99a5-4cba-b970-6a2cb65f2993"
LIGHT_NOVELS_ID = "65bcd45d-1cce-496c-950e-4f3aabf5f5eb"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SNAPSHOT_KEYS = {"wall", "walls", "badge", "screen", "busy", "last_render", "now_listening"}
TWO_WALLS = [
    {"name": "audiobooks", "library_id": ""},
    {"name": "light_novels", "library_id": ""},
]
SIZE = (1920, 1080)
INFO = FbInfo(1920, 1080, 16, 3840)
# The wall header (title, counts, subtitle) on the 1920x1080 canvas.
HEADER = (650, 10, 1880, 90)


def wait_until(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    raise AssertionError(f"condition not met within {timeout} s")


def fixture_json(name):
    return json.loads((FIXTURES / name).read_text())


def closed_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def rgb565(rgb):
    r, g, b = rgb
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def pixel(frame, x, y, width=1920):
    i = (y * width + x) * 2
    return frame[i] | (frame[i + 1] << 8)


def region(frame, box, width=1920):
    x0, y0, x1, y1 = box
    return b"".join(frame[(y * width + x0) * 2:(y * width + x1) * 2] for y in range(y0, y1))


def brightness(frame, box, width=1920):
    """Sum of the 5-6-5 channels over a box, sampled every 4th pixel."""
    total = 0
    data = region(frame, box, width)
    for i in range(0, len(data), 8):
        v = data[i] | (data[i + 1] << 8)
        total += (v >> 11) + ((v >> 5) & 0x3F) + (v & 0x1F)
    return total


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
            with e:
                return Reply(e.code, json.loads(e.read()), e.headers.get("Content-Type"))

    def post(self, path, body=None, raw=None):
        return self.request("POST", path, body, raw)

    def get(self, path):
        return self.request("GET", path)


class NoBlankFramebuffer(FileFramebuffer):
    """A driver that refuses DPMS, so the conductor has to paint black."""

    def blank(self, on):
        self.blank_calls.append(bool(on))
        return False


@pytest.fixture(autouse=True)
def fast_tick(monkeypatch):
    monkeypatch.setattr(estante_ctl, "TICK_S", 0.02)


@pytest.fixture
def make_conductor(fake_abs, tmp_path):
    started = []

    def make(start=True, abs_url=None, fb_cls=None, fb_name="fb", **overrides):
        cfg = dict(estante_ctl.DEFAULTS)
        cfg.update(
            abs_url=fake_abs.base_url if abs_url is None else abs_url,
            abs_token="TESTTOKEN",
            state_dir=str(tmp_path / "state"),
            fb_file=str(tmp_path / fb_name),
            fb_size=[1920, 1080],
            fb_bpp=16,
            poll_seconds=0.2,
            # the fake records Overgeared's cover only at width 400
            now_cover_width=400,
            listen="127.0.0.1",
            port=0,
            walls=TWO_WALLS,
        )
        cfg.update(overrides)
        if fb_cls is None:
            fb = estante_ctl.open_framebuffer(cfg)
        else:
            w, h = cfg["fb_size"]
            fb = fb_cls(cfg["fb_file"], FbInfo(w, h, 16, w * 2))
        conductor = estante_ctl.Conductor(cfg, estante_ctl.make_client(cfg), fb, estante_ctl.state_file(cfg))
        if start:
            conductor.start()
        started.append(conductor)
        return conductor

    yield make
    for c in started:
        c.stop()
        if c.ident is not None:
            c.join(timeout=10)
        assert not c.is_alive()
        c.fb.close()


@pytest.fixture
def conductor(make_conductor):
    return make_conductor()


@pytest.fixture
def http(conductor):
    server = estante_ctl.serve(conductor.cfg, conductor)
    yield Http(server)
    server.shutdown()
    server.server_close()


def frame(conductor):
    with open(conductor.cfg["fb_file"], "rb") as f:
        return f.read()


def rendered(conductor):
    wait_until(lambda: conductor.snapshot()["last_render"] is not None)
    wait_until(lambda: not conductor.snapshot()["busy"])
    return frame(conductor)


def renders_after(conductor, before):
    wait_until(lambda: conductor.render_count > before)
    wait_until(lambda: not conductor.snapshot()["busy"])
    return frame(conductor)


def render_count(conductor):
    return conductor.render_count


def mark_bytes():
    return to_fb_bytes(render_mark(DARK, SIZE), INFO)


def bump_progress(fake_abs, seconds=600):
    prog = fixture_json(f"progress_{IN_PROGRESS_ID}.json")
    prog["currentTime"] = (prog.get("currentTime") or 0) + seconds
    prog["lastUpdate"] = (prog.get("lastUpdate") or 0) + 1000
    fake_abs.override(PROGRESS_PATH, prog)


# config


def test_defaults_and_example_config():
    d = estante_ctl.DEFAULTS
    assert d["listen"] == "0.0.0.0"
    assert d["port"] == 8360
    assert d["walls"] == [{"name": "audiobooks", "library_id": "", "title": "The shelf"}]
    assert d["poll_seconds"] == 60 and d["wall_refresh_minutes"] == 15
    assert d["cover_width"] == 120 and d["now_cover_width"] == 520
    assert d["canvas"] == [1920, 1080] and d["rotate"] == 0
    assert d["fb_device"] == "/dev/fb0" and d["fb_sysfs"] == "/sys/class/graphics/fb0"
    assert d["fb_file"] == "" and d["fb_size"] == [1920, 1080] and d["fb_bpp"] == 16
    assert d["theme"] == "dark" and d["state_dir"] == "/var/lib/estante"
    example = json.loads((REPO / "ctl" / "estante.example.json").read_text())
    assert example == d


def test_load_config_merges_and_validates(tmp_path, monkeypatch):
    path = tmp_path / "estante.json"
    path.write_text(json.dumps({"port": 9999, "rotate": 180, "theme": "paper"}))
    cfg = estante_ctl.load_config(str(path))
    assert cfg["port"] == 9999 and cfg["rotate"] == 180 and cfg["theme"] == "paper"
    assert cfg["walls"] == estante_ctl.DEFAULTS["walls"]
    monkeypatch.setenv("ESTANTE_CONFIG", str(path))
    assert estante_ctl.load_config(None)["port"] == 9999
    path.write_text(json.dumps({"rotate": 90}))
    with pytest.raises(ValueError, match="rotate"):
        estante_ctl.load_config(str(path))
    path.write_text(json.dumps({"theme": "neon"}))
    with pytest.raises(KeyError, match="neon"):
        estante_ctl.load_config(str(path))
    path.write_text("[1, 2]")
    with pytest.raises(ValueError, match="JSON object"):
        estante_ctl.load_config(str(path))
    assert estante_ctl.load_config(str(tmp_path / "missing.json")) == estante_ctl.DEFAULTS


def test_defaults_are_not_shared_between_configs(tmp_path):
    cfg = estante_ctl.load_config(None)
    cfg["walls"][0]["library_id"] = "changed"
    assert estante_ctl.DEFAULTS["walls"][0]["library_id"] == ""


def test_unit_file():
    unit = (REPO / "ctl" / "estante-ctl.service").read_text()
    for line in (
        "User=root",
        "After=network-online.target",
        "Wants=network-online.target",
        "ExecStart=/opt/estante/.venv/bin/python /opt/estante/ctl/estante_ctl.py",
        "Environment=ESTANTE_CONFIG=/etc/estante/estante.json",
        "Restart=always",
        "RestartSec=5",
        "WantedBy=multi-user.target",
    ):
        assert line in unit.splitlines()
    assert "inkypi" not in unit.lower()


def test_open_framebuffer_file(tmp_path):
    cfg = dict(estante_ctl.DEFAULTS, fb_file=str(tmp_path / "fb"), fb_size=[1024, 768], fb_bpp=16)
    fb = estante_ctl.open_framebuffer(cfg)
    try:
        assert isinstance(fb, FileFramebuffer)
        assert (fb.info.width, fb.info.height, fb.info.bpp, fb.info.stride) == (1024, 768, 16, 2048)
        assert (tmp_path / "fb").stat().st_size == 2048 * 768
    finally:
        fb.close()


# walls


def test_resolve_walls_default_is_the_audiobooks_shelf(client):
    (wall,) = estante_ctl.resolve_walls(estante_ctl.load_config(None), client)
    assert (wall.name, wall.library_id, wall.title, wall.subtitle) == (
        "audiobooks", AUDIOBOOKS_ID, "The shelf", "Audiobooks, by author"
    )


def test_resolve_walls_by_library_name(client):
    cfg = dict(estante_ctl.DEFAULTS, walls=TWO_WALLS)
    walls = estante_ctl.resolve_walls(cfg, client)
    assert [(w.name, w.library_id, w.title, w.subtitle) for w in walls] == [
        ("audiobooks", AUDIOBOOKS_ID, "The shelf", "Audiobooks, by author"),
        ("light_novels", LIGHT_NOVELS_ID, "Light novels", "Light Novels, by author"),
    ]


def test_resolve_walls_keeps_ids_and_titles(client):
    cfg = dict(
        estante_ctl.DEFAULTS,
        walls=[{"name": "novels", "library_id": LIGHT_NOVELS_ID, "title": "Novels"}],
    )
    (wall,) = estante_ctl.resolve_walls(cfg, client)
    assert (wall.name, wall.library_id, wall.title, wall.subtitle) == (
        "novels", LIGHT_NOVELS_ID, "Novels", "Light Novels, by author"
    )


def test_resolve_walls_unknown_name_lists_libraries(client):
    cfg = dict(estante_ctl.DEFAULTS, walls=[{"name": "comics", "library_id": ""}])
    with pytest.raises(ValueError) as e:
        estante_ctl.resolve_walls(cfg, client)
    for name in ("comics", "Audiobooks", "Light Novels", "Podcasts"):
        assert name in str(e.value)


def test_resolve_walls_unknown_id_lists_libraries(client):
    cfg = dict(estante_ctl.DEFAULTS, walls=[{"name": "audiobooks", "library_id": "nope"}])
    with pytest.raises(ValueError, match="Light Novels"):
        estante_ctl.resolve_walls(cfg, client)


# frames


def test_startup_frame_is_the_mark(make_conductor):
    c = make_conductor(abs_url=f"http://127.0.0.1:{closed_port()}")
    wait_until(lambda: frame(c) == mark_bytes())
    assert pixel(frame(c), 0, 0) == rgb565(DARK.bg)
    # "ESTANTE" in the mark colour sits around the centre
    centre = region(frame(c), (860, 520, 1060, 560))
    assert rgb565(DARK.mark).to_bytes(2, "little") in centre
    assert c.snapshot()["last_render"] is None


def test_poll_renders_the_composition(conductor):
    data = rendered(conductor)
    assert data != mark_bytes()
    assert pixel(data, 0, 0) == rgb565(DARK.bg)
    snap = conductor.snapshot()
    assert snap["wall"] == "audiobooks"
    assert snap["walls"] == ["audiobooks", "light_novels"]
    assert snap["now_listening"]["item_id"] == IN_PROGRESS_ID
    assert snap["now_listening"]["title"] == "Overgeared"
    assert 0 <= snap["now_listening"]["pct"] <= 100
    # the Now listening cover (column 50..570, y 90..610) is drawn
    assert brightness(data, (50, 90, 570, 610)) > brightness(mark_bytes(), (50, 90, 570, 610)) * 2


def test_unchanged_polls_do_not_rerender(conductor):
    rendered(conductor)
    count = render_count(conductor)
    time.sleep(1.0)
    assert render_count(conductor) == count


def test_progress_change_rerenders(conductor, fake_abs):
    before = rendered(conductor)
    count = render_count(conductor)
    bump_progress(fake_abs, seconds=3 * 3600)
    after = renders_after(conductor, count)
    assert after != before


def test_wall_progress_change_rerenders(conductor, fake_abs):
    before = rendered(conductor)
    count = render_count(conductor)
    me = fixture_json("me.json")
    ids = {p.get("libraryItemId") for p in me["mediaProgress"]}
    unread = next(b["id"] for b in fixture_json("audiobooks.json")["results"] if b["id"] not in ids)
    me["mediaProgress"].append(
        {"libraryItemId": unread, "isFinished": True, "progress": 1, "finishedAt": 1, "lastUpdate": 1}
    )
    fake_abs.override("/api/me", me)
    after = renders_after(conductor, count)
    assert region(after, HEADER) != region(before, HEADER)


def test_wall_refresh_rerenders(make_conductor):
    c = make_conductor(wall_refresh_minutes=0.01)
    rendered(c)
    count = render_count(c)
    wait_until(lambda: render_count(c) > count)


def test_rotate_180_turns_the_frame(make_conductor):
    upright = rendered(make_conductor(fb_name="fb0"))
    turned = rendered(make_conductor(fb_name="fb180", rotate=180))
    pixels = [upright[i:i + 2] for i in range(0, len(upright), 2)]
    assert turned == b"".join(reversed(pixels))


def test_framebuffer_smaller_than_canvas(make_conductor):
    c = make_conductor(fb_size=[1024, 768])
    data = rendered(c)
    assert len(data) == 1024 * 768 * 2
    assert pixel(data, 0, 0, width=1024) == rgb565(DARK.bg)
    assert data != to_fb_bytes(render_mark(DARK, SIZE), FbInfo(1024, 768, 16, 2048))


# routes


def test_swap_changes_the_wall_and_its_header(http, conductor):
    before = rendered(conductor)
    count = render_count(conductor)
    r = http.post("/swap")
    assert r.status == 202 and r.body == {"queued": "swap"}
    after = renders_after(conductor, count)
    assert conductor.snapshot()["wall"] == "light_novels"
    assert region(after, HEADER) != region(before, HEADER)
    assert read_state(estante_ctl.state_file(conductor.cfg))["wall"] == "light_novels"
    count = render_count(conductor)
    http.post("/swap")
    back = renders_after(conductor, count)
    assert conductor.snapshot()["wall"] == "audiobooks"
    assert region(back, HEADER) == region(before, HEADER)


def test_show_by_name_and_by_id(http, conductor):
    rendered(conductor)
    count = render_count(conductor)
    r = http.post("/show", {"screen": "light_novels"})
    assert r.status == 202 and r.body["queued"] == "show" and r.body["wall"] == "light_novels"
    renders_after(conductor, count)
    assert conductor.snapshot()["wall"] == "light_novels"
    count = render_count(conductor)
    r = http.post("/show", {"screen": AUDIOBOOKS_ID})
    assert r.status == 202 and r.body["wall"] == "audiobooks"
    renders_after(conductor, count)
    assert conductor.snapshot()["wall"] == "audiobooks"


def test_show_rejects_unknown_and_bad_bodies(http, conductor):
    r = http.post("/show", {"screen": "comics"})
    assert r.status == 404 and r.body["walls"] == ["audiobooks", "light_novels"]
    assert http.post("/show", {}).status == 400
    assert http.post("/show", raw=b"{nope").status == 400


def test_badge_redraws_and_persists(http, conductor):
    before = rendered(conductor)
    count = render_count(conductor)
    r = http.post("/badge", {"message_waiting": True, "dnd": False, "from": "Jasmine"})
    assert r.status == 202 and r.body["redraw"] is True
    assert r.body["badge"]["message_waiting"] is True and r.body["badge"]["from"] == "Jasmine"
    after = renders_after(conductor, count)
    assert after != before
    assert read_state(estante_ctl.state_file(conductor.cfg))["badge"]["from"] == "Jasmine"
    assert conductor.snapshot()["badge"]["message_waiting"] is True
    r = http.post("/badge", {"message_waiting": True, "from": "Jasmine"})
    assert r.status == 202 and r.body["redraw"] is False
    assert http.post("/badge", raw=b"[1]").status == 400


def test_screen_off_blanks_and_stops_blits(http, conductor, fake_abs):
    shown = rendered(conductor)
    r = http.post("/screen", {"on": False})
    assert r.status == 202 and r.body == {"screen": {"on": False, "dim": 1.0}}
    assert conductor.fb.blank_calls == [True]
    count = render_count(conductor)
    bump_progress(fake_abs, seconds=3 * 3600)
    time.sleep(1.0)
    assert render_count(conductor) == count
    assert frame(conductor) == shown
    assert read_state(estante_ctl.state_file(conductor.cfg))["screen"] == {"on": False, "dim": 1.0}
    assert conductor.snapshot()["screen"] == {"on": False, "dim": 1.0}


def test_unblank_triggers_redraw(http, conductor, fake_abs):
    shown = rendered(conductor)
    http.post("/screen", {"on": False})
    bump_progress(fake_abs, seconds=3 * 3600)
    time.sleep(0.8)
    count = render_count(conductor)
    r = http.post("/screen", {"on": True})
    assert r.status == 202 and r.body["screen"]["on"] is True
    after = renders_after(conductor, count)
    assert conductor.fb.blank_calls == [True, False]
    assert after != shown


def test_screen_off_paints_black_when_blank_is_refused(make_conductor):
    c = make_conductor(fb_cls=NoBlankFramebuffer)
    rendered(c)
    c.set_screen(on=False)
    assert c.fb.blank_calls == [True]
    assert frame(c) == bytes(1920 * 1080 * 2)


def test_screen_off_during_render_wins(make_conductor, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    real = estante_ctl.render_screen

    def slow(view, size):
        entered.set()
        release.wait(10)
        return real(view, size)

    monkeypatch.setattr(estante_ctl, "render_screen", slow)
    c = make_conductor()
    assert entered.wait(15)
    before = frame(c)
    c.set_screen(on=False)
    release.set()
    wait_until(lambda: not c.snapshot()["busy"])
    time.sleep(0.5)
    assert frame(c) == before
    assert c.snapshot()["last_render"] is None
    assert c.fb.blank_calls == [True]


def test_dim_redraws_darker_and_validates(http, conductor):
    before = rendered(conductor)
    count = render_count(conductor)
    r = http.post("/screen", {"dim": 0.5})
    assert r.status == 202 and r.body == {"screen": {"on": True, "dim": 0.5}}
    after = renders_after(conductor, count)
    box = (50, 90, 570, 610)
    assert brightness(after, box) < brightness(before, box) * 0.7
    assert read_state(estante_ctl.state_file(conductor.cfg))["screen"]["dim"] == 0.5
    for bad in ({"dim": 1.5}, {"dim": -0.1}, {"dim": "x"}, {"dim": True}, {"on": "yes"}, [1]):
        r = http.post("/screen", bad)
        assert r.status == 400, bad
    assert http.post("/screen", raw=b"{nope").status == 400
    assert conductor.snapshot()["screen"] == {"on": True, "dim": 0.5}


def test_screen_state_survives_a_restart(make_conductor, tmp_path):
    state = tmp_path / "state" / "state.json"
    write_state({"screen": {"on": False, "dim": 0.4}}, str(state))
    c = make_conductor()
    wait_until(lambda: c.fb.blank_calls == [True])
    wait_until(lambda: c.snapshot()["now_listening"] is not None)
    time.sleep(0.5)
    assert frame(c) == bytes(1920 * 1080 * 2)
    assert c.snapshot()["screen"] == {"on": False, "dim": 0.4}
    assert c.snapshot()["last_render"] is None
    c.set_screen(on=True)
    rendered(c)
    assert c.fb.blank_calls == [True, False]


def test_state_snapshot_keys(http, conductor):
    rendered(conductor)
    r = http.get("/state")
    assert r.status == 200 and r.content_type == "application/json"
    assert set(r.body) == SNAPSHOT_KEYS
    assert set(r.body["screen"]) == {"on", "dim"}
    assert set(r.body["now_listening"]) == {"item_id", "title", "pct"}
    assert r.body["busy"] in (True, False)
    assert r.body["last_render"].endswith("+00:00")


def test_unknown_route_wrong_method_and_500(http, conductor, monkeypatch):
    r = http.get("/nope")
    assert r.status == 404 and r.content_type == "application/json"
    assert http.get("/swap").status == 405
    assert http.post("/state").status == 405
    assert http.request("DELETE", "/screen").status == 405

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(conductor, "snapshot", boom)
    r = http.get("/state")
    assert r.status == 500 and r.body == {"error": "boom"}


# pending actions


def test_user_actions_replace_each_other_and_redraw_never_wins(make_conductor):
    c = make_conductor(start=False)
    c.request("redraw")
    assert c._pending == ("redraw", None)
    c.request("swap")
    assert c._pending == ("swap", None)
    c.request("redraw")
    assert c._pending == ("swap", None)
    c.request("show", "light_novels")
    assert c._pending == ("show", 1)
    c.request("redraw")
    assert c._pending == ("show", 1)
    c.request("show", "Audiobooks")
    assert c._pending == ("show", 0)
    with pytest.raises(KeyError):
        c.request("show", "comics")
    with pytest.raises(ValueError):
        c.request("jump")


# failures


def test_poll_failure_keeps_last_frame(conductor, fake_abs, caplog):
    shown = rendered(conductor)
    count = render_count(conductor)
    caplog.set_level(logging.WARNING, logger="estante-ctl")
    fake_abs.fail_with = 500
    bump_progress(fake_abs, seconds=3 * 3600)
    time.sleep(1.2)
    assert conductor.is_alive()
    assert render_count(conductor) == count
    assert frame(conductor) == shown
    failures = [r for r in caplog.records if "Audiobookshelf" in r.getMessage()]
    assert len(failures) == 1
    fake_abs.fail_with = None
    renders_after(conductor, count)


def test_startup_without_network_retries(make_conductor, fake_abs):
    c = make_conductor(abs_url=f"http://127.0.0.1:{closed_port()}")
    server = estante_ctl.serve(c.cfg, c)
    try:
        http = Http(server)
        wait_until(lambda: frame(c) == mark_bytes())
        time.sleep(0.6)
        assert c.is_alive()
        r = http.get("/state")
        assert r.status == 200 and r.body["last_render"] is None and r.body["now_listening"] is None
        assert http.post("/badge", {"dnd": True}).status == 202
        c.client.base_url = fake_abs.base_url
        rendered(c)
        assert c.snapshot()["now_listening"]["item_id"] == IN_PROGRESS_ID
    finally:
        server.shutdown()
        server.server_close()


def test_missing_abs_url_never_crashes(make_conductor):
    c = make_conductor(abs_url="")
    wait_until(lambda: frame(c) == mark_bytes())
    time.sleep(0.5)
    assert c.is_alive() and c.snapshot()["last_render"] is None


def test_wait_for_exit_reports_a_dead_conductor(make_conductor, monkeypatch):
    c = make_conductor(start=False)

    def crash():
        raise AbsError("boom")

    monkeypatch.setattr(c, "_run", crash)
    c.start()
    c.join(timeout=5)
    assert estante_ctl.wait_for_exit(threading.Event(), c, interval=0.01) == 1
    done = threading.Event()
    done.set()
    assert estante_ctl.wait_for_exit(done, c, interval=0.01) == 0


def test_a_render_that_raises_is_retried_next_poll(make_conductor, monkeypatch):
    real = estante_ctl.render_screen
    calls = []

    def flaky(view, size):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("first render fails")
        return real(view, size)

    monkeypatch.setattr(estante_ctl, "render_screen", flaky)
    c = make_conductor()
    rendered(c)
    assert len(calls) == 2 and c.is_alive()


def test_single_wall_swap_redraws(make_conductor):
    c = make_conductor(walls=[{"name": "audiobooks", "library_id": "", "title": "The shelf"}])
    server = estante_ctl.serve(c.cfg, c)
    try:
        http = Http(server)
        before = rendered(c)
        assert c.snapshot()["walls"] == ["audiobooks"]
        count = render_count(c)
        r = http.post("/swap")
        assert r.status == 202 and r.body == {"queued": "redraw"}
        after = renders_after(c, count)
        assert after == before
        assert c.snapshot()["wall"] == "audiobooks"
        r = http.post("/show", {"screen": "light_novels"})
        assert r.status == 404 and r.body["walls"] == ["audiobooks"]
        assert http.post("/show", {"screen": "audiobooks"}).status == 202
    finally:
        server.shutdown()
        server.server_close()
