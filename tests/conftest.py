import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "plugins"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_abs import FakeAbs  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def fixtures_dir():
    return FIXTURES


@pytest.fixture
def fake_abs():
    with FakeAbs(FIXTURES) as fake:
        yield fake


@pytest.fixture
def client(fake_abs):
    from estante_lib.abs import AbsClient

    return AbsClient(fake_abs.base_url, "TESTTOKEN", timeout=5)


@pytest.fixture
def tmp_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ESTANTE_STATE_DIR", str(tmp_path))
    return tmp_path


def _load(name):
    with open(FIXTURES / name, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def audiobooks():
    return _load("audiobooks.json")["results"]


@pytest.fixture
def me():
    return _load("me.json")


@pytest.fixture
def jade_item():
    return _load("item_616bf0b9-2c0c-472a-b975-72f35077d199.json")


def _cover_image(client, cache_dir, item_id, updated_at, width):
    from PIL import Image

    from estante_lib.abs import CoverCache

    path = CoverCache(str(cache_dir), client).path(item_id, updated_at, width)
    if not path:
        return None
    with Image.open(path) as img:
        return img.convert("RGB")


def _overgeared_view(client, cache_dir):
    from estante_lib.model import pick_now_listening
    from estante_lib.render_now import NowListeningView
    from estante_lib.state import DEFAULT_BADGE

    nl = pick_now_listening(client)
    assert nl.item_id == "4aa1594d-b6ce-441f-9ab3-951ff1340009"
    assert nl.chapter_count == 820
    cover = _cover_image(client, cache_dir, nl.item_id, nl.updated_at, 400)
    return NowListeningView(nl, cover, 7, 2026, dict(DEFAULT_BADGE))


@pytest.fixture
def now_view(client, tmp_path):
    return _overgeared_view(client, tmp_path / "covers")


@pytest.fixture
def overgeared_view(client, tmp_path):
    return _overgeared_view(client, tmp_path / "covers")
