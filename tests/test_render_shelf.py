import dataclasses

from estante_lib.model import STATUS_LISTENING, STATUS_READ, STATUS_UNREAD, ShelfBook, shelf_counts
from estante_lib.palette import BLACK, BLUE, GREEN, RED, WHITE, driver_palette, inky_pass, to_rgb
from estante_lib.render_shelf import (
    MOSAIC_FLAT_BELOW,
    PAGE_COLS,
    PAGE_ROWS,
    PER_PAGE,
    ShelfView,
    grid_for,
    page_books,
    render_shelf,
)

CELL, TILE = 68, 64
X0 = 60
Y0 = 88 + (344 - PAGE_ROWS * CELL) // 2  # 90: the 5 x 68 grid centred in 344


def _tile_xy(idx, cols=PAGE_COLS, cell=CELL, x0=X0, y0=Y0):
    r, c = divmod(idx, cols)
    return x0 + c * cell, y0 + r * cell


def _count(img, box, index):
    x0, y0, x1, y1 = box
    return sum(1 for x in range(x0, x1) for y in range(y0, y1) if img.getpixel((x, y)) == index)


def _first(books, status, with_cover=None, covers=None, limit=PER_PAGE):
    for i, b in enumerate(books[:limit]):
        if b.status == status and (with_cover is None or (b.item_id in covers) == with_cover):
            return i
    raise AssertionError(f"no {status} book")


def test_constants():
    assert (PAGE_COLS, PAGE_ROWS, PER_PAGE, MOSAIC_FLAT_BELOW) == (10, 5, 50, 40)


def test_page_books_clamps_and_counts():
    books = [ShelfBook(str(i), "t", "a", "", 1, STATUS_UNREAD) for i in range(311)]
    assert page_books(books, 6)[1:] == (6, 7) and len(page_books(books, 6)[0]) == 11
    assert page_books(books, 99)[1] == 6 and page_books(books, -1)[1] == 0
    assert page_books(books, 0)[0][0].item_id == "0" and page_books(books, 1)[0][0].item_id == "50"
    assert page_books(books, 9, 32)[1:] == (9, 10) and len(page_books(books, 9, 32)[0]) == 311 - 288
    assert page_books([], 3) == ([], 0, 1)


def test_grid_for_light_novels():
    assert grid_for(597, (720, 344)) == (36, 17, 20)
    assert grid_for(311, (720, 344)) == (26, 12, 27)
    assert grid_for(0, (720, 344)) == (1, 1, 344)


def test_pages_layout_places_fifty_tiles_and_red_frame(shelf_view):
    img = render_shelf(shelf_view)
    assert img.size == (800, 480) and img.mode == "P" and max(img.getdata()) <= 5
    idx = _first(shelf_view.books, STATUS_LISTENING)
    x, y = _tile_xy(idx)
    assert img.getpixel((x - 2, y + 30)) == RED  # frame left
    assert img.getpixel((x + TILE + 1, y + 30)) == RED  # frame right
    assert img.getpixel((x + 30, y - 2)) == RED  # frame top
    # the 50th tile exists (page 0 is full): 64 px square at row 4, col 9
    assert len(shelf_view.books) >= 50
    x, y = 60 + 9 * 68, 88 + 4 * 68
    assert sum(1 for i in range(64) for j in range(64) if img.getpixel((x + i, y + j)) != WHITE) >= 20
    # nothing beyond ten columns
    assert all(img.getpixel((x, 88 + 2 * 68 + 30)) == WHITE for x in range(741, 761))


def test_red_frames_only_on_listening_tiles(shelf_view):
    img = render_shelf(shelf_view)
    for i, b in enumerate(shelf_view.books[:PER_PAGE]):
        x, y = _tile_xy(i)
        framed = img.getpixel((x - 2, y + 30)) == RED and img.getpixel((x + TILE + 1, y + 30)) == RED
        assert framed == (b.status == STATUS_LISTENING), (i, b.status)


