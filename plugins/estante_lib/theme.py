"""Screen themes: dark (the default, near-black with warm off-white type) and paper."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    name: str
    bg: tuple
    ink: tuple
    muted: tuple
    accent: tuple
    series: tuple
    mark: tuple
    frame: tuple
    paper_tile: tuple


DARK = Theme(
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

PAPER = Theme(
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

THEMES = {"dark": DARK, "paper": PAPER}


def get_theme(name):
    try:
        return THEMES[name]
    except KeyError:
        raise KeyError(f"unknown theme {name!r}, expected one of: {', '.join(THEMES)}") from None
