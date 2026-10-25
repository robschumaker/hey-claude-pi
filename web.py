"""Small HTTP server on the Pi's LAN address for the voice daemon: the current state (for the
kitchen dashboard overlay), the overlay script and logo, and rendered replies for casting.
Bound to the LAN address only, never 0.0.0.0.
"""
from __future__ import annotations

import http.server
import json
import logging
import os
import socket
import socketserver
import tempfile
import threading
import time

log = logging.getLogger("voice.web")
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = {"state": "idle", "since": time.time()}
HANDLERS: dict = {}          # name -> callable(dict) -> str; registered by the daemon (music)
EXTRA: dict = {}             # key -> callable() -> dict, merged into state.json (music now-playing)
MEDIA_DIR = tempfile.mkdtemp(prefix="voice-media-")


def set_state(state: str) -> None:
    if STATE["state"] != state:
        STATE.update(state=state, since=time.time())


def lan_ip(probe: str = "192.0.2.1") -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((probe, 1)); return s.getsockname()[0]
    finally:
        s.close()


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/state.json":
            st = dict(STATE)
            for k, f in EXTRA.items():
                try:
                    st[k] = f()
                except Exception:
                    st[k] = None
            return self._send(200, json.dumps(st).encode(), "application/json")
        if path in ("/overlay.js", "/logo.svg"):
            with open(os.path.join(HERE, "web", path[1:]), "rb") as f:
                return self._send(200, f.read(), "application/javascript" if path.endswith(".js") else "image/svg+xml")
        if path.startswith("/media/") and ".." not in path:
            fp = os.path.join(MEDIA_DIR, os.path.basename(path))
            if os.path.isfile(fp):
                with open(fp, "rb") as f:
                    return self._send(200, f.read(), "audio/wav")
        self._send(404, b"not found", "text/plain")

    def do_OPTIONS(self):                                  # CORS preflight from the kiosk page (another port)
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        name = path.strip("/")
        if name not in HANDLERS:
            return self._send(404, b"not found", "text/plain")
        if self.client_address[0] not in ("127.0.0.1", self.server.server_address[0]):   # this Pi only
            return self._send(403, b"local only", "text/plain")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            out = HANDLERS[name](body)
            self._send(200, json.dumps({"result": out}).encode(), "application/json")
        except Exception as e:
            log.exception("web: %s failed", name)
            self._send(500, json.dumps({"error": str(e)}).encode(), "application/json")


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


_server = None


def start(port: int = 8766) -> tuple[str, int]:
    global _server
    if _server is None:
        ip = lan_ip()
        _server = _Server((ip, port), _Handler)
        threading.Thread(target=_server.serve_forever, daemon=True).start()
        log.info("web: http://%s:%d/ (state, overlay, media)", ip, port)
    return _server.server_address
