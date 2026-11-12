"""The shelf of spines: runs by series and author, packing into sections, spines and face-outs."""
import json
import time

import pytest
from PIL import Image, ImageDraw

from estante_lib import render_spines
from estante_lib.model import (
    STATUS_LISTENING,
    STATUS_READ,
    STATUS_UNREAD,
    Run,
    ShelfBook,
    book_hours,
    build_shelf,
    progress_index,
    shelf_counts,
    shelf_runs,
    surname,
)
from estante_lib.render_spines import (
    AUTHOR_MIN_W,
    BOARD_COLOUR,
    BOARD_H,
    BOARD_Y,
    LABEL_COLOUR,
    LINE_W,
    PLINTH,
    TITLE_PAD,
    TITLE_ROOM,
    USABLE_H,
    ColourCache,
    Piece,
    ShelfSectionView,
    draw_face,
    draw_spine,
    piece_for,
    plan_sections,
    render_section,
    section_of,
    spine_colour,
    shelf_title,
    spine_layout,
    spine_title,
)
from estante_lib.text import ELLIPSIS, SERIF, font, text_width
from estante_lib.theme import DARK, PAPER

W = 1230
OVERGEARED = "4aa1594d-b6ce-441f-9ab3-951ff1340009"


def _book(item_id, title="t", author="Ann Author", series="", status=STATUS_UNREAD):
    return ShelfBook(item_id, title, author, series, 1, status)


def _item(book, duration=36000, title_ignore=None):
    return {
        "id": book.item_id,
        "media": {
            "duration": duration,
            "metadata": {
                "title": book.title,
                "titleIgnorePrefix": title_ignore or book.title,
                "authorName": book.author,
                "seriesName": book.series,
            },
        },
    }


def _runs_of(books, progress=None, durations=None):
    items = {b.item_id: _item(b, (durations or {}).get(b.item_id, 36000)) for b in books}
    return shelf_runs(books, items, progress or {})


@pytest.fixture(scope="module")
def library():
    from conftest import FIXTURES

    items = json.loads((FIXTURES / "audiobooks.json").read_text())["results"]
    me = json.loads((FIXTURES / "me.json").read_text())
    progress = progress_index(me)
    books = build_shelf(items, progress)
    runs = shelf_runs(books, {it["id"]: it for it in items}, progress)
    return books, runs, book_hours(items), progress


# runs


def test_fixture_runs_lead_with_the_books_in_progress(library):
    _, runs, _, _ = library
    # in-progress records by lastUpdate, newest first (me.json, isFinished false, progress > 0.01):
    # Overgeared (Park Saenal, no series), Shadow Slave 5 (A Slave of the Shadows #5),
    # The Eye of the World (The Wheel of Time #1), Infinite Mage (Kim Chi-Woo, no series),
    # 05 The Renaissance (The Story of Civilization #5), Dungeon Crawler Carl (#1)
    assert [r.key for r in runs[:6]] == [
        "author:park saenal",
        "series:a slave of the shadows",
        "series:the wheel of time",
        "author:kim chi-woo",
        "series:the story of civilization",
        "series:dungeon crawler carl",
    ]
    assert [r.kind for r in runs[:3]] == ["author", "series", "series"]


def test_series_run_in_sequence_order_with_its_label(library):
    _, runs, _, _ = library
    wot = next(r for r in runs if r.key == "series:the wheel of time")
    # jq '[.results[]|select(.media.metadata.seriesName|test("The Wheel of Time #"))]|length' -> 15,
    # all finished except #1 The Eye of the World (progress 0.0106)
    assert len(wot.books) == 15
    assert [b.title for b in wot.books[:3]] == ["New Spring", "The Eye of the World", "The Great Hunt"]
    assert wot.books[-1].title == "A Memory of Light"
    assert wot.label == "THE WHEEL OF TIME · 14 of 15"


def test_every_book_lands_in_exactly_one_run(library):
    books, runs, _, _ = library
    placed = [b.item_id for r in runs for b in r.books]
    assert sorted(placed) == sorted(b.item_id for b in books)
    assert len(placed) == 311


def test_single_volume_series_joins_its_author_and_author_runs_sort_by_title():
    books = [
        _book("1", "Zeta", "Ann Author", "Lonely Saga #3"),
        _book("2", "Alpha", "Ann Author"),
        _book("3", "Two", "Bob Other", "Pair #2"),
        _book("4", "One", "Bob Other", "Pair #1"),
    ]
    runs = _runs_of(books)
    assert [(r.key, r.kind, [b.title for b in r.books]) for r in runs] == [
        ("author:ann author", "author", ["Alpha", "Zeta"]),
        ("series:pair", "series", ["One", "Two"]),
    ]
    assert runs[0].label == "ANN AUTHOR"
    assert runs[1].label == "PAIR · 0 of 2"


