"""A local stand-in for Audiobookshelf that serves the recorded fixtures."""
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOKEN = "TESTTOKEN"
LIBRARY_FILES = {
    "b14de8f5-99a5-4cba-b970-6a2cb65f2993": "audiobooks.json",
    "65bcd45d-1cce-496c-950e-4f3aabf5f5eb": "light_novels.json",
}


class FakeAbs:
    def __init__(self, fixtures_dir, fail_with=None):
        self.fixtures_dir = Path(fixtures_dir)
        self.fail_with = fail_with
        self.calls = []
        self.overrides = {}
        # path substring -> seconds to wait before answering (a hanging server)
        self.delays = {}
        self._server = None
        self._thread = None

    def __enter__(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                fake._handle(self)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        return False

    def override(self, path, payload):
        """Serve `payload` as JSON for this exact path, ahead of the fixtures."""
        self.overrides[path] = payload

    @property
    def base_url(self):
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def _route(self, path, query=None):
        """Return (status, content_type, body) for a GET path."""
        if path in self.overrides:
            return self._json(self.overrides[path])
        parts = path.strip("/").split("/")
        if parts[:1] != ["api"]:
            return 404, None, None
        rest = parts[1:]
        if rest == ["libraries"]:
            libs = json.loads((self.fixtures_dir / "libraries.json").read_text())
            return self._json({"libraries": libs})
        if len(rest) == 3 and rest[0] == "libraries" and rest[2] == "items":
            name = LIBRARY_FILES.get(rest[1])
            return self._file(name, "application/json") if name else (404, None, None)
        if rest == ["me"]:
            return self._file("me.json", "application/json")
        if rest == ["me", "items-in-progress"]:
            return self._file("in_progress.json", "application/json")
        if len(rest) == 2 and rest[0] == "items":
            return self._file(f"item_{rest[1]}.json", "application/json")
        if len(rest) == 3 and rest[:2] == ["me", "progress"]:
            return self._file(f"progress_{rest[2]}.json", "application/json")
        if len(rest) == 3 and rest[0] == "items" and rest[2] == "cover":
            width = (query or {}).get("width", [""])[0]
            sized = f"covers/{rest[1]}-w{width}.jpg"
            if width and (self.fixtures_dir / sized).is_file():
                return self._file(sized, "image/jpeg")
            return self._file(f"covers/{rest[1]}.jpg", "image/jpeg")
        return 404, None, None

    def _json(self, payload):
        return 200, "application/json", json.dumps(payload).encode()

    def _file(self, name, content_type):
        p = self.fixtures_dir / name
        if not p.is_file():
            return 404, None, None
        return 200, content_type, p.read_bytes()

    def _handle(self, req):
        parts = urllib.parse.urlsplit(req.path)
        path = parts.path
        self.calls.append(path)
        for part, seconds in list(self.delays.items()):
            if part in path:
                time.sleep(seconds)
        if self.fail_with is not None:
            status, ctype, body = self.fail_with, None, None
        elif req.headers.get("Authorization") != f"Bearer {TOKEN}":
            status, ctype, body = 401, None, None
        else:
            status, ctype, body = self._route(path, urllib.parse.parse_qs(parts.query))
        if body is None:
            body = b"Not Found" if status == 404 else b"error"
            ctype = "text/plain"
        req.send_response(status)
        req.send_header("Content-Type", ctype)
        req.send_header("Content-Length", str(len(body)))
        req.end_headers()
        req.wfile.write(body)
