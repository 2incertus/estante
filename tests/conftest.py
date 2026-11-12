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


AUDIOBOOKS_ID = "b14de8f5-99a5-4cba-b970-6a2cb65f2993"
LIGHT_NOVELS_ID = "65bcd45d-1cce-496c-950e-4f3aabf5f5eb"


def _shelf_view(client, state_dir, library_id, title, subtitle):
    from estante_lib.abs import CoverCache
    from estante_lib.model import progress_index, shelf_counts, shelf_parts
    from estante_lib.render_spines import ColourCache, ShelfSectionView, shelf_art

    books, runs, hours, pct = shelf_parts(client.library_items(library_id), progress_index(client.me()))
    cache = CoverCache(str(state_dir / "covers"), client)
    covers, colours = shelf_art(
        books, lambda b: cache.path(b.item_id, b.updated_at, 80), ColourCache(str(state_dir / "spines.json"))
    )
    return ShelfSectionView(
        title, subtitle, books, runs, hours, pct, covers, colours, shelf_counts(books), 0, 2, {}
    )


@pytest.fixture
def shelf_view(client, tmp_path):
    """Audiobooks fixture (311 books): 12 recorded covers, 4 of them face-out books.

    ls tests/fixtures/covers | grep -v w400 lists 18 covers, 12 of them in audiobooks.json;
    4 of those have a finished or in-progress record in me.json.
    """
    view = _shelf_view(client, tmp_path, AUDIOBOOKS_ID, "The shelf", "Audiobooks, by series and author")
    assert len(view.books) == 311 and len(view.colours) == 12 and len(view.covers) == 4
    return view
