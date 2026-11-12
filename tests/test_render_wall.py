import dataclasses

from PIL import Image, ImageOps, ImageStat

from estante_lib.model import STATUS_LISTENING, STATUS_READ, STATUS_UNREAD, ShelfBook, shelf_counts
from estante_lib.render_wall import WallView, grid_for, render_wall
from estante_lib.theme import DARK, PAPER

W, H = 1230, 1080
# 311 audiobooks: 21 x 15 cells of 58 px, tiles of 55, the 21 x 58 - 3 = 1215 px grid centred
CELL, TILE, X0, Y0, COLS = 58, 55, (1230 - 1215) // 2, 110, 21


def _near(px, colour, tol=24):
    return all(abs(a - b) <= tol for a, b in zip(px, colour))


def _count_near(img, box, colour, tol=24):
    x0, y0, x1, y1 = box
    return sum(1 for x in range(x0, x1) for y in range(y0, y1) if _near(img.getpixel((x, y)), colour, tol))


def _tile_xy(idx, cols=COLS, cell=CELL, x0=X0, y0=Y0):
    r, c = divmod(idx, cols)
    return x0 + c * cell, y0 + r * cell


def _tile_mean(img, idx):
    x, y = _tile_xy(idx)
    return sum(ImageStat.Stat(img.crop((x, y, x + TILE, y + TILE)).convert("L")).mean)


def _books(statuses):
    return [ShelfBook(str(i), f"t{i}", "a", "", 1, s) for i, s in enumerate(statuses)]


def _view(books, covers=None):
    return WallView("Audiobooks", "Audiobooks, by author", books, covers or {}, shelf_counts(books))


def test_grid_for():
    assert grid_for(311, (1230, 920)) == (21, 15, 58)
    assert grid_for(597, (1230, 920)) == (29, 21, 42)
    assert grid_for(0, (1230, 920)) == (1, 1, 920)


def test_wall_grid_for_zero_and_many():
    assert grid_for(5000, (1230, 920)) == (82, 61, 15)
    for n in (0, 5000):
        books = _books([STATUS_UNREAD] * n)
        img = render_wall(_view(books), DARK)
        assert img.size == (W, H) and img.mode == "RGB"
    # far more books than pixels: cells never collapse below one pixel of tile
    cols, rows, cell = grid_for(10**6, (1230, 920))
    assert cell >= 4


def test_wall_size_mode_and_background(wall_view):
    for theme in (DARK, PAPER):
        img = render_wall(wall_view, theme)
        assert img.size == (W, H) and img.mode == "RGB"
        assert img.getpixel((W - 3, 100)) == theme.bg  # between the header rule and the tiles
        assert img.getpixel((W - 3, H - 3)) == theme.bg


def test_listening_tile_has_frame_after_all_tiles(wall_view):
    img = render_wall(wall_view, DARK)
    listening = [i for i, b in enumerate(wall_view.books) if b.status == STATUS_LISTENING]
    assert listening
    for i, b in enumerate(wall_view.books):
        x, y = _tile_xy(i)
        framed = img.getpixel((x - 2, y + 20)) == DARK.frame and img.getpixel((x + TILE + 1, y + 20)) == DARK.frame
        assert framed == (b.status == STATUS_LISTENING), (i, b.status)
    x, y = _tile_xy(listening[0])
    assert img.getpixel((x + 20, y - 2)) == DARK.frame
    assert img.getpixel((x + 20, y + TILE + 1)) == DARK.frame


def _dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _ghost(colour, theme):
    """A solid colour after the unread treatment: greyscale, then 70% toward the background."""
    grey = Image.new("RGB", (1, 1), colour).convert("L").convert("RGB")
    return Image.blend(grey, Image.new("RGB", (1, 1), theme.bg), 0.70).getpixel((0, 0))


def test_unread_tile_sits_closer_to_the_background_than_read(wall_view):
    cover = next(iter(wall_view.covers.values()))
    books = _books([STATUS_READ, STATUS_UNREAD])
    covers = {"0": cover, "1": cover}
    for theme in (DARK, PAPER):
        img = render_wall(_view(books, covers), theme)
        cols, rows, cell = grid_for(2, (W, 920))
        tile = cell - 3
        x0 = (W - (cols * cell - 3)) // 2

        def crop(i):
            return img.crop((x0 + i * cell, Y0, x0 + i * cell + tile, Y0 + tile))

        read_mean = ImageStat.Stat(crop(0)).mean
        unread_mean = ImageStat.Stat(crop(1)).mean
        assert _dist(unread_mean, theme.bg) < _dist(read_mean, theme.bg), theme.name
        # the read tile is the cover itself, full colour, untouched
        expected = ImageOps.fit(cover.convert("RGB"), (tile, tile), Image.Resampling.LANCZOS)
        assert crop(0).tobytes() == expected.tobytes()
        # the unread tile is grey under the background tint: take out the 70% of bg and
        # the remaining cover part has equal channels
        r, g, b = ((m - 0.7 * c) / 0.3 for m, c in zip(unread_mean, theme.bg))
        assert abs(r - g) < 4 and abs(g - b) < 4


