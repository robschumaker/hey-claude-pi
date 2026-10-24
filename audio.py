"""Audio for the voice daemon: the ReSpeaker device, wake word, recording with end-pointing,
speech to text (faster-whisper) and text to speech (Piper). All audio is 16 kHz mono int16.
"""
from __future__ import annotations

import logging
import os
import time

import numpy as np

log = logging.getLogger("voice.audio")

RATE = 16000
FRAME = 1280            # 80 ms, what openWakeWord wants
_rng = np.random.default_rng(0)


def find_device(name_part: str) -> tuple[int, int]:
    """Return (input_index, output_index) of the first devices whose name contains name_part."""
    import sounddevice as sd
    inp = out = None
    for i, d in enumerate(sd.query_devices()):
        if name_part.lower() in d["name"].lower():
            if inp is None and d["max_input_channels"] > 0:
                inp = i
            if out is None and d["max_output_channels"] > 0:
                out = i
    if inp is None or out is None:
        raise RuntimeError(f"no audio device matching {name_part!r}; devices: "
                           + "; ".join(d["name"] for d in sd.query_devices()))
    return inp, out


class Ears:
    """One always-open input stream; frames are pulled 80 ms at a time."""

    def __init__(self, device: int, channel: int = 0, channels: int = 2):
        import sounddevice as sd
        self.channel = channel
        self.stream = sd.InputStream(device=device, samplerate=RATE, channels=channels, dtype="int16",
                                     blocksize=FRAME)
        self.stream.start()
        self.last = None

    def frame(self) -> np.ndarray:
        data, overflowed = self.stream.read(FRAME)
        if overflowed:
            log.warning("input overflow")
        self.last = data                       # all channels, for diagnostics
        return data[:, self.channel]

    def close(self):
        self.stream.stop(); self.stream.close()


class WakeWord:
    def __init__(self, model_path: str, threshold: float):
        from openwakeword.model import Model
        self.model = Model(wakeword_models=[model_path], inference_framework="onnx")
        self.name = os.path.splitext(os.path.basename(model_path))[0]
        self.threshold = threshold

    def heard(self, frame: np.ndarray) -> float:
        return self.model.predict(frame)[self.name]

    def reset(self):
        self.model.reset()


def record_utterance(ears: Ears, max_seconds: float = 12.0, start_timeout: float = 6.0,
                     silence_after: float = 0.9, threshold_rms: float = 300.0, on_speech=None,
                     ambient_rms: float | None = None) -> np.ndarray | None:
    """Record until the speaker stops. The speech gate comes from the ambient level measured while
    idle (ambient_rms), never from the first frames after the wake word, which are already speech.
    Waits up to start_timeout for speech, then stops after silence_after seconds of quiet."""
    gate = max(threshold_rms, (ambient_rms or 0.0) * 2.5)
    frames, started, quiet, t0 = [], False, 0.0, time.monotonic()
    peak = 0.0
    while True:
        f = ears.frame()
        rms = float(np.sqrt(np.mean(f.astype(np.float32) ** 2)))
        peak = max(peak, rms)
        elapsed = time.monotonic() - t0
        speaking = rms > gate
        if on_speech is not None:
            on_speech(speaking)
        if not started:
            frames.append(f)
            if len(frames) > 6:            # keep ~0.5 s of pre-roll
                frames.pop(0)
            if speaking:
                started = True
            elif elapsed > start_timeout:
                log.info("no speech within %.0fs (gate %.0f, loudest frame %.0f)", start_timeout, gate, peak)
                return None
            continue
        frames.append(f)
        quiet = quiet + FRAME / RATE if not speaking else 0.0
        if quiet >= silence_after or elapsed > max_seconds:
            audio = np.concatenate(frames)
            log.info("recorded %.1fs (gate %.0f, peak %.0f)", len(audio) / RATE, gate, peak)
            return audio if len(audio) > RATE * 0.4 else None


class Transcriber:
    def __init__(self, model_name: str, threads: int = 4):
        from faster_whisper import WhisperModel
        t = time.monotonic()
        self.model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=threads)
        log.info("whisper %s loaded in %.1fs", model_name, time.monotonic() - t)

    def text(self, audio: np.ndarray) -> str:
        t = time.monotonic()
        segments, _ = self.model.transcribe(audio.astype(np.float32) / 32768.0, language="en",
                                            beam_size=1, vad_filter=True, condition_on_previous_text=False)
        out = " ".join(s.text.strip() for s in segments).strip()
        log.info("stt %.1fs: %r", time.monotonic() - t, out)
        return out


class Voice:
    """Piper text to speech, resampled to 16 kHz, played on the ReSpeaker so its AEC hears it."""

    def __init__(self, voice_path: str, output_device: int):
        from piper import PiperVoice
        self.voice = PiperVoice.load(voice_path)
        self.device = output_device

    def render(self, text: str) -> np.ndarray:
        from scipy.signal import resample_poly
        parts = []
        sr = getattr(getattr(self.voice, "config", None), "sample_rate", 22050)
        for chunk in self.voice.synthesize(text):
            parts.append(np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16))
        if not parts:
            return np.zeros(0, dtype=np.int16)
        audio = np.concatenate(parts)
        if sr != RATE:
            audio = resample_poly(audio.astype(np.float32), RATE, sr).astype(np.int16)
        return audio

    def say(self, text: str) -> None:
        t = time.monotonic()
        audio = self.render(text)
        log.info("tts %.1fs for %d chars", time.monotonic() - t, len(text))
        import sounddevice as sd
        sd.play(audio, samplerate=RATE, device=self.device, blocking=True)


def chime(output_device: int, hz: float = 880.0, ms: int = 120) -> None:
    import sounddevice as sd
    n = int(RATE * ms / 1000)
    tone = (0.25 * np.sin(2 * np.pi * hz * np.arange(n) / RATE) * np.hanning(n) * 32767).astype(np.int16)
    sd.play(tone, samplerate=RATE, device=output_device, blocking=True)
