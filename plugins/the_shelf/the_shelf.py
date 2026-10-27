"""InkyPi plugin: one Audiobookshelf library as a shelf of covers, paged or as one mosaic."""
import logging

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.estante_lib.abs import AbsError
from plugins.estante_lib.inkypi_glue import (
    EnvKeys,
    abs_client,
    cover_cache,
    dimensions,
    load_cover,
    saturation,
    with_api_key,
)
from plugins.estante_lib.model import build_shelf, progress_index
from plugins.estante_lib.render_shelf import ShelfView, page_books, render_shelf
from plugins.estante_lib.state import read_state, update_state

COVER_WIDTH = 80
MOSAIC = "mosaic"
# layout setting -> (cols, rows) of a page; "mosaic" puts the whole library on one screen.
PAGE_GRIDS = {"pages": (10, 5), "pages_large": (8, 4)}
DEFAULT_LAYOUT = "pages"
DEFAULT_TITLE = "The shelf"
# the settings page waits on Audiobookshelf, so it gives up sooner than a screen draw
SETTINGS_ABS_TIMEOUT_S = 5

logger = logging.getLogger(__name__)


class TheShelf(BasePlugin):
    def generate_settings_template(self):
        template_params = with_api_key(super().generate_settings_template())
        try:
            libraries = abs_client(EnvKeys(), timeout=SETTINGS_ABS_TIMEOUT_S).libraries()
            template_params["libraries"] = [
                {"id": lib.get("id", ""), "name": lib.get("name", "")} for lib in libraries
            ]
            template_params["abs_error"] = ""
        except (AbsError, RuntimeError) as e:
            template_params["libraries"] = []
            template_params["abs_error"] = str(e)
        return template_params

    def generate_image(self, settings, device_config):
        library_id = settings.get("library_id")
        if not library_id:
            raise RuntimeError("Choose a library")
        layout = settings.get("layout", DEFAULT_LAYOUT)
        if layout != MOSAIC and layout not in PAGE_GRIDS:
            layout = DEFAULT_LAYOUT
        title = settings.get("title") or DEFAULT_TITLE
        size = dimensions(device_config)
        client = abs_client(device_config)
        try:
            library = next((lib for lib in client.libraries() if lib.get("id") == library_id), None)
            if library is None:
                raise RuntimeError(f"Library {library_id} is not on Audiobookshelf {client.host}")
            books = build_shelf(client.library_items(library_id), progress_index(client.me()))
        except AbsError as e:
            raise RuntimeError(str(e)) from e

        if layout == MOSAIC:
            cols, rows = PAGE_GRIDS[DEFAULT_LAYOUT]
            shown, page, pages = books, 0, 1
        else:
            cols, rows = PAGE_GRIDS[layout]
            requested = read_state()["pages"].get(library_id, 0)
            shown, page, pages = page_books(books, requested, cols * rows)
        cache = cover_cache(client)
        covers = {}
        for book in shown:
            cover = load_cover(cache, book.item_id, book.updated_at, COVER_WIDTH)
            if cover is not None:
                covers[book.item_id] = cover

        view = ShelfView(
            title,
            f"{library.get('name', '')}, by author",
            books,
            covers,
            page=page,
            layout=MOSAIC if layout == MOSAIC else "pages",
            badge=read_state()["badge"],
            cols=cols,
            rows=rows,
        )
        img = render_shelf(view, size, saturation(device_config))

        def record_pages(state):
            state["page_counts"][library_id] = pages

        try:
            update_state(record_pages)
        except OSError as e:  # the image is done; only the conductor's page wrap goes stale
            logger.warning("cannot record the page count of %s: %s", library_id, e)
        return img.convert("RGB")
