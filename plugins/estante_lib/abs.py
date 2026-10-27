"""Audiobookshelf client (GET only) and an on-disk cover cache. Stdlib only."""
import glob
import http.client
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request


class AbsError(RuntimeError):
    """Audiobookshelf could not be reached or answered with an error."""


class AbsClient:
    def __init__(self, base_url, token, timeout=15.0):
        self.base_url = base_url.rstrip("/")
        self.host = urllib.parse.urlsplit(self.base_url).netloc or self.base_url
        self._token = token
        self.timeout = timeout

    def _get(self, path, params=None, none_on_404=False):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(
                url, method="GET", headers={"Authorization": f"Bearer {self._token}"}
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if none_on_404 and e.code == 404:
                return None
            raise AbsError(f"Audiobookshelf {self.host}: HTTP {e.code} on {path}") from e
        except urllib.error.URLError as e:
            raise AbsError(f"Audiobookshelf {self.host}: {e.reason}") from e
        except (socket.timeout, TimeoutError) as e:
            raise AbsError(f"Audiobookshelf {self.host}: timed out") from e
        except (OSError, http.client.HTTPException, ValueError) as e:
            raise AbsError(f"Audiobookshelf {self.host}: {e}") from e

    def _decode(self, body, path):
        try:
            return json.loads(body)
        except ValueError as e:
            raise AbsError(f"Audiobookshelf {self.host}: bad JSON on {path}") from e

    def get_json(self, path, params=None):
        return self._decode(self._get(path, params), path)

    def get_bytes(self, path, params=None):
        return self._get(path, params)

    def libraries(self):
        return self.get_json("/api/libraries").get("libraries", [])

    def library_items(self, library_id):
        data = self.get_json(
            f"/api/libraries/{library_id}/items", {"limit": 0, "minified": 1}
        )
        return data.get("results", [])

    def me(self):
        return self.get_json("/api/me")

    def items_in_progress(self, limit=1):
        data = self.get_json("/api/me/items-in-progress", {"limit": limit})
        return data.get("libraryItems", [])

    def item(self, item_id):
        return self.get_json(f"/api/items/{item_id}")

    def progress(self, item_id):
        path = f"/api/me/progress/{item_id}"
        body = self._get(path, none_on_404=True)
        return None if body is None else self._decode(body, path)

    def cover_bytes(self, item_id, width):
        return self._get(
            f"/api/items/{item_id}/cover",
            {"width": width, "format": "jpeg"},
            none_on_404=True,
        )


def creds_from_env(env=None):
    env = os.environ if env is None else env
    url = env.get("ABS_URL", "")
    token = env.get("ABS_TOKEN", "")
    if not url or not token:
        raise AbsError("ABS_URL and ABS_TOKEN are not set")
    return url, token


class CoverCache:
    def __init__(self, cache_dir, client):
        self.cache_dir = cache_dir
        self.client = client

    def path(self, item_id, updated_at, width):
        """Return a local JPEG path for the cover, or None. Never raises."""
        target = os.path.join(self.cache_dir, f"{item_id}-{updated_at}-w{width}.jpg")
        if os.path.exists(target):
            return target
        try:
            body = self.client.cover_bytes(item_id, width)
            if not body:
                return None
            os.makedirs(self.cache_dir, exist_ok=True)
            tmp = target + ".tmp"
            with open(tmp, "wb") as f:
                f.write(body)
            os.replace(tmp, target)
            pattern = os.path.join(glob.escape(self.cache_dir), f"{glob.escape(item_id)}-*-w{width}.jpg")
            for old in glob.glob(pattern):
                if old != target:
                    try:
                        os.remove(old)
                    except OSError:
                        pass
            return target
        except (AbsError, OSError):
            return None