def test_unread_tiles_are_ghosts_on_both_themes(wall_view):
    unread = [i for i, b in enumerate(wall_view.books[:12]) if b.status == STATUS_UNREAD]
    assert unread
    for theme in (DARK, PAPER):
        img = render_wall(wall_view, theme)
        # 70% of every pixel is the background: each channel stays in bg * 0.7 + [0, 255 * 0.3]
        for i in unread:
            x, y = _tile_xy(i)
            crop = img.crop((x, y, x + TILE, y + TILE))
            for (lo, hi), c in zip(crop.getextrema(), theme.bg):
                assert lo >= c * 0.7 - 1 and hi <= c * 0.7 + 255 * 0.3 + 1, (theme.name, i)


def test_missing_cover_tiles(wall_view):
    books = _books([STATUS_READ, STATUS_UNREAD, STATUS_LISTENING])
    for theme in (DARK, PAPER):
        img = render_wall(_view(books), theme)
        cols, rows, cell = grid_for(3, (W, 920))
        tile = cell - 3
        x0 = (W - (cols * cell - 3)) // 2
        mid = tile // 2
        read = img.getpixel((x0 + mid, Y0 + mid))
        ghost = img.getpixel((x0 + cell + mid, Y0 + mid))
        assert read == theme.paper_tile  # read, no cover
        assert img.getpixel((x0 + 2 * cell + mid, Y0 + mid)) == theme.paper_tile  # listening
        assert ghost == _ghost(theme.paper_tile, theme)
        assert _dist(ghost, theme.bg) < _dist(read, theme.bg)


def test_header_and_footer(wall_view):
    for theme in (DARK, PAPER):
        img = render_wall(wall_view, theme)
        assert _count_near(img, (0, 28, 300, 70), theme.ink) > 50  # title, Garamond 44
        assert _count_near(img, (W - 500, 26, W, 46), theme.ink) > 30  # counts
        assert _count_near(img, (W - 300, 58, W, 75), theme.series) > 10  # subtitle
        assert _count_near(img, (300, 86, 900, 87), theme.ink, 60) > 500  # header rule
        assert _count_near(img, (300, 1044, 900, 1045), theme.muted, 60) > 500  # footer rule
        assert _count_near(img, (0, 1054, 300, 1074), theme.ink) > 30  # percentage
        assert _count_near(img, (W - 110, 1056, W, 1072), theme.mark) > 20  # ESTANTE
        assert _count_near(img, (0, 1056, W - 120, 1072), theme.mark) == 0


def test_footer_percentage_text_is_stable(wall_view):
    counts = shelf_counts(wall_view.books)
    pct = round(100 * counts[STATUS_READ] / counts["total"])
    other = dataclasses.replace(
        wall_view, counts={**counts, STATUS_READ: 0, STATUS_UNREAD: counts[STATUS_UNREAD] + counts[STATUS_READ]}
    )
    a = render_wall(wall_view, DARK).crop((0, 1050, 300, 1078))
    b = render_wall(other, DARK).crop((0, 1050, 300, 1078))
    assert pct > 0 and a.tobytes() != b.tobytes()


def test_light_novels_grid(novels_wall_view):
    img = render_wall(novels_wall_view, DARK)
    assert img.size == (W, H)
    cols, rows, cell = grid_for(597, (W, 920))
    x0 = (W - (cols * cell - 3)) // 2
    last = 596
    r, c = divmod(last, cols)
    x, y = x0 + c * cell, Y0 + r * cell
    # the last tile is a missing-cover ghost, inside the tile area
    assert y + cell - 3 <= 1030
    assert img.getpixel((x + 5, y + 5)) != DARK.bg


def test_scaled_wall():
    books = _books([STATUS_READ] * 10)
    img = render_wall(_view(books), DARK, size=(820, 720), scale=720 / 1080)
    assert img.size == (820, 720)


def test_tiles_from_large_covers_are_square_crops():
    cover = Image.new("RGB", (120, 180), (200, 10, 10))
    cover.paste((10, 200, 10), (0, 0, 120, 30))  # a green band cropped away by a centred fit
    books = _books([STATUS_READ])
    img = render_wall(_view(books, {"0": cover}), DARK)
    cols, rows, cell = grid_for(1, (W, 920))
    x0 = (W - (cols * cell - 3)) // 2
    assert _near(img.getpixel((x0 + 5, Y0 + 5)), (200, 10, 10))
