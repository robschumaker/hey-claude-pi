"""Hosted speech via Deepgram, used when DEEPGRAM_API_KEY is set: the Pi 4 runs whisper and
Piper at about real time, which is too slow to talk to. Standard library only.
"""
from __future__ import annotations

import io
import json
import logging
import os
import time
import urllib.request
import wave

import numpy as np

log = logging.getLogger("voice.hosted")
RATE = 16000


def _wav_bytes(audio: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(audio.astype(np.int16).tobytes())
    return buf.getvalue()


class DeepgramTranscriber:
    def __init__(self, key: str, model: str = "nova-3"):
        self.key, self.model = key, model

    def text(self, audio: np.ndarray) -> str:
        t = time.monotonic()
        url = f"https://api.deepgram.com/v1/listen?model={self.model}&smart_format=true&language=en"
        req = urllib.request.Request(url, data=_wav_bytes(audio), method="POST",
                                     headers={"Authorization": f"Token {self.key}", "Content-Type": "audio/wav"})
        with urllib.request.urlopen(req, timeout=20) as r:
            body = json.load(r)
        out = body["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
        log.info("deepgram stt %.1fs: %r", time.monotonic() - t, out)
        return out


class DeepgramVoice:
    def __init__(self, key: str, output_device: int, model: str = "aura-2-thalia-en"):
        self.key, self.device, self.model = key, output_device, model

    def render(self, text: str) -> np.ndarray:
        url = f"https://api.deepgram.com/v1/speak?model={self.model}&encoding=linear16&sample_rate={RATE}&container=none"
        req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), method="POST",
                                     headers={"Authorization": f"Token {self.key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
        return np.frombuffer(raw, dtype=np.int16)

    def say(self, text: str) -> None:
        import sounddevice as sd
        t = time.monotonic()
        audio = self.render(text)
        log.info("deepgram tts %.1fs for %d chars", time.monotonic() - t, len(text))
        sd.play(audio, samplerate=RATE, device=self.device, blocking=True)


def available() -> str | None:
    return os.environ.get("DEEPGRAM_API_KEY") or None