def test_a_book_in_two_series_joins_the_numbered_larger_one():
    books = [
        _book("1", "B1", "J", "Harry Potter #1, Harry Potter (Full-Cast Editions) #1"),
        _book("2", "B2", "J", "Harry Potter (Full-Cast Editions) #2, Harry Potter #2"),
        _book("3", "W1", "S", "The Stormlight Archive #3, The Cosmere"),
        _book("4", "W2", "S", "The Stormlight Archive #4, The Cosmere"),
    ]
    runs = _runs_of(books)
    assert {r.key: [b.title for b in r.books] for r in runs} == {
        "series:harry potter": ["B1", "B2"],
        "series:the stormlight archive": ["W1", "W2"],
    }


def test_activity_order_then_surname():
    books = [
        _book("a", "A", "Zed Zulu"),
        _book("b", "B", "Amy Adams"),
        _book("c", "C", "Max Mid", status=STATUS_READ),
        _book("d", "D", "Old Read", status=STATUS_READ),
        _book("e", "E", "Now Listening", status=STATUS_LISTENING),
    ]
    progress = {
        "c": {"isFinished": True, "finishedAt": 500, "lastUpdate": 500},
        "d": {"isFinished": True, "finishedAt": 100, "lastUpdate": 900},
        "e": {"isFinished": False, "progress": 0.4, "lastUpdate": 50},
    }
    runs = _runs_of(books, progress)
    assert [r.books[0].item_id for r in runs] == ["e", "c", "d", "b", "a"]


def test_surname():
    assert surname("Robert Jordan") == "Jordan"
    assert surname("Amir Levine, Md, Rachel Heller") == "Levine"
    assert surname("") == ""


# pieces and packing


def test_pieces_spines_for_unread_faces_for_read_and_listening():
    hours = {"u": 10.0, "r": 10.0, "l": 10.0, "long": 100.0, "short": 0.1}
    assert piece_for(_book("u"), hours).kind == "spine"
    assert piece_for(_book("r", status=STATUS_READ), hours).kind == "face"
    face = piece_for(_book("l", status=STATUS_LISTENING), hours)
    assert (face.kind, face.width, face.height) == ("face", 200, USABLE_H)
    # 28 + hours * 1.2, clamped to 34..70 (a one-word title never widens its spine)
    assert piece_for(_book("u"), hours).width == 40
    assert piece_for(_book("long"), hours).width == 70
    assert piece_for(_book("short"), hours).width == 34
    assert piece_for(_book("missing"), {}).width == 34


def test_three_boards_end_where_the_four_did():
    assert BOARD_Y == (110, 420, 730) and render_spines.BOARDS == 3 and USABLE_H == 286
    assert BOARD_Y[-1] + USABLE_H + BOARD_H == 1030
    # the tallest spine and a face-out stand under the next board
    for top, nxt in zip(BOARD_Y, BOARD_Y[1:]):
        assert top + USABLE_H + BOARD_H <= nxt


def test_plan_sections_reads_the_board_count_when_called(library, monkeypatch):
    _, runs, hours, _ = library
    monkeypatch.setattr(render_spines, "BOARDS", 2)
    sections = plan_sections(runs, hours)
    assert all(len(section) <= 2 for section in sections)
    assert any(len(section) == 2 for section in sections)


def test_spine_heights_are_deterministic_and_vary():
    heights = [piece_for(_book(f"id-{i}"), {}).height for i in range(200)]
    assert all(248 <= h <= 284 for h in heights)
    assert len(set(heights)) > 15
    again = [piece_for(_book(f"id-{i}"), {}).height for i in range(200)]
    assert heights == again


def _placements(sections):
    for s, section in enumerate(sections):
        for b, board in enumerate(section):
            for run, pieces in board:
                yield s, b, run, pieces


def _board_width(board, gap=2, run_gap=14):
    x = 0
    for i, (_, pieces) in enumerate(board):
        if i:
            x += run_gap
        x += max(40, sum(p.width for p in pieces) + gap * (len(pieces) - 1))
    return x


