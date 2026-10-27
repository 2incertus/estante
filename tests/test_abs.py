import os

import pytest

from estante_lib.abs import AbsClient, AbsError, CoverCache, creds_from_env

AUDIOBOOKS = "b14de8f5-99a5-4cba-b970-6a2cb65f2993"
JADE_CITY = "616bf0b9-2c0c-472a-b975-72f35077d199"
# A first-page audiobook with a recorded cover: ls tests/fixtures/covers
COVER_ID = "1d788522-5f4a-40c4-a57c-98d298e08ab7"


def test_bearer_header_is_sent(fake_abs, client):
    client.libraries()
    # FakeAbs returned 200 only because the header matched
    assert fake_abs.calls[-1] == "/api/libraries"


def test_libraries_returns_list(client):
    libs = client.libraries()
    # jq 'length' tests/fixtures/libraries.json
    assert isinstance(libs, list) and len(libs) == 3


def test_library_items_returns_results_list(client):
    items = client.library_items(AUDIOBOOKS)
    # jq '.results | length' tests/fixtures/audiobooks.json
    assert len(items) == 311
    assert "chapters" not in items[0]["media"]


def test_progress_404_returns_none(client):
    assert client.progress("does-not-exist") is None


def test_progress_returns_record(client):
    prog = client.progress(JADE_CITY)
    # jq '.currentTime' tests/fixtures/progress_616bf0b9-2c0c-472a-b975-72f35077d199.json
    assert prog["currentTime"] == 102.712


def test_item_carries_chapters(client):
    it = client.item(JADE_CITY)
    # jq '.media.chapters | length' tests/fixtures/item_616bf0b9-2c0c-472a-b975-72f35077d199.json
    assert len(it["media"]["chapters"]) == 61


def test_item_404_raises_abserror(client):
    with pytest.raises(AbsError):
        client.item("no-such-item")


def test_items_in_progress_returns_library_items(fake_abs, client):
    items = client.items_in_progress(1)
    # jq '.libraryItems[0].id' tests/fixtures/in_progress.json
    assert items[0]["id"] == "4aa1594d-b6ce-441f-9ab3-951ff1340009"
    assert fake_abs.calls[-1] == "/api/me/items-in-progress"


def test_me_returns_media_progress(client):
    # jq '.mediaProgress | length' tests/fixtures/me.json
    assert len(client.me()["mediaProgress"]) == 100


def test_connection_refused_raises_abserror():
    c = AbsClient("http://127.0.0.1:9", "x", timeout=1)
    with pytest.raises(AbsError) as e:
        c.me()
    assert "127.0.0.1" in str(e.value)


def test_401_raises_abserror(fake_abs):
    c = AbsClient(fake_abs.base_url, "WRONG")
    with pytest.raises(AbsError) as e:
        c.me()
    assert "401" in str(e.value)


def test_server_error_raises_abserror(fixtures_dir):
    from fake_abs import FakeAbs

    with FakeAbs(fixtures_dir, fail_with=500) as fake:
        c = AbsClient(fake.base_url, "TESTTOKEN")
        with pytest.raises(AbsError):
            c.libraries()
        with pytest.raises(AbsError):
            c.progress(JADE_CITY)


def test_cover_bytes_is_jpeg_and_404_is_none(client):
    body = client.cover_bytes(COVER_ID, 80)
    assert body[:2] == b"\xff\xd8"
    assert client.cover_bytes("no-such-item", 80) is None


def test_cover_cache_fetches_once_and_prunes(fake_abs, client, tmp_path):
    cache = CoverCache(str(tmp_path), client)
    first = cache.path(COVER_ID, 1, 80)
    again = cache.path(COVER_ID, 1, 80)
    assert first == again and os.path.exists(first)
    assert os.path.basename(first) == f"{COVER_ID}-1-w80.jpg"
    assert fake_abs.calls.count(f"/api/items/{COVER_ID}/cover") == 1
    newer = cache.path(COVER_ID, 2, 80)
    assert not os.path.exists(first) and os.path.exists(newer)


def test_cover_cache_keeps_other_widths(client, tmp_path):
    cache = CoverCache(str(tmp_path), client)
    small = cache.path(COVER_ID, 1, 80)
    other = tmp_path / f"{COVER_ID}-1-w400.jpg"
    other.write_bytes(b"x")
    cache.path(COVER_ID, 2, 80)
    assert not os.path.exists(small) and other.exists()


def test_cover_cache_missing_cover_returns_none(client, tmp_path):
    assert CoverCache(str(tmp_path), client).path("no-such-item", 1, 80) is None


def test_cover_cache_never_raises_when_abs_down(tmp_path):
    c = AbsClient("http://127.0.0.1:9", "x", timeout=1)
    assert CoverCache(str(tmp_path), c).path(COVER_ID, 1, 80) is None


def test_creds_from_env_missing_raises():
    with pytest.raises(AbsError) as e:
        creds_from_env({})
    assert str(e.value) == "ABS_URL and ABS_TOKEN are not set"


def test_creds_from_env_reads_both():
    assert creds_from_env({"ABS_URL": "http://h:1", "ABS_TOKEN": "t"}) == ("http://h:1", "t")
