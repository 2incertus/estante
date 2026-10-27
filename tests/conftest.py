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


def _shelf_view(client, cache_dir, library_id, title, subtitle, n_covers, layout):
    from estante_lib.model import build_shelf, progress_index
    from estante_lib.render_shelf import ShelfView

    books = build_shelf(client.library_items(library_id), progress_index(client.me()))
    covers = {}
    for book in books[:n_covers]:
        img = _cover_image(client, cache_dir, book.item_id, book.updated_at, 80)
        if img is not None:
            covers[book.item_id] = img
    return ShelfView(title, subtitle, books, covers, page=0, layout=layout)


@pytest.fixture
def shelf_view(client, tmp_path):
    """Audiobooks fixture, covers for the first 12 books (all recorded), page 0."""
    view = _shelf_view(
        client, tmp_path / "covers", AUDIOBOOKS_ID, "The shelf", "Audiobooks, by author", 12, "pages"
    )
    assert len(view.covers) == 12
    return view


@pytest.fixture
def novels_view(client, tmp_path):
    """Light novels fixture (597 books), covers for the first 6 (all recorded), mosaic."""
    view = _shelf_view(
        client, tmp_path / "covers", LIGHT_NOVELS_ID, "Light novels", "Light Novels, by author", 6, "mosaic"
    )
    assert len(view.covers) == 6
    return view


INKYPI_DIR = REPO / ".inkypi"
INKYPI_SRC = INKYPI_DIR / "src"


def put_inkypi_on_path():
    """Make InkyPi's `plugins` package (with our symlinked plugins) importable."""
    src = str(INKYPI_SRC)
    if src not in sys.path:
        sys.path.insert(1, src)


def expected_now_render(client, cache_dir, year, tz, badge=None):
    """Now listening rendered directly from estante_lib, as the plugin should."""
    from estante_lib.model import finished_in_year, pick_now_listening
    from estante_lib.render_now import NowListeningView, render_now_listening
    from estante_lib.state import DEFAULT_BADGE

    nl = pick_now_listening(client)
    cover = _cover_image(client, cache_dir, nl.item_id, nl.updated_at, 400) if nl else None
    finished = finished_in_year(client.me(), year, tz)
    view = NowListeningView(nl, cover, finished, year, dict(badge or DEFAULT_BADGE))
    return render_now_listening(view)


def expected_shelf_render(client, cache_dir, library_id, title, subtitle, layout, page, cols=10, rows=5):
    """The shelf rendered directly from estante_lib, as the plugin should."""
    from estante_lib.model import build_shelf, progress_index
    from estante_lib.render_shelf import ShelfView, page_books, render_shelf

    books = build_shelf(client.library_items(library_id), progress_index(client.me()))
    shown = books if layout == "mosaic" else page_books(books, page, cols * rows)[0]
    covers = {}
    for book in shown:
        img = _cover_image(client, cache_dir, book.item_id, book.updated_at, 80)
        if img is not None:
            covers[book.item_id] = img
    view = ShelfView(title, subtitle, books, covers, page=page, layout=layout, cols=cols, rows=rows)
    return render_shelf(view)