def test_fixture_packs_into_seven_to_twelve_sections_without_overflow(library):
    books, runs, hours, _ = library
    sections = plan_sections(runs, hours)
    assert 7 <= len(sections) <= 12
    assert all(1 <= len(section) <= 3 for section in sections)
    for section in sections:
        for board in section:
            assert board and _board_width(board) <= W
    # every book exactly once; a run moves up at most LOOKAHEAD places to fill a board's hole
    order = [p.book.item_id for _, _, _, pieces in _placements(sections) for p in pieces]
    assert sorted(order) == sorted(b.item_id for b in books)
    run_order = list(dict.fromkeys(run.key for _, _, run, _ in _placements(sections)))
    for placed_at, key in enumerate(run_order):
        planned_at = [r.key for r in runs].index(key)
        assert planned_at - placed_at <= render_spines.LOOKAHEAD
    # with three boards of 200 px face-outs the first runs fit in their planned order
    assert run_order[:6] == [r.key for r in runs[:6]]
    assert plan_sections(runs, hours) == sections


def test_a_run_that_moves_on_leaves_its_hole_to_a_later_run():
    faces = [_book(f"a{i}", status=STATUS_READ) for i in range(5)]
    first = Run("author:a", "A", "author", faces)  # 5 * 202 - 2 = 1008 px
    pair = Run("author:b", "B", "author", [_book(f"b{i}", status=STATUS_READ) for i in range(2)])
    small = Run("author:c", "C", "author", [_book("c1")])  # one 34 px spine, a 40 px slot
    boards = plan_sections([first, pair, small], {})[0]
    assert [[run.key for run, _ in board] for board in boards] == [["author:a", "author:c"], ["author:b"]]


def test_runs_that_fit_a_board_are_never_split(library):
    _, runs, hours, _ = library
    sections = plan_sections(runs, hours)
    seen = {}
    for s, b, run, pieces in _placements(sections):
        seen.setdefault(run.key, []).append((s, b))
    for run in runs:
        width = sum(piece_for(bk, hours).width for bk in run.books) + 2 * (len(run.books) - 1)
        if width <= W:
            assert len(seen[run.key]) == 1, run.key


def test_a_run_wider_than_a_board_wraps_onto_the_next():
    wide = Run("series:wide", "WIDE · 30 of 30", "series", [_book(f"w{i}", status=STATUS_READ) for i in range(30)])
    small = Run("author:s", "S", "author", [_book("s1")])
    sections = plan_sections([small, wide], {})
    boards = [board for section in sections for board in section]
    # 30 face-outs at 200 px with 2 px gaps: 5 beside the small run (54 + 5 * 202 - 2 = 1062),
    # then 6 per board (6 * 202 - 2 = 1210)
    frags = [len(pieces) for board in boards for run, pieces in board if run.key == "series:wide"]
    assert frags == [5, 6, 6, 6, 6, 1]
    assert all(_board_width(board) <= W for board in boards)


def test_empty_library_is_one_empty_section():
    assert plan_sections([], {}) == [[]]
    assert section_of([], "x", {}) is None


def test_section_of(library):
    _, runs, hours, _ = library
    sections = plan_sections(runs, hours)
    assert section_of(runs, OVERGEARED, hours) == 0
    last = sections[-1][-1][-1][1][-1].book.item_id
    assert section_of(runs, last, hours) == len(sections) - 1
    assert section_of(runs, "nope", hours) is None


# colours


def test_spine_colour_prefers_saturated_and_skips_white():
    img = Image.new("RGB", (120, 120), (250, 250, 250))
    ImageDraw.Draw(img).rectangle([0, 0, 59, 119], fill=(30, 60, 170))
    r, g, b = spine_colour(img, "x")
    assert b > 140 and r < 70
    red = spine_colour(Image.new("RGB", (60, 60), (190, 30, 30)), "x")
    assert red[0] > 150 and red[1] < 60
    a, again, other = spine_colour(None, "Dune"), spine_colour(None, "Dune"), spine_colour(None, "Emma")
    assert a == again and a != other
    assert max(a) < 200 and max(a) - min(a) < 120


def test_colour_cache_persists_and_follows_updated_at(tmp_path):
    cover = tmp_path / "c.jpg"
    Image.new("RGB", (60, 60), (190, 30, 30)).save(cover)
    path = tmp_path / "spines.json"
    cache = ColourCache(str(path))
    rgb = cache.get("i1", 5, str(cover))
    assert rgb[0] > 150
    assert cache.get("i2", 5, None) is None
    cache.save()
    stored = json.loads(path.read_text())
    assert stored["i1"]["updated_at"] == 5 and tuple(stored["i1"]["rgb"]) == rgb
    cover.unlink()
    again = ColourCache(str(path))
    assert again.get("i1", 5, str(cover)) == rgb  # from the file, the cover is not read again
    Image.new("RGB", (60, 60), (30, 30, 190)).save(cover)
    assert again.get("i1", 6, str(cover))[2] > 150
    junk = tmp_path / "junk.jpg"
    junk.write_bytes(b"no")
    assert again.get("i3", 1, str(junk)) is None
    (tmp_path / "bad.json").write_text("[1")
    assert ColourCache(str(tmp_path / "bad.json")).get("i1", 5, None) is None


