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


class SpeechDetector:
    """Silero VAD (shipped with openWakeWord): speech probability per 80 ms frame, independent of
    how loud the speaker is. Falls back to an energy gate if the model can't load."""

    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold
        try:
            from openwakeword.vad import VAD
            self.vad = VAD()
        except Exception:
            log.exception("silero VAD unavailable; using the energy gate")
            self.vad = None

    def reset(self):
        if self.vad is not None:
            self.vad.reset_states()

    def speaking(self, frame: np.ndarray, rms: float, gate: float) -> tuple[bool, float]:
        if self.vad is None:
            return rms > gate, rms
        p = float(self.vad.predict(frame, frame_size=640))
        return p >= self.threshold, p


_detector: SpeechDetector | None = None


def record_utterance(ears: Ears, max_seconds: float = 12.0, start_timeout: float = 6.0,
                     silence_after: float = 1.0, threshold_rms: float = 300.0, on_speech=None,
                     ambient_rms: float | None = None) -> np.ndarray | None:
    """Record until the speaker stops. Speech is detected by Silero VAD, so a quiet voice across the
    room counts; the energy gate (ambient-based) is only the fallback. Waits up to start_timeout for
    speech, then stops after silence_after seconds without it. Returns int16 audio or None."""
    global _detector
    if _detector is None:
        _detector = SpeechDetector(float(os.environ.get("VOICE_VAD_THRESHOLD", "0.5")))
    det = _detector
    det.reset()
    gate = max(threshold_rms, (ambient_rms or 0.0) * 2.5)
    frames, started, quiet, t0 = [], False, 0.0, time.monotonic()
    peak, best = 0.0, 0.0
    while True:
        f = ears.frame()
        rms = float(np.sqrt(np.mean(f.astype(np.float32) ** 2)))
        peak = max(peak, rms)
        speaking, score = det.speaking(f, rms, gate)
        best = max(best, score)
        elapsed = time.monotonic() - t0
        if on_speech is not None:
            on_speech(speaking)
        if not started:
            frames.append(f)
            if len(frames) > 6:            # keep ~0.5 s of pre-roll
                frames.pop(0)
            if speaking:
                started = True
            elif elapsed > start_timeout:
                log.info("no speech within %.0fs (best vad %.2f, loudest frame rms %.0f)", start_timeout, best, peak)
                return None
            continue
        frames.append(f)
        quiet = quiet + FRAME / RATE if not speaking else 0.0
        if quiet >= silence_after or elapsed > max_seconds:
            audio = np.concatenate(frames)
            log.info("recorded %.1fs in %.1fs (peak rms %.0f)", len(audio) / RATE, elapsed, peak)
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


def chime(output_device: int, notes=((660.0, 110), (990.0, 160)), gain: float = 0.9) -> None:
    """Acknowledgement: a short rising two-tone right after the wake word, meaning "heard you, go ahead".
    Blocking, so recording starts only once it has finished."""
    import sounddevice as sd
    parts = []
    for hz, ms in notes:
        n = int(RATE * ms / 1000)
        parts.append(np.sin(2 * np.pi * hz * np.arange(n) / RATE) * np.hanning(n))
    tone = (gain * np.concatenate(parts) * 32767).astype(np.int16)
    sd.play(tone, samplerate=RATE, device=output_device, blocking=True)
