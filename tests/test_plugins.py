"""The two InkyPi plugins, called the way InkyPi calls them, against the fixture ABS."""
import logging
import os
from datetime import datetime, timezone

import pytest

from conftest import (
    AUDIOBOOKS_ID,
    INKYPI_SRC,
    LIGHT_NOVELS_ID,
    expected_now_render,
    expected_shelf_render,
    put_inkypi_on_path,
)
from estante_lib.palette import driver_palette, inky_pass, to_rgb
from estante_lib.state import read_state, write_state

if not INKYPI_SRC.is_dir():
    pytest.skip("no InkyPi dev checkout: run scripts/dev-inkypi.sh", allow_module_level=True)

put_inkypi_on_path()

from plugins.estante_lib import inkypi_glue  # noqa: E402
from plugins.now_listening.now_listening import NowListening  # noqa: E402
from plugins.the_shelf.the_shelf import TheShelf  # noqa: E402

AUDIOBOOKS_PAGES = 7  # 311 items in audiobooks.json, 50 per page
AUDIOBOOKS_LARGE_PAGES = 10  # 311 items, 32 per page


class FakeDeviceConfig:
    """The slice of InkyPi's Config the plugins use, backed by dicts."""

    def __init__(self, abs=None, abs_url=None, token="TESTTOKEN", resolution=(800, 480), **config):
        self.resolution = resolution
        self.config = {"orientation": "horizontal", "image_settings": {}}
        self.config.update(config)
        self.env = {}
        url = abs.base_url if abs is not None else abs_url
        if url:
            self.env["ABS_URL"] = url
        if token:
            self.env["ABS_TOKEN"] = token

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default={}):
        if key is None:
            return self.config
        return self.config.get(key, default)

    def load_env_key(self, key):
        return self.env.get(key)


def _assert_panel_ready(img):
    assert img.size == (800, 480) and img.mode == "RGB"
    pal = driver_palette(0.5)
    assert {c for _, c in img.getcolors(1 << 16)} <= set(pal)
    passed = inky_pass(img)
    assert not any(passed.histogram()[6:])
    assert to_rgb(passed, pal).tobytes() == img.tobytes()


def test_now_listening_generate_image_rgb_800x480(fake_abs, client, tmp_state_dir):
    img = NowListening({"id": "now_listening"}).generate_image({}, FakeDeviceConfig(abs=fake_abs))
    _assert_panel_ready(img)
    year = datetime.now(timezone.utc).year
    expected = expected_now_render(client, tmp_state_dir / "expected", year, timezone.utc)
    assert img.tobytes() == to_rgb(expected, driver_palette(0.5)).tobytes()


def test_now_listening_reads_badge_from_state(fake_abs, client, tmp_state_dir):
    badge = {"message_waiting": True, "dnd": False, "count": 2, "from": "Jasmine"}
    write_state({"badge": badge})
    img = NowListening({"id": "now_listening"}).generate_image({}, FakeDeviceConfig(abs=fake_abs))
    year = datetime.now(timezone.utc).year
    expected = expected_now_render(client, tmp_state_dir / "expected", year, timezone.utc, badge)
    assert img.tobytes() == to_rgb(expected, driver_palette(0.5)).tobytes()


def test_now_listening_uses_inky_saturation(fake_abs, tmp_state_dir):
    config = FakeDeviceConfig(abs=fake_abs, image_settings={"inky_saturation": 0.0})
    img = NowListening({"id": "now_listening"}).generate_image({}, config)
    assert {c for _, c in img.getcolors(1 << 16)} <= set(driver_palette(0.0))


def test_generate_image_raises_runtime_error_when_abs_down(tmp_state_dir):
    with pytest.raises(RuntimeError) as e:
        NowListening({"id": "now_listening"}).generate_image(
            {}, FakeDeviceConfig(abs_url="http://127.0.0.1:9")
        )
    assert "127.0.0.1" in str(e.value)


def test_shelf_raises_runtime_error_when_abs_down(tmp_state_dir):
    with pytest.raises(RuntimeError) as e:
        TheShelf({"id": "the_shelf"}).generate_image(
            {"library_id": AUDIOBOOKS_ID}, FakeDeviceConfig(abs_url="http://127.0.0.1:9")
        )
    assert "127.0.0.1" in str(e.value)


