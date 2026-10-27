"""What the two InkyPi plugins share: size, saturation, credentials, covers, the local year."""
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from PIL import Image

from .abs import AbsClient, CoverCache
from .state import state_dir

LANDSCAPE_ONLY = "Estante screens are landscape; set InkyPi orientation to horizontal"
NO_CREDENTIALS = "Set ABS_URL and ABS_TOKEN on InkyPi's API Keys page"
ABS_KEYS = {"required": True, "service": "Audiobookshelf", "expected_key": "ABS_URL and ABS_TOKEN"}


def with_api_key(template_params):
    """Mark a settings page with InkyPi's "Requires API Key" tag for the ABS keys."""
    template_params["api_key"] = dict(ABS_KEYS)
    return template_params


class EnvKeys:
    """InkyPi's own key lookup (Config.load_env_key) for code that has no device config.

    InkyPi re-reads its .env on every lookup, so keys edited on the API Keys page apply
    without a restart.
    """

    def load_env_key(self, key):
        load_dotenv(override=True)
        return os.getenv(key)


def dimensions(device_config):
    if device_config.get_config("orientation") == "vertical":
        raise RuntimeError(LANDSCAPE_ONLY)
    return device_config.get_resolution()


def saturation(device_config):
    settings = device_config.get_config("image_settings", {}) or {}
    return float(settings.get("inky_saturation", 0.5))


def abs_client(device_config):
    url = device_config.load_env_key("ABS_URL")
    token = device_config.load_env_key("ABS_TOKEN")
    if not url or not token:
        raise RuntimeError(NO_CREDENTIALS)
    return AbsClient(url, token)


def cover_cache(client):
    folder = os.path.join(state_dir(), "covers")
    os.makedirs(folder, exist_ok=True)
    return CoverCache(folder, client)


def load_cover(cache, item_id, updated_at, width):
    path = cache.path(item_id, updated_at, width)
    if not path:
        return None
    try:
        with Image.open(path) as img:
            return img.convert("RGB")
    except OSError:
        return None


def local_year(device_config):
    """(current year, tzinfo) in InkyPi's configured timezone; UTC when unset or unknown."""
    tz = timezone.utc
    name = device_config.get_config("timezone", None)
    if name:
        try:
            tz = ZoneInfo(name)
        except (ValueError, KeyError, OSError):
            tz = timezone.utc
    return datetime.now(tz).year, tz
