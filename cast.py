"""Reply through a Google Cast speaker (current kitchen setup, until a speaker hangs off the
ReSpeaker). The daemon's web server serves each rendered reply as a WAV; say() returns when the
speaker has finished so the daemon can listen again. No echo cancellation this way, so the
daemon does not listen while the speaker talks.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
import wave

import numpy as np

from voice import web

log = logging.getLogger("voice.cast")
RATE = 16000


class CastVoice:
    """Wraps a renderer (anything with render(text) -> int16 16 kHz array) and plays through a cast device."""

    def __init__(self, renderer, device_name: str):
        import pychromecast
        self.renderer = renderer
        self.ip, self.port = web.start()
        casts, self.browser = pychromecast.get_listed_chromecasts(friendly_names=[device_name], timeout=15)
        if not casts:
            self.browser.stop_discovery()
            raise RuntimeError(f"cast device {device_name!r} not found")
        self.cast = casts[0]          # the browser stays alive: pychromecast resolves the host through it
        self.cast.wait(timeout=15)
        log.info("cast ready: %s at %s", device_name, self.cast.cast_info.host)

    def say(self, text: str) -> None:
        t = time.monotonic()
        audio = self.renderer.render(text)
        t_render = time.monotonic() - t
        name = f"{uuid.uuid4().hex}.wav"
        fp = os.path.join(web.MEDIA_DIR, name)
        with wave.open(fp, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(audio.astype(np.int16).tobytes())
        seconds = len(audio) / RATE
        mc = self.cast.media_controller
        t1 = time.monotonic()
        mc.play_media(f"http://{self.ip}:{self.port}/media/{name}", "audio/wav")
        mc.block_until_active(timeout=10)
        started_at = None
        while True:                                   # wait for playback to start, then for its length
            st = mc.status.player_state
            if started_at is None and st in ("PLAYING", "BUFFERING"):
                started_at = time.monotonic()
            if started_at is not None and (st in ("IDLE", "UNKNOWN") or time.monotonic() - started_at >= seconds + 0.5):
                break
            if started_at is None and time.monotonic() - t1 > 10:
                break
            time.sleep(0.1)
        t_start = (started_at or time.monotonic()) - t1
        log.info("cast said %d chars: render %.1fs, start %.1fs, audio %.1fs, total %.1fs", len(text), t_render, t_start, seconds, time.monotonic() - t)
        try:
            os.remove(fp)
        except OSError:
            pass