# drawing


def _count(img, colour, tol=10):
    return sum(n for n, c in img.getcolors(1 << 20) if all(abs(a - b) <= tol for a, b in zip(c, colour)))


def test_spine_carries_its_title_and_author():
    # Garamond 30 "Dune Messiah" is about 169 px, inside the 270 - 70 px the title may take
    piece = Piece(_book("s", "Dune Messiah", "Frank Herbert"), "spine", 50, 270)
    img = draw_spine(piece, (40, 60, 120), DARK)
    assert img.size == (50, 270)
    assert _count(img, (245, 242, 232), tol=40) > 80  # off-white type on a dark spine
    assert spine_title(piece) == "Dune Messiah"
    light = draw_spine(piece, (230, 210, 160), DARK)
    assert _count(light, (28, 26, 24), tol=40) > 80


def _ink_box(img):
    return img.convert("L").point(lambda v: 255 if v > 180 else 0).getbbox()


def _two_line_width():
    return 2 * LINE_W + 2 * TITLE_PAD


def test_line_pitch_comes_from_the_font():
    top, bottom = font(SERIF, 26, 500).getbbox("Hgy", anchor="ls")[1::2]
    assert bottom - top < LINE_W <= bottom - top + 8
    assert 28 <= LINE_W <= 32
    assert 64 <= _two_line_width() <= 80


def test_a_short_title_is_one_line_at_30():
    layout = spine_layout("Dune Messiah", 50, 270)
    assert (layout.size, layout.lines, layout.width) == (30, ("Dune Messiah",), 50)


def test_a_medium_title_drops_to_26():
    # Garamond 30 "Children of Time" is about 209 px, over the 200 px room; at 26 about 181
    layout = spine_layout("Children of Time", 50, 270)
    assert (layout.size, layout.lines, layout.width) == (26, ("Children of Time",), 50)


def test_a_narrow_spine_starts_at_26():
    layout = spine_layout("Dune", 40, 270)
    assert (layout.size, layout.lines, layout.width) == (26, ("Dune",), 40)


def test_a_long_title_wraps_to_two_lines_and_widens_the_spine():
    # about 269 px at 26, over the whole 254 px spine: two lines, cut between words
    layout = spine_layout("The Lies of Locke Lamora", 40, 270)
    assert layout.size == 26 and len(layout.lines) == 2
    assert " ".join(layout.lines) == "The Lies of Locke Lamora"
    assert all(text_width(font(SERIF, 26, 500), line) <= 270 - 2 * TITLE_PAD for line in layout.lines)
    assert layout.width == _two_line_width()
    assert (layout.author, layout.room) == (False, 270 - 2 * TITLE_PAD)
    # a long book already wider than two lines keeps its width
    assert spine_layout("The Lies of Locke Lamora", 90, 270).width == 90


def test_a_very_long_title_ends_with_the_ellipsis_on_line_two():
    title = "The Hitchhiker's Guide to the Galaxy and Everything After It, the Complete Edition"
    layout = spine_layout(title, 40, 270)
    # no separator to drop a subtitle at: two lines at 22, the last one cut
    assert layout.size == 22 and len(layout.lines) == 2
    first, second = layout.lines
    assert not first.endswith(ELLIPSIS) and title.startswith(first + " ")
    assert second.endswith(ELLIPSIS)
    assert all(text_width(font(SERIF, 22, 500), line) <= 254 for line in layout.lines)
    assert layout.width == _two_line_width() and not layout.author


def test_a_word_longer_than_the_room_is_cut():
    layout = spine_layout("Supercalifragilisticexpialidocious Antidisestablishmentarianism", 40, 270)
    assert len(layout.lines) == 2 and layout.lines[0].endswith(ELLIPSIS)
    assert layout.lines[1].endswith(ELLIPSIS)
    alone = spine_layout("Supercalifragilisticexpialidocious", 40, 270)
    # one word is one line, cut: there is nothing to put on a second line
    assert len(alone.lines) == 1 and alone.lines[0].endswith(ELLIPSIS) and alone.width == 40


