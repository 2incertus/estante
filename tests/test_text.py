import os

from PIL import ImageFont

from estante_lib.text import FONT_DIR, SANS, SERIF, ellipsize, fit_text, font, text_width

ELLIPSIS = "\u2026"


def test_fonts_are_vendored():
    assert os.path.isfile(os.path.join(FONT_DIR, SERIF))
    assert os.path.isfile(os.path.join(FONT_DIR, SANS))


def test_font_weight_does_not_raise():
    assert font(SANS, 15, 600).size == 15
    assert font(SERIF, 52, 500).size == 52
    assert isinstance(font(SERIF, 20), ImageFont.FreeTypeFont)


def test_font_weight_changes_width():
    text = "NOW LISTENING"
    assert text_width(font(SANS, 20, 800), text) > text_width(font(SANS, 20, 300), text)


def test_ellipsize_keeps_short_text_and_trims_long():
    f = font(SERIF, 20)
    assert ellipsize("Jade City", f, 400) == "Jade City"
    out = ellipsize("A very long title that will not fit in a narrow column", f, 120)
    assert out.endswith(ELLIPSIS) and text_width(f, out) <= 120
    assert "\u2014" not in out


def test_fit_text_short_title_single_line_max_size():
    lines, fnt = fit_text("Jade City", SERIF, 52, 30, 290, 2)
    assert lines == ["Jade City"] and fnt.size == 52


def test_fit_text_shrinks_before_wrapping():
    t = "The Way of Kings"
    assert text_width(font(SERIF, 52), t) > 290
    lines, fnt = fit_text(t, SERIF, 52, 30, 290, 2)
    assert len(lines) == 1 and 30 <= fnt.size < 52
    assert text_width(fnt, lines[0]) <= 290


def test_fit_text_wraps_long_title():
    t = "I'm in Love with the Villainess: She's So Cheeky for a Commoner, Vol. 02"
    lines, fnt = fit_text(t, SERIF, 52, 30, 290, 2)
    assert 1 < len(lines) <= 2 and all(text_width(fnt, l) <= 290 for l in lines)


def test_fit_text_ellipsizes_absurd_title():
    lines, fnt = fit_text("word " * 80, SERIF, 52, 30, 290, 2)
    assert len(lines) == 2 and lines[-1].endswith(ELLIPSIS) and fnt.size == 30
    assert all(text_width(fnt, l) <= 290 for l in lines)


def test_fit_text_reaches_odd_min_size():
    lines, fnt = fit_text("word " * 80, SERIF, 52, 31, 290, 2)
    assert fnt.size == 31
