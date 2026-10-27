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
