"""household voice daemon: wake word -> record -> whisper -> Claude -> Piper -> ReSpeaker.

Runs as household-voice.service on the Pi. Everything is configured from the household .env
(see .env.example, VOICE_*). Audio in and out both go through the ReSpeaker so its echo
cancellation hears what we play.
"""
from __future__ import annotations

import logging
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from voice.env import load_env  # noqa: E402

load_env(os.environ.get("ENV_FILE", "/home/pi/household/.env"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("voice")

from voice import audio  # noqa: E402
from voice.brain import Brain  # noqa: E402
from voice.brain_cc import ClaudeCodeBrain  # noqa: E402
from voice import hosted  # noqa: E402
from voice import cast as cast_out  # noqa: E402
from voice import web, screen as screen_mod  # noqa: E402


def cfg(name: str, default: str) -> str:
    return os.environ.get(name, default)


def main() -> int:
    inp, out = audio.find_device(cfg("VOICE_DEVICE", "XVF3800"))
    log.info("audio in=%d out=%d", inp, out)
    wake_path = cfg("VOICE_WAKE_MODEL", "/home/pi/voice-models/hey_claude.onnx")
    wake = audio.WakeWord(wake_path, float(cfg("VOICE_WAKE_THRESHOLD", "0.5")))
    if hosted.available():
        log.info("speech via Deepgram")
        stt = hosted.DeepgramTranscriber(hosted.available(), cfg("VOICE_DEEPGRAM_STT", "nova-3"))
        tts = hosted.DeepgramVoice(hosted.available(), out, cfg("VOICE_DEEPGRAM_TTS", "aura-2-thalia-en"))
    else:
        log.info("speech local (whisper + Piper); slow on a Pi 4")
        stt = audio.Transcriber(cfg("VOICE_WHISPER_MODEL", "base.en"))
        tts = audio.Voice(cfg("VOICE_PIPER_VOICE", "/home/pi/voice-models/en_US-lessac-medium.onnx"), out)
    if os.environ.get("ANTHROPIC_API_KEY"):
        log.info("brain: Claude API")
        brain = Brain(cfg("STORE_DIR", "/home/pi/household"), cfg("VOICE_MODEL", "claude-opus-5-5"),
                      user=cfg("VOICE_USER", "voice"))
    else:
        log.info("brain: Claude Code on the subscription")
        brain = ClaudeCodeBrain(model=cfg("VOICE_CC_MODEL", "") or None)
    web.start()                                        # state for the dashboard overlay, media for casting
    scr = screen_mod.Screen()
    output = cfg("VOICE_OUTPUT", "respeaker")
    if output.startswith("cast:"):
        # Current kitchen setup: reply through a Google Cast speaker. No echo cancellation that way,
        # so we simply do not listen while it talks. Long-term: a speaker on the ReSpeaker's amplifier.
        tts = cast_out.CastVoice(tts, output.split(":", 1)[1])
        log.info("output: cast to %s", output.split(":", 1)[1])
    follow_up = float(cfg("VOICE_FOLLOW_UP_SECONDS", "6"))
    channel = int(cfg("VOICE_CHANNEL", "0"))
    ears = audio.Ears(inp, channel=channel)
    diag = cfg("VOICE_DIAG", "0") == "1"           # log near misses
    other = audio.WakeWord(wake_path, wake.threshold)  # both channels are scored; the louder one wins
    log.info("listening for %s on both channels (recording follows the one that wakes; default %d)", wake.name, channel)
    frames, peak0, peak1 = 0, 0, 0
    ambient = [200.0, 200.0]                               # running quiet-room level per channel, for the recorder's gate
    stall = {"frames": 0}

    def _stall_watch():                                   # no audio for 20 s means the array wedged: exit, systemd restarts us via mic-check
        import threading
        last = -1
        while True:
            time.sleep(20)
            if stall["frames"] == last:
                log.error("no audio frames for 20 s; exiting so the service restarts and mic-check reboots the array")
                os._exit(3)
            last = stall["frames"]
    import threading
    threading.Thread(target=_stall_watch, daemon=True).start()
    try:
        while True:
            score = wake.heard(ears.frame())
            o = other.heard(ears.last[:, 1 - channel])
            frames += 1; stall["frames"] = frames
            for ch in (0, 1):
                r = float(np.sqrt(np.mean(ears.last[:, ch].astype(np.float32) ** 2)))
                ambient[ch] = min(ambient[ch] * 1.01, 0.95 * ambient[ch] + 0.05 * r)   # tracks down fast, up slowly
            peak0 = max(peak0, int(abs(ears.last[:, 0].astype(int)).max())); peak1 = max(peak1, int(abs(ears.last[:, 1].astype(int)).max()))
            if frames % 375 == 0:                                   # every 30 s: proof of life for the microphone
                log.info("mic 30s peak ch0=%d ch1=%d (silence is <100, speech nearby >2000)", peak0, peak1)
                peak0 = peak1 = 0
            if diag and max(score, o) >= 0.1:
                log.info("scores ch%d=%.2f ch%d=%.2f", channel, score, 1 - channel, o)
            if max(score, o) < wake.threshold:
                continue
            ears.channel = channel if score >= o else 1 - channel
            log.info("wake %.2f on channel %d", max(score, o), ears.channel)
            other.reset()
            wake.reset()
            scr.wake(); web.set_state("quiet")
            if not output.startswith("cast:"):
                audio.chime(out)
            timeout = 6.0
            while True:                                   # one exchange, then follow-ups
                try:
                    clip = audio.record_utterance(ears, start_timeout=timeout, ambient_rms=ambient[ears.channel],
                                                  on_speech=lambda sp: web.set_state("hearing" if sp else "quiet"))
                    if clip is None:
                        break
                    web.set_state("thinking")
                    heard = stt.text(clip)
                    if not heard:
                        break
                    try:
                        answer = brain.reply(heard)
                    except Exception:                     # Claude failed: say so, keep going
                        log.exception("claude failed")
                        answer = "Sorry, I couldn't reach my brain just now."
                    web.set_state("speaking")
                    tts.say(answer)
                    web.set_state("quiet")
                except Exception:                         # recognition or speech failed (network, cast): back to listening
                    log.exception("exchange failed; listening again")
                    break
                wake.reset()
                timeout = follow_up
            web.set_state("idle"); scr.restore()
    finally:
        ears.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