def test_vertical_orientation_rejected(fake_abs, tmp_state_dir):
    config = FakeDeviceConfig(abs=fake_abs, orientation="vertical")
    with pytest.raises(RuntimeError, match="landscape"):
        NowListening({"id": "now_listening"}).generate_image({}, config)
    with pytest.raises(RuntimeError, match="landscape"):
        TheShelf({"id": "the_shelf"}).generate_image({"library_id": AUDIOBOOKS_ID}, config)


def test_missing_creds_message(tmp_state_dir):
    with pytest.raises(RuntimeError, match="API Keys page"):
        NowListening({"id": "now_listening"}).generate_image({}, FakeDeviceConfig(token=None))
    with pytest.raises(RuntimeError, match="API Keys page"):
        TheShelf({"id": "the_shelf"}).generate_image(
            {"library_id": AUDIOBOOKS_ID}, FakeDeviceConfig(abs_url="http://127.0.0.1:9", token="")
        )


def test_shelf_requires_library(fake_abs, tmp_state_dir):
    with pytest.raises(RuntimeError, match="Choose a library"):
        TheShelf({"id": "the_shelf"}).generate_image({}, FakeDeviceConfig(abs=fake_abs))
    with pytest.raises(RuntimeError, match="Choose a library"):
        TheShelf({"id": "the_shelf"}).generate_image({"library_id": ""}, FakeDeviceConfig(abs=fake_abs))


def test_shelf_unknown_library(fake_abs, tmp_state_dir):
    with pytest.raises(RuntimeError, match="not-a-library"):
        TheShelf({"id": "the_shelf"}).generate_image(
            {"library_id": "not-a-library"}, FakeDeviceConfig(abs=fake_abs)
        )


def test_shelf_writes_page_count_and_reads_page(fake_abs, client, tmp_state_dir):
    write_state({"pages": {AUDIOBOOKS_ID: 3}})
    shelf = TheShelf({"id": "the_shelf"})
    settings = {"library_id": AUDIOBOOKS_ID, "layout": "pages"}
    img = shelf.generate_image(settings, FakeDeviceConfig(abs=fake_abs))
    _assert_panel_ready(img)
    assert read_state()["page_counts"] == {AUDIOBOOKS_ID: AUDIOBOOKS_PAGES}
    cache = tmp_state_dir / "expected"
    page4 = expected_shelf_render(client, cache, AUDIOBOOKS_ID, "The shelf", "Audiobooks, by author", "pages", 3)
    assert img.tobytes() == to_rgb(page4, driver_palette(0.5)).tobytes()

    write_state({"pages": {AUDIOBOOKS_ID: 6}, "page_counts": {AUDIOBOOKS_ID: AUDIOBOOKS_PAGES}})
    img7 = shelf.generate_image(settings, FakeDeviceConfig(abs=fake_abs))
    assert img7.tobytes() != img.tobytes()
    page7 = expected_shelf_render(client, cache, AUDIOBOOKS_ID, "The shelf", "Audiobooks, by author", "pages", 6)
    assert img7.tobytes() == to_rgb(page7, driver_palette(0.5)).tobytes()


def test_shelf_larger_pages(fake_abs, client, tmp_state_dir):
    write_state({"pages": {AUDIOBOOKS_ID: 2}})
    settings = {"library_id": AUDIOBOOKS_ID, "layout": "pages_large"}
    img = TheShelf({"id": "the_shelf"}).generate_image(settings, FakeDeviceConfig(abs=fake_abs))
    _assert_panel_ready(img)
    assert read_state()["page_counts"] == {AUDIOBOOKS_ID: AUDIOBOOKS_LARGE_PAGES}
    expected = expected_shelf_render(
        client, tmp_state_dir / "expected", AUDIOBOOKS_ID, "The shelf", "Audiobooks, by author",
        "pages", 2, cols=8, rows=4,
    )
    assert img.tobytes() == to_rgb(expected, driver_palette(0.5)).tobytes()
    normal = expected_shelf_render(
        client, tmp_state_dir / "expected", AUDIOBOOKS_ID, "The shelf", "Audiobooks, by author", "pages", 2
    )
    assert img.tobytes() != to_rgb(normal, driver_palette(0.5)).tobytes()


