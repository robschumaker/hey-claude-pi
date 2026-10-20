"""Benchmark the voice pipeline on the Pi without a microphone: Piper synthesises a test
sentence, then each whisper model transcribes it. Prints seconds per stage.
Usage: venv-voice/bin/python voice/bench.py /home/pi/voice-models/en_US-lessac-medium.onnx base.en small.en
"""
import sys, time, os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from voice import audio

SENTENCE = "Hey Claude, add two cartons of oat milk to the shopping list and remind me the gutters need cleaning before the rains."

voice_path, models = sys.argv[1], sys.argv[2:]
t = time.monotonic()
v = audio.Voice.__new__(audio.Voice)
from piper import PiperVoice
v.voice = PiperVoice.load(voice_path)
print(f"piper load  {time.monotonic()-t:5.1f}s")
t = time.monotonic(); clip = v.render(SENTENCE)
print(f"piper tts   {time.monotonic()-t:5.1f}s for {len(clip)/16000:.1f}s of audio")
for m in models:
    stt = audio.Transcriber(m)
    for i in range(2):
        t = time.monotonic(); text = stt.text(clip); dt_ = time.monotonic() - t
    print(f"{m:10} {dt_:5.1f}s  -> {text}")