def test_unread_tile_is_lighter_than_read(shelf_view):
    img = render_shelf(shelf_view)
    unread = _first(shelf_view.books, STATUS_UNREAD, True, shelf_view.covers)
    read = _first(shelf_view.books, STATUS_READ, True, shelf_view.covers)
    ux, uy = _tile_xy(unread)
    rx, ry = _tile_xy(read)
    white_unread = _count(img, (ux, uy, ux + TILE, uy + TILE), WHITE)
    white_read = _count(img, (rx, ry, rx + TILE, ry + TILE), WHITE)
    assert white_unread > white_read + 0.25 * TILE * TILE


def test_missing_cover_read_tile_is_solid_blue(shelf_view):
    img = render_shelf(shelf_view)
    idx = _first(shelf_view.books, STATUS_READ, False, shelf_view.covers)
    x, y = _tile_xy(idx)
    assert _count(img, (x, y, x + TILE, y + TILE), BLUE) == TILE * TILE


def test_header_counts_and_page_line(shelf_view):
    # audiobooks.json + me.json: 73 read, 13 listening, 225 unread, 311 total (pinned with jq)
    assert shelf_counts(shelf_view.books) == {"read": 73, "listening": 13, "unread": 225, "total": 311}
    img = render_shelf(shelf_view)
    assert _count(img, (40, 25, 300, 64), BLACK) > 100  # "The shelf"
    assert _count(img, (420, 26, 761, 47), BLACK) > 100  # counts line
    assert _count(img, (420, 52, 761, 69), BLUE) > 50  # subtitle and page line
    assert all(img.getpixel((x, 76)) == BLACK and img.getpixel((x, 77)) == BLACK for x in range(40, 761))
    last = render_shelf(dataclasses.replace(shelf_view, page=6))
    assert _count(last, (420, 52, 761, 69), BLUE) > 50
    # page 7 of 7 holds 11 books (rows 1 and 2): the third row is empty
    assert _count(last, (40, Y0 + 2 * CELL + 10, 761, Y0 + 2 * CELL + 50), WHITE) == 721 * 40


def test_large_tiles_eight_by_four(shelf_view):
    view = dataclasses.replace(shelf_view, cols=8, rows=4)
    img = render_shelf(view)
    assert max(img.getdata()) <= 5
    tile, cell = 82, 86
    x0 = 40 + (720 - 8 * cell) // 2
    idx = _first(shelf_view.books, STATUS_LISTENING, limit=32)
    x, y = _tile_xy(idx, cols=8, cell=cell, x0=x0, y0=88)
    assert img.getpixel((x - 2, y + 40)) == RED and img.getpixel((x + tile + 1, y + 40)) == RED
    # 32 tiles: the 32nd tile is drawn, nothing below the fourth row
    x, y = _tile_xy(31, cols=8, cell=cell, x0=x0, y0=88)
    assert _count(img, (x, y, x + tile, y + tile), WHITE) < tile * tile


