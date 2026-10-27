import json
from datetime import timezone
from pathlib import Path

from estante_lib.model import (
    STATUS_LISTENING,
    STATUS_READ,
    STATUS_UNREAD,
    NowListening,
    build_shelf,
    chapter_index,
    chapter_line,
    finished_in_year,
    page_count,
    pick_now_listening,
    progress_index,
    series_key,
    series_line,
    shelf_counts,
    shelf_sort_key,
    status_of,
    time_left,
    visible_key,
)

OVERGEARED = "4aa1594d-b6ce-441f-9ab3-951ff1340009"
SHADOW_SLAVE_1 = "90d6f040-088b-4245-a5ac-43d97ac9475d"


def test_status_thresholds():
    prog = {
        "a": {"isFinished": True},
        "b": {"isFinished": False, "progress": 0.5},
        "c": {"isFinished": False, "progress": 0.005},
    }
    assert status_of("a", prog) == STATUS_READ
    assert status_of("b", prog) == STATUS_LISTENING
    assert status_of("c", prog) == STATUS_UNREAD
    assert status_of("zzz", prog) == STATUS_UNREAD


def test_status_exactly_at_threshold_is_unread():
    assert status_of("x", {"x": {"isFinished": False, "progress": 0.01}}) == STATUS_UNREAD


def test_progress_index_keys_by_library_item(me):
    idx = progress_index(me)
    # jq '[.mediaProgress[].libraryItemId] | unique | length' tests/fixtures/me.json
    assert len(idx) == 100
    # jq '.mediaProgress[] | select(.libraryItemId=="616bf0b9-2c0c-472a-b975-72f35077d199") | .currentTime' tests/fixtures/me.json
    assert idx["616bf0b9-2c0c-472a-b975-72f35077d199"]["currentTime"] == 102.712


def test_series_key_orders_numbers_numerically():
    assert series_key("The Wheel of Time #2") < series_key("The Wheel of Time #10")
    assert series_key("The Wheel of Time #10") == ("the wheel of time", 10.0)
    assert series_key("Standalone") == ("standalone", 0.0)
    assert series_key("") == ("", 0.0)


def test_series_line():
    assert series_line("The Green Bone Saga #1") == "The Green Bone Saga, book 1"
    assert series_line("Overlord #02") == "Overlord, book 2"
    assert series_line("Cradle #1.5") == "Cradle, book 1.5"
    assert series_line("#02") == "book 2"
    assert series_line("Discworld") == "Discworld"
    assert series_line("") == ""


def test_shelf_sort_key_blank_author_last():
    a = {"media": {"metadata": {"authorNameLF": "Lee, Fonda", "seriesName": "", "title": "Jade City"}}}
    b = {"media": {"metadata": {"authorNameLF": "", "seriesName": "", "title": "Aaa"}}}
    assert shelf_sort_key(a) < shelf_sort_key(b)


def test_shelf_sort_uses_title_ignore_prefix():
    a = {"media": {"metadata": {"authorNameLF": "X", "title": "The Zebra", "titleIgnorePrefix": "Zebra, The"}}}
    b = {"media": {"metadata": {"authorNameLF": "X", "title": "Moon"}}}
    assert shelf_sort_key(b) < shelf_sort_key(a)


def test_blank_author_sorts_last(audiobooks, me):
    books = build_shelf(audiobooks, progress_index(me))
    blanks = [i for i, b in enumerate(books) if b.author == ""]
    # jq '[.results[] | select((.media.metadata.authorNameLF // "") == "")] | length' tests/fixtures/audiobooks.json
    assert len(blanks) == 1
    assert blanks[0] == len(books) - len(blanks)


def test_build_shelf_orders_series_within_author(audiobooks, me):
    books = build_shelf(audiobooks, progress_index(me))
    jade = [b for b in books if b.series.startswith("The Green Bone Saga")]
    # jq -r '.results[] | .media.metadata.seriesName | select(startswith("The Green Bone Saga"))' tests/fixtures/audiobooks.json
    assert [b.series for b in jade] == [
        "The Green Bone Saga #1",
        "The Green Bone Saga #2",
        "The Green Bone Saga #3",
    ]
    first = jade[0]
    assert first.item_id == "616bf0b9-2c0c-472a-b975-72f35077d199"
    assert first.title == "Jade City" and first.author == "Fonda Lee"
    # jq '.results[] | select(.id=="616bf0b9-2c0c-472a-b975-72f35077d199") | .updatedAt' tests/fixtures/audiobooks.json
    assert first.updated_at == 1780948485055


