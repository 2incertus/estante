import dataclasses
import math

from estante_lib.render_now import NowListeningView, render_now_column
from estante_lib.state import DEFAULT_BADGE
from estante_lib.theme import DARK, PAPER

W, H = 620, 1080
BAR_X0, BAR_X1, BAR_Y, BAR_H = 50, 570, 900, 14
TEXT_BOX = (50, 615, 571, 890)


def _near(px, colour, tol=24):
    return all(abs(a - b) <= tol for a, b in zip(px, colour))


def _count_near(img, box, colour, tol=24):
    x0, y0, x1, y1 = box
    return sum(1 for x in range(x0, x1) for y in range(y0, y1) if _near(img.getpixel((x, y)), colour, tol))


def _count_not(img, box, colour):
    x0, y0, x1, y1 = box
    return sum(1 for x in range(x0, x1) for y in range(y0, y1) if img.getpixel((x, y)) != colour)


def test_column_size_mode_and_background(now_view):
    for theme in (DARK, PAPER):
        img = render_now_column(now_view, theme)
        assert img.size == (W, H) and img.mode == "RGB"
        assert img.getpixel((5, 5)) == theme.bg and img.getpixel((W - 5, H - 5)) == theme.bg


def test_label_in_accent(now_view):
    img = render_now_column(now_view, DARK)
    assert _count_near(img, (50, 46, 300, 66), DARK.accent) > 30  # NOW LISTENING


def test_progress_bar_filled_to_pct(now_view):
    # Overgeared progress: currentTime 28095.155 of duration 583287.581035 (fixtures).
    assert abs(now_view.nl.pct - 28095.155 / 583287.581035) < 1e-9
    img = render_now_column(now_view, DARK)
    y = BAR_Y + BAR_H // 2
    fx = BAR_X0 + max(4, int((BAR_X1 - BAR_X0) * now_view.nl.pct))
    assert img.getpixel((BAR_X0 + 3, y)) == DARK.accent
    assert img.getpixel((fx - 1, y)) == DARK.accent
    assert img.getpixel((min(fx + 6, BAR_X1 - 4), y)) == DARK.bg
    assert img.getpixel((BAR_X0 + 100, BAR_Y)) == DARK.ink  # outline top
    assert img.getpixel((BAR_X1 - 1, y)) == DARK.ink  # outline right


def test_finished_bar_is_full(now_view):
    nl = dataclasses.replace(now_view.nl, finished=True, finished_at=1790000000000)
    img = render_now_column(dataclasses.replace(now_view, nl=nl), DARK)
    row = [img.getpixel((x, BAR_Y + 7)) for x in range(BAR_X0 + 3, BAR_X1 - 3)]
    assert row.count(DARK.accent) == len(row)
    assert _count_near(img, (50, 46, 300, 66), DARK.accent) > 30  # LAST LISTENED


def test_tick_thinning_for_820_chapters(overgeared_view):
    assert overgeared_view.nl.chapter_count == 820
    img = render_now_column(overgeared_view, DARK)
    tick_y = BAR_Y + BAR_H + 4
    ticks = sum(1 for x in range(BAR_X0, BAR_X1 + 1) if img.getpixel((x, tick_y)) != DARK.bg)
    assert 40 <= ticks <= 96
    # every ceil(820 / 96) = 9th chapter: at most 91 ticks, none for chapter 0
    assert math.ceil(820 / 96) == 9
    assert img.getpixel((BAR_X0, tick_y)) == DARK.bg


def test_cover_contained_in_box(now_view):
    img = render_now_column(now_view, DARK)
    # the square w400 cover contained in the 520 box at (50, 90): almost no background inside
    assert _count_not(img, (50, 90, 570, 610), DARK.bg) > 0.9 * 520 * 520
    assert img.getpixel((25, 350)) == DARK.bg and img.getpixel((595, 350)) == DARK.bg
    assert img.getpixel((300, 80)) == DARK.bg


def test_title_and_rows_below_cover(now_view):
    img = render_now_column(now_view, DARK)
    assert _count_near(img, (50, 633, 571, 678), DARK.ink) > 100  # title, first baseline 668
    assert _count_not(img, (50, 611, 571, 628), DARK.bg) == 0  # clear air between cover and title
    assert _count_not(img, (50, 860, 571, 898), DARK.bg) == 0  # nothing touches the bar


