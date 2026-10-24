"""Audition Deepgram Aura voices through the ReSpeaker: each candidate introduces itself.
    venv-voice/bin/python voice/audition.py [voice ...]      (names like asteria, orion; default: a spread)
    venv-voice/bin/python voice/audition.py --list            (every aura-2 English voice Deepgram offers)
Reads DEEPGRAM_API_KEY from the store's .env like the daemon does."""
import json, os, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from voice import env, hosted  # noqa: E402

env.load_env("/home/pi/household/.env")
key = hosted.available() or sys.exit("no DEEPGRAM_API_KEY")
DEFAULT = ["asteria", "luna", "athena", "helena", "orion", "arcas", "apollo", "zeus"]
SAMPLE = "Hi, I'm {name}. The kitchen is seventy two degrees and the shopping list has four items. Shall I add milk?"


def all_voices():
    req = urllib.request.Request("https://api.deepgram.com/v1/models", headers={"Authorization": f"Token {key}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = json.load(r)
    return sorted(m["canonical_name"] for m in body.get("tts", []) if m["canonical_name"].startswith("aura-2-") and m["canonical_name"].endswith("-en"))


if "--list" in sys.argv:
    print("\n".join(all_voices())); sys.exit()
import sounddevice as sd  # noqa: E402
out = next(i for i, d in enumerate(sd.query_devices()) if "xvf3800" in d["name"].lower() and d["max_output_channels"] > 0)
names = [a for a in sys.argv[1:] if not a.startswith("-")] or DEFAULT
for n in names:
    model = n if n.startswith("aura-") else f"aura-2-{n}-en"
    v = hosted.DeepgramVoice(key, out, model)
    print(model, flush=True)
    try:
        v.say(SAMPLE.format(name=n.capitalize()))
    except Exception as e:
        print("  failed:", e)
    time.sleep(0.6)
