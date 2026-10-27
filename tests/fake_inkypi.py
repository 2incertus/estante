"""A local stand-in for InkyPi: GET / and POST /display_plugin_instance, plus a device.json."""
import json
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

AUDIOBOOKS_ID = "b14de8f5-99a5-4cba-b970-6a2cb65f2993"
LIGHT_NOVELS_ID = "65bcd45d-1cce-496c-950e-4f3aabf5f5eb"

DEVICE_JSON = {
    "name": "InkyPi",
    "display_type": "inky",
    "playlist_config": {
        "playlists": [
            {
                "name": "Default",
                "start_time": "00:00",
                "end_time": "24:00",
                "plugins": [
                    {
                        "plugin_id": "now_listening",
                        "name": "Now listening",
                        "plugin_settings": {},
                        "refresh": {"interval": 1800},
                    },
                    {
                        "plugin_id": "the_shelf",
                        "name": "The shelf",
                        "plugin_settings": {"library_id": AUDIOBOOKS_ID},
                        "refresh": {"interval": 1800},
                    },
                    {
                        "plugin_id": "the_shelf",
                        "name": "Light novels",
                        "plugin_settings": {"library_id": LIGHT_NOVELS_ID},
                        "refresh": {"interval": 1800},
                    },
                ],
            }
        ],
        "active_playlist": "Default",
    },
}


class FakeInkyPi:
    def __init__(self, delay_s=0.0):
        self.delay_s = delay_s
        self.displays = []
        self.failed = []
        self._fail_left = 0
        self._lock = threading.Lock()
        self._tmp = None
        self._server = None
        self._thread = None
        self.device_json = None

    def __enter__(self):
        fake = self
        self._tmp = tempfile.TemporaryDirectory(prefix="fake-inkypi-")
        self.device_json = str(Path(self._tmp.name) / "device.json")
        Path(self.device_json).write_text(json.dumps(DEVICE_JSON, indent=2), encoding="utf-8")

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                fake._handle(self, "GET")

            def do_POST(self):
                fake._handle(self, "POST")

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
        self._tmp.cleanup()
        return False

    @property
    def url(self):
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def fail_next(self, n):
        with self._lock:
            self._fail_left = n

    def _reply(self, req, status, payload):
        body = json.dumps(payload).encode()
        req.send_response(status)
        req.send_header("Content-Type", "application/json")
        req.send_header("Content-Length", str(len(body)))
        req.end_headers()
        req.wfile.write(body)

    def _handle(self, req, method):
        if method == "GET" and req.path == "/":
            self._reply(req, 200, {"ok": True})
            return
        if method == "POST" and req.path == "/display_plugin_instance":
            length = int(req.headers.get("Content-Length") or 0)
            data = json.loads(req.rfile.read(length) or b"{}")
            entry = (data.get("playlist_name"), data.get("plugin_id"), data.get("plugin_instance"))
            if self.delay_s:
                time.sleep(self.delay_s)
            with self._lock:
                fail = self._fail_left > 0
                if fail:
                    self._fail_left -= 1
            if fail:
                self.failed.append(entry)
                self._reply(req, 500, {"error": "simulated failure"})
                return
            self.displays.append(entry)
            self._reply(req, 200, {"success": True})
            return
        self._reply(req, 404, {"error": "not found"})