def test_series_line_in_series_colour(now_view):
    with_series = dataclasses.replace(now_view.nl, series="Overgeared #1")
    without = dataclasses.replace(now_view.nl, series="")
    a = render_now_column(dataclasses.replace(now_view, nl=with_series), DARK)
    b = render_now_column(dataclasses.replace(now_view, nl=without), DARK)
    assert _count_near(a, TEXT_BOX, DARK.series) > 30
    assert _count_near(b, TEXT_BOX, DARK.series) == 0


def _ink_rows(img, theme, box=TEXT_BOX):
    x0, y0, x1, y1 = box
    return {y for y in range(y0, y1) if any(img.getpixel((x, y)) != theme.bg for x in range(x0, x1))}


def test_blank_author_leaves_no_empty_row(now_view):
    def render(**fields):
        nl = dataclasses.replace(now_view.nl, **fields)
        return render_now_column(dataclasses.replace(now_view, nl=nl), DARK)

    title_only = _ink_rows(render(author="", narrator="", series=""), DARK)
    author_rows = _ink_rows(render(author="Park Saenal", narrator="", series=""), DARK) - title_only
    narrator_rows = _ink_rows(render(author="", narrator="BookTTS AI cast", series=""), DARK) - title_only
    assert author_rows and narrator_rows
    # the narrator moves up into the author's row: same top within the font size difference
    assert abs(min(narrator_rows) - min(author_rows)) <= 8


def test_long_title_wraps_to_two_lines(now_view):
    nl = dataclasses.replace(
        now_view.nl, title="The Extraordinarily Long and Winding Title of a Light Novel Adaptation Volume Twelve"
    )
    img = render_now_column(dataclasses.replace(now_view, nl=nl), DARK)
    assert img.size == (W, H)
    assert _count_not(img, (50, 860, 571, 898), DARK.bg) == 0


def test_chapter_and_time_lines(now_view):
    img = render_now_column(now_view, DARK)
    assert _count_near(img, (50, 938, 571, 958), DARK.ink) > 30  # chapter line at 948
    assert _count_near(img, (50, 970, 571, 990), DARK.ink) > 30  # time line at 980


def test_render_nothing_playing():
    view = NowListeningView(None, None, 0, 2026, dict(DEFAULT_BADGE))
    img = render_now_column(view, DARK)
    assert img.size == (W, H) and img.mode == "RGB"
    assert _count_not(img, (50, 90, 570, 610), DARK.bg) == 0  # no cover
    assert _count_near(img, (50, 46, 330, 66), DARK.accent) > 30  # NOTHING PLAYING
    assert _count_near(img, (50, 633, 571, 678), DARK.ink) > 30  # "Nothing played yet"
    assert _count_not(img, (50, 895, 571, 925), DARK.bg) == 0  # no bar, no ticks


def test_footer_default_and_no_mark(now_view):
    for theme in (DARK, PAPER):
        img = render_now_column(now_view, theme)
        # same baselines as the wall footer: rule at y 1044, text at y 1064
        assert _count_near(img, (60, 1044, 560, 1045), theme.muted, 60) > 450  # rule
        assert _count_not(img, (50, 1000, 571, 1040), theme.bg) == 0  # the old 1024 rule is gone
        assert _count_near(img, (50, 1054, 300, 1074), theme.ink) > 20  # "7 finished in 2026"
        assert _count_near(img, (50, 1052, 571, 1076), theme.accent) == 0
        assert _count_near(img, (300, 1052, 620, 1076), theme.mark) == 0  # ESTANTE lives on the wall


def test_badge_draws_message_in_accent(now_view):
    badge = {"message_waiting": True, "dnd": True, "count": 1, "from": "Jasmine"}
    img = render_now_column(dataclasses.replace(now_view, badge=badge), DARK)
    assert _count_near(img, (50, 1052, 571, 1076), DARK.accent) > 30
    assert _count_not(img, (50, 1052, 571, 1076), DARK.bg) > 100


def test_scaled_column(now_view):
    s = 720 / 1080
    img = render_now_column(now_view, DARK, size=(round(620 * s), 720), scale=s)
    assert img.size == (413, 720)