def test_piece_for_widens_a_long_titled_short_book():
    book = _book("x", "The Hitchhiker's Guide to the Galaxy", "Douglas Adams")
    piece = piece_for(book, {"x": 2.0})
    assert piece.width == _two_line_width()
    assert spine_layout(book.title, piece.width, piece.height).width == piece.width
    assert piece_for(_book("y", "Dune", "Frank Herbert"), {"y": 2.0}).width == 34


def test_two_line_spine_draws_both_lines_inside_and_no_author():
    book = _book("s", "The Lies of Locke Lamora", "Scott Lynch")
    w = _two_line_width()
    piece = Piece(book, "spine", w, 270)
    img = draw_spine(piece, (40, 60, 120), DARK)
    assert img.size == (w, 270)
    x0, y0, x1, y1 = _ink_box(img)
    # nothing touches the long edges or the spine's ends
    assert x0 >= 2 and x1 <= w - 4
    assert y0 >= 4 and y1 <= 270 - 4
    # two lines: ink in both halves of the width
    left = _ink_box(img.crop((0, 0, w // 2, 270)))
    right = _ink_box(img.crop((w // 2, 0, w, 270)))
    assert left is not None and right is not None
    # no author at the top: the top 50 px carry no type
    assert _ink_box(img.crop((0, 5, w, 50))) is None


def test_long_title_is_wrapped_and_ellipsized_inside_the_spine():
    title = ("A very long title that keeps going " * 4)[:120]
    piece = piece_for(_book("s", title, ""), {})
    assert spine_title(piece).endswith(ELLIPSIS)
    img = draw_spine(piece, (40, 60, 120), DARK)
    x0, y0, x1, y1 = _ink_box(img)
    w, h = piece.width, piece.height
    assert x0 >= 2 and x1 <= w - 4
    assert y0 >= 4 and y1 <= h - 4


@pytest.mark.parametrize(
    "title",
    [
        "Dune",
        "Children of Time",
        "The Lies of Locke Lamora",
        "Wwwwwwwwwwwwwwwwwwwww",
        "Lord of the Mysteries (Web Novel) Novel Chapters | Light Novel Pub - Part 2",
    ],
)
@pytest.mark.parametrize("hours", [0, 8, 40])
def test_type_never_leaves_the_spine(title, hours):
    piece = piece_for(_book(f"{title}-{hours}", title, "Ursula K. Le Guin"), {f"{title}-{hours}": hours})
    img = draw_spine(piece, (40, 60, 120), DARK)
    x0, y0, x1, y1 = _ink_box(img)
    assert x0 >= 1 and x1 <= piece.width - 3
    assert y0 >= 4 and y1 <= piece.height - 4


def test_face_out_listening_has_ribbon_and_progress_read_has_neither():
    cover = Image.new("RGB", (120, 120), (90, 140, 90))
    listening = Piece(_book("l", status=STATUS_LISTENING), "face", 200, USABLE_H)
    img = draw_face(listening, cover, 0.5, DARK)
    assert img.size == (200, USABLE_H)
    # the 14 x 72 ribbon, less its 9 px notch
    assert 14 * 60 < _count(img, DARK.accent, tol=4) < 14 * 72 + 186 * 4
    # the progress line runs under the 186 px cover: half of it at 0.5
    base = img.crop((0, USABLE_H - 6, 200, USABLE_H - 2))
    assert _count(base, DARK.accent, tol=4) == 93 * 4
    read = draw_face(Piece(_book("r", status=STATUS_READ), "face", 200, USABLE_H), cover, None, DARK)
    assert _count(read, DARK.accent, tol=4) == 0
    assert _count(read, PLINTH, tol=2) > 0
    assert _count(read, (90, 140, 90), tol=8) == 186 * 186
    missing = draw_face(Piece(_book("m", "Dune", status=STATUS_READ), "face", 200, USABLE_H), None, None, PAPER)
    assert _count(missing, spine_colour(None, "Dune"), tol=2) > 180 * 180


def _section_view(library, section=0, books=None, runs=None, colours=None, covers=None):
    all_books, all_runs, hours, progress = library
    books = all_books if books is None else books
    runs = all_runs if runs is None else runs
    pct = {b.item_id: (progress.get(b.item_id) or {}).get("progress") or 0.0 for b in books}
    if colours is None:
        colours = {b.item_id: spine_colour(None, b.title) for b in books}
    return ShelfSectionView(
        "The shelf", "Audiobooks, by series and author", books, runs, hours, pct, covers or {}, colours,
        shelf_counts(books), section, 2, {},
    )


def test_render_section_size_header_and_footer(library):
    view = _section_view(library)
    for theme in (DARK, PAPER):
        img, info = render_section(view, theme)
        assert img.size == (W, 1080) and img.mode == "RGB"
        assert img.getpixel((W - 1, 1075)) == theme.bg
    n = info["sections"]
    assert info["section"] == 0 and 7 <= n <= 12
    assert info["subtitle"] == f"Audiobooks, by series and author  ·  shelf 1 of {n}"
    assert info["first_author"] == "Saenal"
    assert info["footer"] == (
        f"{info['count']} of 311 on this shelf  ·  Saenal to {info['last_author']}  ·  next shelf in 2 min"
    )
    assert 10 <= info["count"] <= 150
    # section numbers wrap
    _, info = render_section(_section_view(library, section=n + 1), DARK)
    assert info["section"] == 1 and info["subtitle"].endswith(f"shelf 2 of {n}")


def test_boards_and_labels_under_runs(library):
    view = _section_view(library)
    img, _ = render_section(view, DARK)
    for y in BOARD_Y:
        top = y + USABLE_H
        assert _count(img.crop((0, top + 3, W, top + 14)), BOARD_COLOUR, tol=0) > W * 4
    # every run placed on the first board has label ink on the board under its own slot
    _, runs, hours, _ = library
    board = plan_sections(runs, hours)[0][0]
    x = 0
    for i, (run, pieces) in enumerate(board):
        if i:
            x += 14
        slot = max(40, sum(p.width for p in pieces) + 2 * (len(pieces) - 1))
        band = img.crop((x, BOARD_Y[0] + USABLE_H + 2, x + slot, BOARD_Y[0] + USABLE_H + 14))
        assert _count(band, LABEL_COLOUR, tol=40) > 3, run.key
        x += slot
    # nothing of a label spills into the 14 px gap before the next run
    gap = img.crop((x + 2, BOARD_Y[0] + USABLE_H + 2, min(W, x + 12), BOARD_Y[0] + USABLE_H + 14))
    assert _count(gap, LABEL_COLOUR, tol=40) == 0


def test_face_outs_on_the_first_board_show_covers(library):
    books, runs, hours, progress = library
    covers = {b.item_id: Image.new("RGB", (120, 120), (20, 200, 220)) for b in books if b.status != STATUS_UNREAD}
    img, _ = render_section(_section_view(library, covers=covers), DARK)
    first = img.crop((0, BOARD_Y[0], W, BOARD_Y[0] + USABLE_H))
    assert _count(first, (20, 200, 220), tol=6) > 186 * 186 * 3
    assert _count(first, DARK.accent, tol=4) > 14 * 60  # Overgeared's ribbon


def test_empty_library_renders_shelf_1_of_1():
    library = ([], [], {}, {})
    img, info = render_section(_section_view(library), DARK)
    assert img.size == (W, 1080)
    assert (info["sections"], info["section"], info["count"]) == (1, 0, 0)
    assert info["subtitle"].endswith("shelf 1 of 1")
    assert info["footer"] == "0 of 0 on this shelf"


def test_single_face_out_book_is_one_section():
    book = _book("only", "Only", "Solo Writer", status=STATUS_READ)
    runs = _runs_of([book], {"only": {"isFinished": True, "finishedAt": 1}})
    library = ([book], runs, {"only": 5.0}, {})
    img, info = render_section(_section_view(library, section=3), PAPER)
    assert (info["sections"], info["section"], info["count"]) == (1, 0, 1)
    # one section: no "next shelf" promise
    assert info["footer"] == "1 of 1 on this shelf  ·  Writer to Writer"


def test_other_size_scales(library):
    img, _ = render_section(_section_view(library), DARK, size=(820, 720), scale=2 / 3)
    assert img.size == (820, 720)


def test_heaviest_section_is_the_one_with_most_spines(library):
    _, runs, hours, _ = library
    plan = plan_sections(runs, hours)
    spines = [
        sum(1 for board in section for _, pieces in board for p in pieces if p.kind == "spine")
        for section in plan
    ]
    index, count = render_spines.heaviest_section(runs, hours)
    assert count == max(spines) and spines[index] == count
    # a section of the unread tail, all spines (wrapped titles widen some, so not always the last)
    assert all(p.kind == "spine" for board in plan[index] for _, pieces in board for p in pieces)
    assert render_spines.heaviest_section([], {}) == (0, 0)


def test_the_heaviest_section_renders_fast_with_cached_colours(library):
    _, runs, hours, _ = library
    index, count = render_spines.heaviest_section(runs, hours)
    assert count >= 40
    view = _section_view(library, section=index)
    render_spines.clear_caches()
    start = time.perf_counter()
    render_section(view, DARK)
    # the real budget is the Pi self-test's 1.5 s; here only a regression of several times fails
    assert time.perf_counter() - start < 0.6


def test_a_metadata_edit_replans():
    a = _book("1", "One", "Ann Author")
    runs = _runs_of([a])
    first = render_spines.planned(runs, {})
    renamed = [Run(runs[0].key, runs[0].label, runs[0].kind, [_book("1", "One", "Ann Other")])]
    assert render_spines.planned(renamed, {}) is not first
    relabelled = [Run(runs[0].key, "SOMETHING ELSE", runs[0].kind, runs[0].books)]
    assert render_spines.planned(relabelled, {}) is not first
    assert render_spines.planned(runs, {}) is first


def _ink_rows(img):
    ink = img.convert("L").point(lambda v: 255 if v > 180 else 0)
    return ink.getbbox()


def test_author_surname_is_whole_or_absent():
    assert AUTHOR_MIN_W == 44
    short = Piece(_book("s", "Dune", "Frank Herbert"), "spine", 50, 270)
    top = draw_spine(short, (40, 60, 120), DARK).crop((0, 5, 50, 70))
    assert _ink_rows(top) is not None  # "Herbert" at the top
    # one line at 26 that leaves no room for the whole surname: no clipped "Herb..."
    crowded = Piece(_book("c", "Children of Time", "Adrian Tchaikovsky"), "spine", 50, 270)
    assert spine_title(crowded) == "Children of Time"
    top = draw_spine(crowded, (40, 60, 120), DARK).crop((0, 5, 50, 52))
    assert _ink_rows(top) is None
    narrow = Piece(_book("n", "Dune", "Frank Herbert"), "spine", 43, 270)
    assert _ink_rows(draw_spine(narrow, (40, 60, 120), DARK).crop((0, 5, 43, 70))) is None


def test_fast_ellipsize_matches_ellipsize_on_the_fixture_titles(library):
    from estante_lib.render_spines import _fast_ellipsize
    from estante_lib.text import ellipsize

    books, _, _, _ = library
    fnt = font(SERIF, 26, 500)
    for book in books:
        title = " ".join(book.title.split())
        for room in (60, 178, 214):
            assert _fast_ellipsize(title, fnt, room) == ellipsize(title, fnt, room), (title, room)


@pytest.mark.parametrize("room", [230, 245])
def test_fast_ellipsize_matches_ellipsize_where_kerning_fools_the_estimate(room):
    # the advances add up to about 271 px; kerned, the real width is about 241
    from estante_lib.render_spines import _fast_ellipsize
    from estante_lib.text import ellipsize

    fnt = font(SERIF, 26, 500)
    title = "AVAWAYATo Wa Ta Yo"
    assert _fast_ellipsize(title, fnt, room) == ellipsize(title, fnt, room)
    if room == 245:
        assert _fast_ellipsize(title, fnt, room) == title


def _fixture_layouts(books, hours):
    for book in books:
        if book.status != STATUS_UNREAD:
            continue
        piece = piece_for(book, hours)
        title = shelf_title(book.title, book.author, book.series)
        yield book, piece, spine_layout(title, piece.width, piece.height)


def test_every_fixture_spine_line_fits_its_room(library):
    books, _, hours, _ = library
    fnts = {size: font(SERIF, size, 500) for size in (22, 26, 30)}
    for book, piece, layout in _fixture_layouts(books, hours):
        assert layout.width == piece.width
        assert layout.room in (piece.height - TITLE_ROOM, piece.height - 2 * TITLE_PAD)
        for line in layout.lines:
            assert text_width(fnts[layout.size], line) <= layout.room, (book.title, line)


def test_at_most_six_fixture_spines_end_with_the_ellipsis(library):
    books, _, hours, _ = library
    cut = [book.title for book, _, layout in _fixture_layouts(books, hours) if layout.lines[-1].endswith(ELLIPSIS)]
    # 57 did before titles were cleaned and two-line titles had the whole spine
    assert len(cut) <= 6, cut


@pytest.mark.parametrize(
    "title, author, series, cleaned",
    [
        ("The Burning God (The Poppy War, Book 3)", "R. F. Kuang", "The Poppy War #3", "The Burning God"),
        (
            "Dom Casmurro by Joaquim Maria Machado de Assis (1839 - 1908)",
            "Joaquim Maria Machado de Assis",
            "",
            "Dom Casmurro",
        ),
        ("Alienista, O", "Machado de Assis", "", "O Alienista"),
        ("Count of Monte Cristo, The", "Alexandre Dumas", "", "The Count of Monte Cristo"),
        ("1491 (Unabridged)", "Charles C. Mann", "", "1491"),
        ("Blood over Bright Haven [Unabridged]", "M. L. Wang", "", "Blood over Bright Haven"),
        ("Red Rising (Full-Cast Edition)", "Pierce Brown", "", "Red Rising"),
        ("Golden Son (Part 1 of 2) (Dramatized Adaptation)", "Pierce Brown", "Red Rising #2", "Golden Son (Part 1 of 2)"),
        ("Dune (Abridged)  ", "Frank Herbert", "", "Dune"),
        ("Warbreaker (Vol. 2)", "Brandon Sanderson", "", "Warbreaker"),
        ("Oathbringer (Stormlight Archive Volume 3)", "Brandon Sanderson", "", "Oathbringer"),
        ("Skyward (Skyward Saga)", "Brandon Sanderson", "Skyward #1", "Skyward"),
        ("NEKOMONOGATARI: Cat Tale (White)", "NISIOISIN", "Monogatari #6", "NEKOMONOGATARI: Cat Tale (White)"),
        ("Le Morte d'Arthur (Selected Tales)", "Thomas Malory", "", "Le Morte d'Arthur (Selected Tales)"),
        ("Walden by Henry David Thoreau", "Someone Else", "", "Walden by Henry David Thoreau"),
        ("  Team   of Rivals ", "Doris Kearns Goodwin", "", "Team of Rivals"),
    ],
)
def test_shelf_title_cleans_scraped_titles(title, author, series, cleaned):
    assert shelf_title(title, author, series) == cleaned


def test_a_subtitle_is_dropped_before_anything_is_cut():
    layout = spine_layout("The Shock Doctrine: The Rise of Disaster Capitalism", 40, 248)
    assert not any(line.endswith(ELLIPSIS) for line in layout.lines)
    assert " ".join(layout.lines) == "The Shock Doctrine"
    dashed = spine_layout("Scattered Minds - The Origins and Healing of Attention Deficit Disorder", 40, 248)
    assert " ".join(dashed.lines) == "Scattered Minds"


def test_a_title_without_a_separator_still_ends_with_the_ellipsis():
    title = "A very long title that keeps going and going with no place at all to stop it early on"
    layout = spine_layout(title, 40, 248)
    assert layout.lines[-1].endswith(ELLIPSIS) and len(layout.lines) == 2


def test_a_hyphenated_word_fits_one_line_on_the_whole_spine():
    layout = spine_layout("Slaughterhouse-Five", 40, 258)
    assert layout.lines == ("Slaughterhouse-Five",) and layout.width == 40
    assert layout.author is False and layout.room == 258 - 2 * TITLE_PAD


def test_a_word_longer_than_the_spine_breaks_at_its_hyphen():
    word = "Supercalifragilistic-Expialidociousness"
    assert text_width(font(SERIF, 26, 500), word) > 248 - 2 * TITLE_PAD
    layout = spine_layout(word, 40, 248)
    assert layout.lines == ("Supercalifragilistic-", "Expialidociousness")
    assert layout.width == _two_line_width() and layout.size == 26


@pytest.mark.parametrize("sep", [" -", ":", ",", ";", " |"])
def test_line_one_never_ends_with_a_separator(sep):
    from estante_lib.spine_text import _two_lines

    fnt = font(SERIF, 26, 500)
    # line 1 has room for the separator; the greedy fill would leave it dangling there
    room = max(text_width(fnt, f"Alpha Beta{sep}"), text_width(fnt, "Gamma Delta")) + 2
    assert text_width(fnt, f"Alpha Beta{sep} Gamma") > room
    lines, cut = _two_lines(f"Alpha Beta{sep} Gamma Delta", fnt, room)
    assert lines == ("Alpha Beta", "Gamma Delta") and not cut


def test_one_line_spines_keep_the_surname_room_and_two_line_spines_do_not():
    one = spine_layout("Dune", 50, 270)
    assert (one.author, one.room) == (True, 270 - TITLE_ROOM)
    whole = spine_layout("The Name of the Wind", 50, 270)  # 240 px at 26: over 200, under 254
    assert (whole.size, whole.lines, whole.author, whole.room) == (26, ("The Name of the Wind",), False, 254)
    top = draw_spine(Piece(_book("w", "The Name of the Wind", "Patrick Rothfuss"), "spine", 50, 270), (40, 60, 120), DARK)
    assert _ink_rows(top.crop((0, 4, 50, 20))) is None  # no surname above the title