def test_mosaic_light_novels_flat_tiles(novels_view):
    cols, rows, cell = grid_for(len(novels_view.books), (720, 344))
    tile = cell - 1
    assert tile < MOSAIC_FLAT_BELOW
    x0 = 40 + (720 - cols * cell) // 2
    y0 = 88 + (344 - rows * cell) // 2
    img = render_shelf(novels_view)
    assert img.size == (800, 480) and max(img.getdata()) <= 5
    unread = _first(novels_view.books, STATUS_UNREAD, limit=len(novels_view.books))
    x, y = _tile_xy(unread, cols, cell, x0, y0)
    assert img.getpixel((x + tile // 2, y + tile // 2)) == WHITE
    assert img.getpixel((x, y)) == BLACK and img.getpixel((x + tile - 1, y + tile - 1)) == BLACK
    listening = _first(novels_view.books, STATUS_LISTENING, limit=len(novels_view.books))
    x, y = _tile_xy(listening, cols, cell, x0, y0)
    assert _count(img, (x, y, x + tile, y + tile), RED) == tile * tile
    read = _first(novels_view.books, STATUS_READ, limit=len(novels_view.books))
    x, y = _tile_xy(read, cols, cell, x0, y0)
    colour = img.getpixel((x + tile // 2, y + tile // 2))
    assert colour not in (WHITE, RED)
    assert _count(img, (x, y, x + tile, y + tile), colour) == tile * tile
    # the whole library is on screen: the last tile is drawn
    x, y = _tile_xy(len(novels_view.books) - 1, cols, cell, x0, y0)
    assert img.getpixel((x, y)) != WHITE


def test_mosaic_audiobooks_shows_every_book(shelf_view):
    view = dataclasses.replace(shelf_view, layout="mosaic")
    cols, rows, cell = grid_for(len(view.books), (720, 344))
    x0 = 40 + (720 - cols * cell) // 2
    y0 = 88 + (344 - rows * cell) // 2
    img = render_shelf(view)
    tiles = [_tile_xy(i, cols, cell, x0, y0) for i in range(len(view.books))]
    assert all(img.getpixel((x, y)) != WHITE for x, y in tiles)  # each has an outline or a fill
    beyond = _tile_xy(len(view.books), cols, cell, x0, y0)
    assert img.getpixel(beyond) == WHITE


def test_empty_shelf_renders():
    img = render_shelf(ShelfView("The shelf", "Audiobooks, by author", [], {}))
    assert img.size == (800, 480) and img.mode == "P"
    assert _count(img, (40, 88, 761, 433), WHITE) == 721 * 345
    mosaic = render_shelf(ShelfView("The shelf", "Audiobooks, by author", [], {}, layout="mosaic"))
    assert mosaic.size == (800, 480)


def test_inky_pass_noop_on_shelf(shelf_view, novels_view):
    for view in (shelf_view, novels_view):
        img = render_shelf(view)
        assert inky_pass(to_rgb(img, driver_palette(0.5))).tobytes() == img.tobytes()


def test_footer_percent_read(shelf_view):
    img = render_shelf(shelf_view)
    assert all(img.getpixel((x, 444)) == BLACK for x in range(40, 761))
    assert _count(img, (40, 452, 400, 472), BLACK) > 50  # "23% of the shelf read"
    assert _count(img, (40, 452, 400, 472), RED) == 0
    assert _count(img, (660, 452, 761, 472), GREEN) > 0


def test_badge_on_shelf_footer(shelf_view):
    badge = {"message_waiting": True, "dnd": False, "count": 2, "from": "Jasmine"}
    img = render_shelf(dataclasses.replace(shelf_view, badge=badge))
    row = [img.getpixel((x, 462)) for x in range(40, 400)]
    assert RED in row
    assert _count(img, (660, 452, 761, 472), GREEN) > 0  # the mark still fits on the wide footer


def test_mosaic_red_only_for_listening(shelf_view):
    # Last Argument of Kings (read, index 2) has a mostly red cover: its flat tile takes
    # the next most common ink so a solid RED tile always means "listening".
    view = dataclasses.replace(shelf_view, layout="mosaic")
    cols, rows, cell = grid_for(len(view.books), (720, 344))
    tile = cell - 1
    x0 = 40 + (720 - cols * cell) // 2
    y0 = 88 + (344 - rows * cell) // 2
    img = render_shelf(view)
    assert view.books[2].status == STATUS_READ and view.books[2].item_id in view.covers
    for i, b in enumerate(view.books):
        x, y = _tile_xy(i, cols, cell, x0, y0)
        centre = img.getpixel((x + tile // 2, y + tile // 2))
        assert (centre == RED) == (b.status == STATUS_LISTENING), (i, b.title)
