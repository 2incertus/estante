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
