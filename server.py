from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from seattle_finder.cache import JsonCache
from seattle_finder.config import ROOT, default_config, google_server_key
from seattle_finder.demographics import load_candidates
from seattle_finder.google_maps import GoogleMapsClient
from seattle_finder.http_client import HttpClient
from seattle_finder.rents import load_rent_estimates
from seattle_finder.scoring import analyze


WEB_DIR = ROOT / "web"
CACHE_DIR = ROOT / "cache"


class AppHandler(BaseHTTPRequestHandler):
    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/?"):
            self._send_file(WEB_DIR / "index.html")
            return
        if self.path == "/api/config":
            self._send_json(default_config())
            return

        static_path = WEB_DIR / self.path.lstrip("/")
        self._send_file(static_path)

    def do_POST(self) -> None:
        if self.path != "/api/analyze":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            config = default_config()
            config.update(payload)

            cache = JsonCache(CACHE_DIR)
            http = HttpClient(cache)
            maps = GoogleMapsClient(http, google_server_key())
            candidates = load_candidates(http, config)
            rent_estimates = load_rent_estimates(http, candidates, config)
            result = analyze(candidates, maps, config, rent_estimates)
            self._send_json(result)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=500)


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"Seattle Finder running at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run()
