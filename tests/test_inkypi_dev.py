"""The plugins inside a real InkyPi checkout in dev mode (mock display), in-process.

InkyPi's `inkypi.py --dev` hard-codes port 8080, so instead of a subprocess this test
imports the same app with `--dev` in argv and drives it through Flask's test client.
InkyPi's dependencies (flask, waitress, pi-heif, pytz) come from .inkypi/venv, which
scripts/dev-inkypi.sh builds with the same Python as .venv. The checkout's
device_dev.json, .env and current image are restored afterwards.
"""
import json
import logging
import sys
from datetime import datetime, timezone

import pytest
from PIL import Image

from fake_abs import FakeAbs

from conftest import (
    AUDIOBOOKS_ID,
    FIXTURES,
    INKYPI_DIR,
    INKYPI_SRC,
    LIGHT_NOVELS_ID,
    expected_now_render,
    expected_shelf_render,
    put_inkypi_on_path,
)
from estante_lib.abs import AbsClient
from estante_lib.palette import driver_palette, inky_pass, to_rgb
from estante_lib.state import read_state

pytestmark = pytest.mark.skipif(
    not INKYPI_SRC.is_dir(), reason="no InkyPi dev checkout: run scripts/dev-inkypi.sh"
)

INSTANCES = [
    ("now_listening", "Now listening", {}),
    ("the_shelf", "The shelf", {"library_id": AUDIOBOOKS_ID, "layout": "pages", "title": "The shelf"}),
    ("the_shelf", "Light novels", {"library_id": LIGHT_NOVELS_ID, "layout": "mosaic", "title": "Light novels"}),
]
# Files inside the checkout that dev mode writes and this test puts back.
RESTORED = [
    INKYPI_DIR / ".env",
    INKYPI_SRC / "config" / "device_dev.json",
    INKYPI_SRC / "static" / "images" / "current_image.png",
]


def _venv_site_packages():
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site = INKYPI_DIR / "venv" / "lib" / version / "site-packages"
    if not site.is_dir():
        pytest.fail(f"{site} missing: run scripts/dev-inkypi.sh (it builds .inkypi/venv with .venv's Python)")
    return str(site)


@pytest.fixture(scope="module")
def dev(tmp_path_factory):
    """InkyPi imported in dev mode against a fixture ABS; yields (inkypi module, ABS client)."""
    if "inkypi" in sys.modules:
        pytest.fail("InkyPi can be imported once per process")
    saved = {path: path.read_bytes() if path.exists() else None for path in RESTORED}
    root = logging.getLogger()
    root_handlers, root_level = list(root.handlers), root.level
    site = _venv_site_packages()
    module = None
    with FakeAbs(FIXTURES) as fake, pytest.MonkeyPatch.context() as mp:
        sys.path.append(site)  # last, so .venv's own packages win
        put_inkypi_on_path()
        mp.setenv("ESTANTE_STATE_DIR", str(tmp_path_factory.mktemp("state")))
        mp.setattr(sys, "argv", ["inkypi.py", "--dev"])
        mp.chdir(INKYPI_SRC)
        (INKYPI_DIR / ".env").write_text(f"ABS_URL={fake.base_url}\nABS_TOKEN=TESTTOKEN\n")
        try:
            import inkypi as module

            module.refresh_task.start()
            yield module, AbsClient(fake.base_url, "TESTTOKEN", timeout=5)
        finally:
            if module is not None:
                module.refresh_task.stop()
            for path, data in saved.items():
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(data)
            sys.path.remove(site)
            # InkyPi's logging.conf replaces the root handlers and disables existing loggers.
            root.handlers[:] = root_handlers
            root.setLevel(root_level)
            for logger in logging.Logger.manager.loggerDict.values():
                if isinstance(logger, logging.Logger):
                    logger.disabled = False


def _add_instance(http, plugin_id, name, settings):
    refresh = {
        "playlist": "Default",
        "instance_name": name,
        "refreshType": "interval",
        "unit": "minute",
        "interval": 5,
    }
    form = {"plugin_id": plugin_id, "refresh_settings": json.dumps(refresh), **settings}
    resp = http.post("/add_plugin", data=form)
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["success"] is True


def _display(http, plugin_id, name):
    latest = INKYPI_SRC / "mock_display_output" / "latest.png"
    latest.unlink(missing_ok=True)
    resp = http.post(
        "/display_plugin_instance",
        json={"playlist_name": "Default", "plugin_id": plugin_id, "plugin_instance": name},
    )
    assert resp.status_code == 200, resp.get_json()
    with Image.open(latest) as img:
        return img.convert("RGB")


def test_inkypi_dev_renders_identical_images(dev, tmp_path):
    inkypi, client = dev
    http = inkypi.app.test_client()
    for plugin_id, name, settings in INSTANCES:
        _add_instance(http, plugin_id, name, settings)

    pal = driver_palette(0.5)
    cache = tmp_path / "expected"
    year = datetime.now(timezone.utc).year  # device_dev.json sets no timezone: UTC
    expected = {
        "Now listening": expected_now_render(client, cache, year, timezone.utc),
        "The shelf": expected_shelf_render(
            client, cache, AUDIOBOOKS_ID, "The shelf", "Audiobooks, by author", "pages", 0
        ),
        "Light novels": expected_shelf_render(
            client, cache, LIGHT_NOVELS_ID, "Light novels", "Light Novels, by author", "mosaic", 0
        ),
    }
    for plugin_id, name, _ in INSTANCES:
        shown = _display(http, plugin_id, name)
        assert shown.size == (800, 480)
        assert shown.tobytes() == to_rgb(expected[name], pal).tobytes(), name
        passed = inky_pass(shown)
        assert not any(passed.histogram()[6:])
        assert passed.tobytes() == expected[name].tobytes(), name

    assert read_state()["page_counts"] == {AUDIOBOOKS_ID: 7, LIGHT_NOVELS_ID: 1}


def test_inkypi_dev_settings_pages(dev):
    inkypi, _ = dev
    http = inkypi.app.test_client()
    page = http.get("/plugin/the_shelf")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Light Novels" in html and f'value="{AUDIOBOOKS_ID}"' in html
    assert 'name="layout"' in html and "Whole library on one screen" in html
    assert "pluginSettings.library_id" in html

    _add_instance(http, "the_shelf", "Edit check", INSTANCES[2][2])
    edit = http.get("/plugin/the_shelf?instance=Edit%20check").get_data(as_text=True)
    assert f'"library_id": "{LIGHT_NOVELS_ID}"' in edit
    assert '"layout": "mosaic"' in edit

    now = http.get("/plugin/now_listening")
    assert now.status_code == 200
    assert "API Keys" in now.get_data(as_text=True)

    home = http.get("/").get_data(as_text=True)
    assert "Now listening" in home and "The shelf" in home