def test_shelf_counts_match_fixture(audiobooks, me):
    c = shelf_counts(build_shelf(audiobooks, progress_index(me)))
    # jq -n --slurpfile me tests/fixtures/me.json --slurpfile ab tests/fixtures/audiobooks.json
    #   '($me[0].mediaProgress | map({key: .libraryItemId, value: .}) | from_entries) as $p
    #    | $ab[0].results | map($p[.id])
    #    | {read: map(select(. != null and .isFinished)) | length,
    #       listening: map(select(. != null and (.isFinished|not) and .progress > 0.01)) | length}'
    # -> read 73, listening 13; unread = 311 - 73 - 13 = 225
    assert c == {"read": 73, "listening": 13, "unread": 225, "total": 311}


def test_page_count():
    assert page_count(0, 50) == 1
    assert page_count(50, 50) == 1
    assert page_count(51, 50) == 2
    assert page_count(311, 50) == 7


def test_chapter_index_bounds(jade_item):
    ch = jade_item["media"]["chapters"]
    assert chapter_index(ch, 0) == 0
    assert chapter_index(ch, -5) == 0
    assert chapter_index(ch, ch[5]["start"]) == 5
    assert chapter_index(ch, ch[5]["end"] - 0.01) == 5
    # jq '.media.chapters | length' tests/fixtures/item_616bf0b9-2c0c-472a-b975-72f35077d199.json -> 61
    assert chapter_index(ch, ch[-1]["end"] + 100) == 60
    assert chapter_index([], 10) == -1


def test_chapter_line_uses_title_when_not_numbered():
    assert chapter_line("Chapter 1", 0, 61) == "Chapter 1 of 61"
    assert chapter_line("chapter 1 ", 0, 61) == "Chapter 1 of 61"
    assert chapter_line("Chapter 001", 0, 61) == "Chapter 1 of 61"
    assert chapter_line("Chapter 500", 47, 820) == "Chapter 500 (48 of 820)"
    assert chapter_line("Prologue", 0, 30) == "Prologue (1 of 30)"
    assert chapter_line("", 2, 10) == "Chapter 3 of 10"


def test_time_left():
    assert time_left(68839.08, 102.7) == "19h 05m"
    assert time_left(3000, 480) == "42m"
    assert time_left(100, 200) == "0m"
    assert time_left(3600, 0) == "1h 00m"


def test_finished_in_year(me):
    # jq '.mediaProgress | map(select(.isFinished and .finishedAt >= 1767225600000)) | length' tests/fixtures/me.json
    assert finished_in_year(me, 2026, timezone.utc) == 7
    # jq '.mediaProgress | map(select(.isFinished and .finishedAt >= 1735689600000 and .finishedAt < 1767225600000)) | length' tests/fixtures/me.json
    assert finished_in_year(me, 2025, timezone.utc) == 61


def test_pick_now_listening_uses_in_progress(client, fake_abs):
    nl = pick_now_listening(client)
    # jq -r '.libraryItems[0].id' tests/fixtures/in_progress.json
    assert nl.item_id == OVERGEARED and nl.finished is False
    # jq '.media.chapters | length' tests/fixtures/item_4aa1594d-b6ce-441f-9ab3-951ff1340009.json
    assert 0 < nl.pct < 1 and nl.chapter_count == 820
    assert nl.title == "Overgeared" and nl.author == "Park Saenal"
    assert nl.narrator == "BookTTS AI cast" and nl.series == ""
    # jq '.libraryItems[0].media.duration' tests/fixtures/in_progress.json
    assert nl.duration == 583287.581035
    # jq '.currentTime' tests/fixtures/progress_4aa1594d-b6ce-441f-9ab3-951ff1340009.json
    assert nl.current_time == 28095.155
    assert fake_abs.calls == [
        "/api/me/items-in-progress",
        f"/api/items/{OVERGEARED}",
        f"/api/me/progress/{OVERGEARED}",
    ]


def test_pick_falls_back_to_last_finished(client, fake_abs):
    fake_abs.override("/api/me/items-in-progress", {"libraryItems": []})
    nl = pick_now_listening(client)
    assert nl.finished is True and nl.pct == 1.0
    # jq -r '.mediaProgress | map(select(.isFinished)) | max_by([.finishedAt, .lastUpdate]) | .libraryItemId' tests/fixtures/me.json
    assert nl.item_id == SHADOW_SLAVE_1
    # jq -c '.media.metadata | {title, authors: [.authors[].name], narrators, series}' tests/fixtures/item_90d6f040-088b-4245-a5ac-43d97ac9475d.json
    assert nl.title == "Shadow Slave 1" and nl.author == "Guiltythree"
    assert nl.narrator == "Reagan Boggs" and nl.series == "A Slave of the Shadows #1"
    # jq '.mediaProgress[] | select(.libraryItemId=="90d6f040-088b-4245-a5ac-43d97ac9475d") | .finishedAt, .duration' tests/fixtures/me.json
    assert nl.finished_at == 1778635227000
    assert nl.duration == 57691.64916499997 and nl.current_time == nl.duration
    # jq '.media.chapters | length' tests/fixtures/item_90d6f040-088b-4245-a5ac-43d97ac9475d.json
    assert nl.chapter_count == 95 and nl.chapter_idx == 94


