"""InkyPi plugin: the audiobook in progress (or the last one finished), cover and progress."""
from plugins.base_plugin.base_plugin import BasePlugin
from plugins.estante_lib.abs import AbsError
from plugins.estante_lib.inkypi_glue import (
    abs_client,
    cover_cache,
    dimensions,
    load_cover,
    local_year,
    saturation,
    with_api_key,
)
from plugins.estante_lib.model import finished_in_year, pick_now_listening
from plugins.estante_lib.render_now import NowListeningView, render_now_listening
from plugins.estante_lib.state import read_state

COVER_WIDTH = 400


class NowListening(BasePlugin):
    def generate_settings_template(self):
        return with_api_key(super().generate_settings_template())

    def generate_image(self, settings, device_config):
        size = dimensions(device_config)
        client = abs_client(device_config)
        year, tz = local_year(device_config)
        try:
            nl = pick_now_listening(client)
            cover = None
            if nl is not None:
                cover = load_cover(cover_cache(client), nl.item_id, nl.updated_at, COVER_WIDTH)
            finished = finished_in_year(client.me(), year, tz)
        except AbsError as e:
            raise RuntimeError(str(e)) from e
        view = NowListeningView(nl, cover, finished, year, read_state()["badge"])
        return render_now_listening(view, size, saturation(device_config)).convert("RGB")
