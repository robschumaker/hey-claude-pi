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
    output = cfg("VOICE_OUTPUT", "respeaker")
    if output.startswith("cast:"):
        # Current kitchen setup: reply through a Google Cast speaker. No echo cancellation that way,
        # so we simply do not listen while it talks. Long-term: a speaker on the ReSpeaker's amplifier.
        tts = cast_out.CastVoice(tts, output.split(":", 1)[1])
        log.info("output: cast to %s", output.split(":", 1)[1])
    follow_up = float(cfg("VOICE_FOLLOW_UP_SECONDS", "6"))
    channel = int(cfg("VOICE_CHANNEL", "0"))
    ears = audio.Ears(inp, channel=channel)
    diag = cfg("VOICE_DIAG", "0") == "1"           # score the other channel too, log near misses
    other = audio.WakeWord(wake_path, wake.threshold) if diag else None
    log.info("listening for %s on channel %d%s", wake.name, channel, " (diagnostic: scoring both channels)" if diag else "")
    try:
        while True:
            score = wake.heard(ears.frame())
            if diag:
                o = other.heard(ears.last[:, 1 - channel])
                if max(score, o) >= 0.1:
                    log.info("scores ch%d=%.2f ch%d=%.2f", channel, score, 1 - channel, o)
            if score < wake.threshold:
                continue
            log.info("wake %.2f", score)
            wake.reset()
            if not output.startswith("cast:"):
                audio.chime(out)
            timeout = 6.0
            while True:                                   # one exchange, then follow-ups
                clip = audio.record_utterance(ears, start_timeout=timeout)
                if clip is None:
                    break
                heard = stt.text(clip)
                if not heard:
                    break
                try:
                    answer = brain.reply(heard)
                except Exception as e:                    # network, API, anything: say so, keep listening
                    log.exception("claude failed")
                    answer = "Sorry, I couldn't reach my brain just now."
                tts.say(answer)
                wake.reset()
                timeout = follow_up
    finally:
        ears.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