def test_shelf_title_and_mosaic(fake_abs, client, tmp_state_dir):
    settings = {"library_id": LIGHT_NOVELS_ID, "layout": "mosaic", "title": "Light novels"}
    img = TheShelf({"id": "the_shelf"}).generate_image(settings, FakeDeviceConfig(abs=fake_abs))
    _assert_panel_ready(img)
    assert read_state()["page_counts"] == {LIGHT_NOVELS_ID: 1}
    expected = expected_shelf_render(
        client, tmp_state_dir / "expected", LIGHT_NOVELS_ID, "Light novels", "Light Novels, by author", "mosaic", 0
    )
    assert img.tobytes() == to_rgb(expected, driver_palette(0.5)).tobytes()


def test_shelf_settings_template_lists_libraries(fake_abs, tmp_state_dir, monkeypatch):
    # InkyPi re-reads its .env on every lookup; keep a developer's .inkypi/.env out of this test.
    monkeypatch.setattr(inkypi_glue, "load_dotenv", lambda **kw: False)
    monkeypatch.setenv("ABS_URL", fake_abs.base_url)
    monkeypatch.setenv("ABS_TOKEN", "TESTTOKEN")
    t = TheShelf({"id": "the_shelf"}).generate_settings_template()
    assert [lib["name"] for lib in t["libraries"]] == ["Audiobooks", "Light Novels", "Podcasts"]
    assert t["libraries"][0] == {"id": AUDIOBOOKS_ID, "name": "Audiobooks"}
    assert t["abs_error"] == ""
    assert t["settings_template"] == "the_shelf/settings.html"


def test_shelf_settings_template_reports_abs_error(tmp_state_dir, monkeypatch):
    monkeypatch.setattr(inkypi_glue, "load_dotenv", lambda **kw: False)
    monkeypatch.setenv("ABS_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ABS_TOKEN", "TESTTOKEN")
    t = TheShelf({"id": "the_shelf"}).generate_settings_template()
    assert t["libraries"] == []
    assert "127.0.0.1" in t["abs_error"]

    monkeypatch.delenv("ABS_TOKEN")
    t = TheShelf({"id": "the_shelf"}).generate_settings_template()
    assert t["libraries"] == [] and "API Keys page" in t["abs_error"]


def test_local_year_uses_inkypi_timezone():
    year, tz = inkypi_glue.local_year(FakeDeviceConfig(timezone="America/Los_Angeles"))
    assert str(tz) == "America/Los_Angeles" and year == datetime.now(tz).year
    _, tz = inkypi_glue.local_year(FakeDeviceConfig())
    assert datetime.now(tz).utcoffset().total_seconds() == 0
    _, tz = inkypi_glue.local_year(FakeDeviceConfig(timezone="Not/AZone"))
    assert datetime.now(tz).utcoffset().total_seconds() == 0


def test_shelf_keeps_its_image_when_state_dir_is_read_only(fake_abs, tmp_state_dir, caplog):
    shelf = TheShelf({"id": "the_shelf"})
    settings = {"library_id": AUDIOBOOKS_ID, "layout": "pages"}
    first = shelf.generate_image(settings, FakeDeviceConfig(abs=fake_abs))
    caplog.set_level(logging.WARNING)
    os.chmod(tmp_state_dir, 0o500)
    try:
        img = shelf.generate_image(settings, FakeDeviceConfig(abs=fake_abs))
    finally:
        os.chmod(tmp_state_dir, 0o700)
    assert img.tobytes() == first.tobytes()
    assert "page count" in caplog.text


def test_shelf_settings_template_uses_a_short_timeout(fake_abs, tmp_state_dir, monkeypatch):
    monkeypatch.setattr(inkypi_glue, "load_dotenv", lambda **kw: False)
    monkeypatch.setenv("ABS_URL", fake_abs.base_url)
    monkeypatch.setenv("ABS_TOKEN", "TESTTOKEN")
    made = []
    real = inkypi_glue.AbsClient

    def recording(*args, **kwargs):
        client = real(*args, **kwargs)
        made.append(client)
        return client

    monkeypatch.setattr(inkypi_glue, "AbsClient", recording)
    TheShelf({"id": "the_shelf"}).generate_settings_template()
    assert [c.timeout for c in made] == [5]
    TheShelf({"id": "the_shelf"}).generate_image({"library_id": AUDIOBOOKS_ID}, FakeDeviceConfig(abs=fake_abs))
    # drawing a screen keeps the longer default
    assert made[-1].timeout == 15.0