def test_pick_returns_none_without_progress(client, fake_abs):
    fake_abs.override("/api/me/items-in-progress", {"libraryItems": []})
    fake_abs.override("/api/me", {"mediaProgress": []})
    assert pick_now_listening(client) is None


def _nl(current_time, chapters):
    return NowListening(
        item_id="x", title="T", author="A", narrator="N", series="", updated_at=1,
        duration=1000.0, current_time=current_time, chapters=chapters,
        finished=False, finished_at=None, last_update=0,
    )


def test_now_listening_pct_clamped_and_zero_duration():
    assert _nl(2000, []).pct == 1.0
    assert _nl(-5, []).pct == 0.0
    zero = _nl(10, [])
    zero.duration = 0
    assert zero.pct == 0.0
    assert _nl(10, []).chapter_title == "" and _nl(10, []).chapter_idx == -1


def test_visible_key_changes_on_chapter_and_percent_only():
    chapters = [
        {"start": 0, "end": 503, "title": "Chapter 1"},
        {"start": 503, "end": 1000, "title": "Chapter 2"},
    ]
    badge = {"message_waiting": False, "dnd": False}
    a = visible_key(_nl(500.0, chapters), badge)
    b = visible_key(_nl(501.0, chapters), badge)     # same chapter, same 50%
    c = visible_key(_nl(504.0, chapters), badge)     # chapter 2, still 50%
    d = visible_key(_nl(511.0, chapters), badge)     # chapter 2, 51%
    assert a == b
    assert a != c and c != d
    assert visible_key(_nl(500.0, chapters), {"message_waiting": True}) != a
    assert visible_key(_nl(500.0, chapters), {"dnd": True}) != a
    assert visible_key(None, {}) == ("none", -1, 0, False, False, False)


def test_last_listened_with_zero_duration_record_is_complete(client, fake_abs, me):
    # Most finished records carry duration 0:
    # jq '.mediaProgress | map(select(.isFinished and .duration == 0)) | length' tests/fixtures/me.json
    records = [dict(p) for p in me["mediaProgress"]]
    for p in records:
        if p["libraryItemId"] == SHADOW_SLAVE_1:
            p["duration"] = 0
    fake_abs.override("/api/me/items-in-progress", {"libraryItems": []})
    fake_abs.override("/api/me", {"mediaProgress": records})
    nl = pick_now_listening(client)
    assert nl.item_id == SHADOW_SLAVE_1 and nl.finished is True
    assert nl.pct == 1.0
    assert nl.chapter_idx == nl.chapter_count - 1
    # jq '.media.chapters[-1].end' tests/fixtures/item_90d6f040-088b-4245-a5ac-43d97ac9475d.json
    assert nl.duration == nl.current_time == 57697.80245099999


def test_finished_pct_is_full_even_without_duration():
    nl = _nl(0, [])
    nl.finished = True
    nl.duration = 0
    assert nl.pct == 1.0


PODCAST_ITEM = "58ccf7ab-c342-4e44-90bd-4f8df7819e16"


def test_podcast_in_progress_is_skipped(client, fake_abs):
    in_progress = json.loads((Path(__file__).parent / "fixtures" / "in_progress.json").read_text())
    podcast = {"id": PODCAST_ITEM, "mediaType": "podcast", "media": {"metadata": {"title": "A show"}}}
    fake_abs.override("/api/me/items-in-progress", {"libraryItems": [podcast] + in_progress["libraryItems"]})
    nl = pick_now_listening(client)
    # jq -r '.libraryItems[0].id' tests/fixtures/in_progress.json
    assert nl.item_id == OVERGEARED and nl.finished is False


def test_finished_podcast_episode_is_not_last_listened(client, fake_abs, me):
    # me.json holds one podcast episode record:
    # jq -c '.mediaProgress | map(select(.episodeId != null)) | map(.libraryItemId)' tests/fixtures/me.json
    records = [dict(p) for p in me["mediaProgress"]]
    episode = next(p for p in records if p.get("episodeId"))
    episode.update(isFinished=True, finishedAt=1790000000000, lastUpdate=1790000000000)
    fake_abs.override("/api/me/items-in-progress", {"libraryItems": [
        {"id": PODCAST_ITEM, "mediaType": "podcast", "media": {"metadata": {"title": "A show"}}}
    ]})
    fake_abs.override("/api/me", {"mediaProgress": records})
    nl = pick_now_listening(client)
    # jq -r '.mediaProgress | map(select(.isFinished)) | max_by([.finishedAt, .lastUpdate]) | .libraryItemId' tests/fixtures/me.json
    assert nl.item_id == SHADOW_SLAVE_1 and nl.finished is True
