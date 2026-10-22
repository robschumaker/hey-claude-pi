"""Reply through a Google Cast speaker (current kitchen setup, until a speaker hangs off the
ReSpeaker). The Pi serves each rendered reply as a WAV on its LAN address and casts the URL;
say() returns when the speaker has finished so the daemon can listen again. No echo
cancellation is possible this way, so the daemon does not listen while the speaker talks.
"""
from __future__ import annotations

import http.server
import logging
import os
import socket
import socketserver
import tempfile
import threading
import time
import uuid
import wave

import numpy as np

log = logging.getLogger("voice.cast")
RATE = 16000


def lan_ip(probe: str = "192.0.2.1") -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((probe, 1)); return s.getsockname()[0]
    finally:
        s.close()


class _Server(socketserver.TCPServer):
    allow_reuse_address = True


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class CastVoice:
    """Wraps a renderer (anything with render(text) -> int16 16 kHz array) and plays through a cast device."""

    def __init__(self, renderer, device_name: str, port: int = 8766):
        import pychromecast
        self.renderer = renderer
        self.dir = tempfile.mkdtemp(prefix="voice-cast-")
        self.ip = lan_ip()
        handler = lambda *a, **k: _Quiet(*a, directory=self.dir, **k)
        self.httpd = _Server((self.ip, port), handler)   # LAN address only, never 0.0.0.0
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.port = port
        casts, self.browser = pychromecast.get_listed_chromecasts(friendly_names=[device_name], timeout=15)
        if not casts:
            self.browser.stop_discovery()
            raise RuntimeError(f"cast device {device_name!r} not found")
        self.cast = casts[0]          # the browser stays alive: pychromecast resolves the host through it
        self.cast.wait(timeout=15)
        log.info("cast ready: %s at %s, serving on http://%s:%d", device_name, self.cast.cast_info.host, self.ip, port)

    def say(self, text: str) -> None:
        t = time.monotonic()
        audio = self.renderer.render(text)
        name = f"{uuid.uuid4().hex}.wav"
        with wave.open(os.path.join(self.dir, name), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(audio.astype(np.int16).tobytes())
        seconds = len(audio) / RATE
        mc = self.cast.media_controller
        mc.play_media(f"http://{self.ip}:{self.port}/{name}", "audio/wav")
        mc.block_until_active(timeout=10)
        deadline = time.monotonic() + seconds + 8
        started = False
        while time.monotonic() < deadline:
            st = mc.status.player_state
            if st in ("PLAYING", "BUFFERING"):
                started = True
            elif started and st in ("IDLE", "UNKNOWN"):
                break
            time.sleep(0.25)
        log.info("cast said %d chars (%.1fs audio) in %.1fs", len(text), seconds, time.monotonic() - t)
        try:
            os.remove(os.path.join(self.dir, name))
        except OSError:
            pass
