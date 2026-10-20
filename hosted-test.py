"""Round-trip test of Deepgram from the Pi: Piper says a sentence, Deepgram transcribes it, then
Deepgram speaks the reply and we time it. No microphone or speaker needed.
Usage: ENV_FILE=/home/pi/household/.env venv-voice/bin/python voice/hosted-test.py"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from auth import load_env
load_env(os.environ.get("ENV_FILE", "/home/pi/household/.env"))
import logging; logging.basicConfig(level=logging.INFO, format="%(message)s")
from voice import audio, hosted
key = hosted.available()
if not key:
    print("DEEPGRAM_API_KEY not set"); sys.exit(1)
from piper import PiperVoice
v = audio.Voice.__new__(audio.Voice); v.voice = PiperVoice.load("/home/pi/voice-models/en_US-lessac-medium.onnx")
clip = v.render("Hey Claude, add two cartons of oat milk to the shopping list.")
print("heard:", hosted.DeepgramTranscriber(key).text(clip))
t = time.monotonic(); a = hosted.DeepgramVoice(key, 0).render("Done. Two cartons of oat milk are on the list.")
print(f"tts {time.monotonic()-t:.1f}s -> {len(a)/16000:.1f}s of audio")
