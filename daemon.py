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

import numpy as np  # noqa: E402
from voice import audio  # noqa: E402
from voice.brain import Brain  # noqa: E402
from voice.brain_cc import ClaudeCodeBrain  # noqa: E402
from voice import hosted  # noqa: E402
from voice import cast as cast_out  # noqa: E402
from voice import web, screen as screen_mod  # noqa: E402
from voice import ntfy  # noqa: E402
from voice import music as music_mod  # noqa: E402


def cfg(name: str, default: str) -> str:
    return os.environ.get(name, default)


def main() -> int:
    inp, out = audio.find_device(cfg("VOICE_DEVICE", "XVF3800"))
    log.info("audio in=%d out=%d", inp, out)
    audio.open_softvol(cfg("VOICE_OUTPUT_PCM", "voice"))
    audio.set_volume()                                 # the array forgets its level on every reset
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
        brain = ClaudeCodeBrain(model=cfg("VOICE_CC_MODEL", "") or None,
                                session_hours=float(cfg("VOICE_SESSION_HOURS", "24")))
    web.start()                                        # state for the dashboard overlay, media for casting
    if hasattr(brain, "new_conversation"):
        ntfy.start_if_configured(brain)                # text commands from the owner's phone, if set up
    music = None
    if cfg("PANDORA_USER", "") and cfg("PANDORA_PASSWORD", ""):
        music = music_mod.Pianobar(cfg("PANDORA_USER", ""), cfg("PANDORA_PASSWORD", ""), int(cfg("VOICE_MUSIC_LEVEL", "8")))
        web.HANDLERS["music"] = lambda body: music.command(body.get("action", "status"), body.get("station"))
        web.EXTRA["music"] = music.state
        log.info("music: Pandora through pianobar, on the speaker; paused during exchanges")
    scr = screen_mod.Screen()
    output = cfg("VOICE_OUTPUT", "respeaker")
    if output.startswith("cast:"):
        # Fallback when no speaker is on the array: reply through a Google Cast device. No echo
        # cancellation or wake chime that way, so we simply do not listen while it talks.
        tts = cast_out.CastVoice(tts, output.split(":", 1)[1])
        log.info("output: cast to %s", output.split(":", 1)[1])
    follow_up = float(cfg("VOICE_FOLLOW_UP_SECONDS", "6"))
    channel = int(cfg("VOICE_CHANNEL", "0"))
    ears = audio.Ears(inp, channel=channel)
    diag = cfg("VOICE_DIAG", "0") == "1"           # log near misses
    # Scoring the other channel too doubles the model cost (33 ms of an 80 ms frame on a Pi 4) and, measured
    # over 2026-10-24/25, produced half the false wakes and none of the real ones. Off unless asked.
    both = cfg("VOICE_SCORE_BOTH", "0") == "1"
    other = audio.WakeWord(wake_path, wake.threshold) if both else None
    need = int(cfg("VOICE_WAKE_FRAMES", "2"))      # consecutive frames over threshold: one-frame spikes are noise
    music_thr = float(cfg("VOICE_WAKE_THRESHOLD_MUSIC", "0.35"))   # music drowns the wake word; listen harder
    # Measured 2026-10-25: channel 0 is the array's echo-cancelled output (speaker noise at full level leaves it at
    # its silent floor), channel 1 is nearly raw. Channel 1 scores the wake word better in a quiet room, so it is
    # the default; while music plays we listen and record on the cancelled channel instead.
    music_channel = int(cfg("VOICE_CHANNEL_MUSIC", "0"))
    log.info("listening for %s on channel %d%s, %d frames over %.2f", wake.name, channel,
             " (both channels scored)" if both else "", need, wake.threshold)
    run = 0
    heartbeat = time.monotonic()
    peak = 0.0
    try:
        while True:
            over_music = bool(music and music.playing)
            ears.channel = music_channel if over_music else channel
            f = ears.frame()
            if ears.lost():                           # discontinuity: the model's context is garbage now
                wake.reset(); run = 0
                if other: other.reset()
            peak = max(peak, float(np.abs(f).mean()))
            if time.monotonic() - heartbeat > 300:
                log.info("heartbeat: peak %.0f, backlog %d frames", peak, ears.backlog)
                heartbeat = time.monotonic(); peak = 0.0
            score = wake.heard(f)
            o = other.heard(ears.last[:, 1 - channel]) if other else 0.0
            if diag and max(score, o) >= 0.1:
                log.info("scores ch%d=%.2f ch%d=%.2f", channel, score, 1 - channel, o)
            run = run + 1 if max(score, o) >= (music_thr if over_music else wake.threshold) else 0
            if run < (1 if over_music else need):                 # over music: one frame, lower bar
                continue
            run = 0
            if not over_music:
                ears.channel = channel if score >= o else 1 - channel
            log.info("wake %.2f on channel %d%s", max(score, o), ears.channel, " over music" if over_music else "")
            if other: other.reset()
            wake.reset()
            scr.wake(); web.set_state("quiet")
            if music: music.duck()                        # quiet for the exchange, resumed at its end
            if not output.startswith("cast:"):
                audio.chime(out)
            timeout = 6.0
            while True:                                   # one exchange, then follow-ups
                try:
                    clip = audio.record_utterance(ears, start_timeout=timeout,
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
                    ears.drain()                          # our own voice, captured meanwhile
                    web.set_state("quiet")
                except Exception:                         # recognition or speech failed (network, cast): back to listening
                    log.exception("exchange failed; listening again")
                    break
                wake.reset()
                timeout = follow_up
            web.set_state("idle"); scr.restore(); ears.drain()
            if music: music.unduck()
    finally:
        ears.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
