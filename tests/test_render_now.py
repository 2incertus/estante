import dataclasses
import math

from estante_lib.palette import BLACK, BLUE, GREEN, RED, WHITE, driver_palette, inky_pass, to_rgb
from estante_lib.render_now import NowListeningView, render_now_listening
from estante_lib.state import DEFAULT_BADGE

BAR_X0, BAR_X1, BAR_Y = 470, 760, 300


def _count(img, box, index):
    x0, y0, x1, y1 = box
    return sum(1 for x in range(x0, x1) for y in range(y0, y1) if img.getpixel((x, y)) == index)


def test_render_size_mode_and_palette_only(now_view):
    img = render_now_listening(now_view)
    assert img.size == (800, 480) and img.mode == "P"
    assert max(img.tobytes()) <= 5
    assert img.getpalette()[:18] == [v for c in driver_palette(0.5) for v in c]


def test_inky_pass_noop_on_now_listening(now_view):
    img = render_now_listening(now_view)
    assert inky_pass(to_rgb(img, driver_palette(0.5))).tobytes() == img.tobytes()


def test_progress_bar_filled_to_pct(now_view):
    # Overgeared progress: currentTime 28095.155 of duration 583287.581035 (fixtures).
    assert abs(now_view.nl.pct - 28095.155 / 583287.581035) < 1e-9
    img = render_now_listening(now_view)
    y = BAR_Y + 6
    fx = BAR_X0 + max(4, int((BAR_X1 - BAR_X0) * now_view.nl.pct))
    assert img.getpixel((BAR_X0 + 3, y)) == RED
    assert img.getpixel((fx - 1, y)) == RED
    assert img.getpixel((min(fx + 6, BAR_X1 - 3), y)) == WHITE
    assert img.getpixel((BAR_X0 + 100, BAR_Y)) == BLACK  # outline top
    assert img.getpixel((BAR_X1 - 1, y)) == BLACK  # outline right


def test_finished_bar_is_full(now_view):
    nl = dataclasses.replace(now_view.nl, finished=True, finished_at=1790000000000)
    img = render_now_listening(dataclasses.replace(now_view, nl=nl))
    row = [img.getpixel((x, BAR_Y + 6)) for x in range(BAR_X0 + 3, BAR_X1 - 3)]
    assert row.count(RED) == len(row)


def test_tick_thinning_for_820_chapters(overgeared_view):
    assert overgeared_view.nl.chapter_count == 820
    img = render_now_listening(overgeared_view)
    ticks = sum(1 for x in range(470, 761) if img.getpixel((x, 318)) == BLACK)
    assert 40 <= ticks <= 96
    # every ceil(820 / 96) = 9th chapter: at most 91 ticks, none for chapter 0
    assert math.ceil(820 / 96) == 9
    assert img.getpixel((470, 318)) == WHITE


def test_render_without_narrator_or_series(now_view):
    nl = dataclasses.replace(now_view.nl, narrator="", series="")
    img = render_now_listening(dataclasses.replace(now_view, nl=nl))
    assert img.size == (800, 480) and img.mode == "P"
    # nothing BLUE in the text column when there is no series line, and no GREEN
    # specks: antialiased glyph edges would snap to both in finish()
    assert _count(img, (470, 100, 761, 290), BLUE) == 0
    assert _count(img, (470, 100, 761, 290), GREEN) == 0


def test_series_line_is_blue(now_view):
    nl = dataclasses.replace(now_view.nl, series="Overgeared #1")
    img = render_now_listening(dataclasses.replace(now_view, nl=nl))
    assert _count(img, (470, 100, 761, 290), BLUE) > 30


def test_cover_fills_the_left_box(now_view):
    img = render_now_listening(now_view)
    # the square w400 cover covers the 400x400 box: almost no paper inside it
    assert _count(img, (40, 40, 440, 440), WHITE) < 0.5 * 400 * 400
    assert img.getpixel((20, 240)) == WHITE and img.getpixel((450, 240)) == WHITE


def test_render_nothing_playing():
    img = render_now_listening(NowListeningView(None, None, 0, 2026, dict(DEFAULT_BADGE)))
    assert img.size == (800, 480) and img.mode == "P"
    assert _count(img, (40, 40, 440, 440), WHITE) == 400 * 400  # no cover
    assert _count(img, (470, 50, 761, 75), RED) > 0  # NOTHING PLAYING label
    assert _count(img, (470, 90, 761, 130), BLACK) > 0  # "Nothing played yet"
    assert _count(img, (470, 290, 761, 325), BLACK) == 0  # no bar, no ticks


def test_footer_default_and_brand(now_view):
    img = render_now_listening(now_view)
    assert all(img.getpixel((x, 410)) == BLACK for x in range(470, 761))  # rule
    assert _count(img, (470, 424, 640, 441), BLACK) > 0  # "7 finished in 2026"
    assert _count(img, (470, 424, 640, 441), RED) == 0
    assert _count(img, (680, 424, 761, 441), GREEN) > 0  # ESTANTE in GREEN


def test_badge_draws_red_message_in_footer(now_view):
    badge = {"message_waiting": True, "dnd": True, "count": 1, "from": "Jasmine"}
    img = render_now_listening(dataclasses.replace(now_view, badge=badge))
    row = [img.getpixel((x, 432)) for x in range(470, 760)]
    assert RED in row and BLACK in row


def test_waiting_message_takes_the_marks_room(now_view):
    # "Message from Jasmine waiting" after the QUIET tag does not fit beside ESTANTE
    # in a 290 px column: the message keeps every word and the mark gives way.
    badge = {"message_waiting": True, "dnd": True, "count": 1, "from": "Jasmine"}
    img = render_now_listening(dataclasses.replace(now_view, badge=badge))
    assert _count(img, (470, 420, 761, 445), GREEN) == 0
    assert _count(img, (700, 424, 761, 441), RED) > 0
