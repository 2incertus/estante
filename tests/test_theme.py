import dataclasses

import pytest

from estante_lib.theme import DARK, PAPER, THEMES, Theme, get_theme


def test_dark_values():
    assert DARK == Theme(
        "dark",
        bg=(18, 18, 20),
        ink=(236, 233, 224),
        muted=(150, 150, 144),
        accent=(214, 69, 58),
        series=(120, 160, 230),
        mark=(90, 170, 110),
        frame=(214, 69, 58),
        paper_tile=(40, 40, 44),
    )


def test_paper_values():
    assert PAPER == Theme(
        "paper",
        bg=(236, 233, 224),
        ink=(26, 28, 31),
        muted=(110, 110, 104),
        accent=(168, 34, 30),
        series=(38, 78, 160),
        mark=(40, 96, 52),
        frame=(168, 34, 30),
        paper_tile=(222, 219, 210),
    )


def test_themes_and_lookup():
    assert THEMES == {"dark": DARK, "paper": PAPER}
    assert get_theme("dark") is DARK and get_theme("paper") is PAPER


def test_unknown_theme_lists_names():
    with pytest.raises(KeyError) as err:
        get_theme("sepia")
    assert "sepia" in str(err.value) and "dark" in str(err.value) and "paper" in str(err.value)


def test_theme_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        DARK.bg = (0, 0, 0)
