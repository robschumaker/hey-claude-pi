# Hey Claude on a Raspberry Pi

A "Hey Claude" voice assistant for the house: a ReSpeaker XVF3800 USB microphone array on a Raspberry Pi 4, a local wake word, hosted speech recognition and synthesis, and Claude as the brain, running as Claude Code in headless mode on a Claude subscription, with the household's own tools attached as an MCP server. A kiosk display shows a Claude logo that breathes while it listens, spins while it thinks and glows while it talks.

Measured on a Pi 4 that is also driving a kiosk: a plain answer in 3 to 4 seconds from the end of the question, a tool answer in 4 to 6, a web-searched answer in 10 to 20.

## How it works

```
ReSpeaker XVF3800 (USB, 2 channels, 16 kHz)
   └─ audio.Ears ── openWakeWord "hey_claude" on both channels (local, ~80 ms frames)
         └─ wake ── audio.chime: rising two-tone on the speaker, then
            audio.record_utterance: Silero VAD end-pointing, 0.5 s pre-roll
               └─ hosted.DeepgramTranscriber: Nova-3 speech to text (~1 s)
                     └─ brain_cc.ClaudeCodeBrain: one persistent `claude -p --input-format stream-json`
                        process, tools = the MCP servers in mcp.json + WebSearch + WebFetch
                           └─ hosted.DeepgramVoice (Aura-2) ── normalised and played on the array's
                              speaker, or rendered and cast to a Google display (cast.py)
web.py: state endpoint + overlay script for the kiosk; screen.py: wakes the display for the exchange
mic-check.sh: ExecStartPre that reboots the array's firmware if its audio pipeline is wedged
```

Everything lives in one process (`daemon.py`) and one systemd unit. The daemon logs a microphone peak-level heartbeat and every recorder outcome so a silent failure explains itself.

## Hardware

- Raspberry Pi 4 (a Pi 5 would be more comfortable; local Whisper and Piper were tried on the Pi 4 and are too slow to talk to, which is why speech is hosted and only the wake word is local).
- Seeed ReSpeaker XVF3800 USB 4-mic array. It does echo cancellation in firmware when the speaker is on its own amplifier output, so the assistant can hear over its own voice. Channel 1 of its USB stream scores the wake word far better than channel 0; the daemon scores both and records from the one that woke.
- A speaker on the array's JST amplifier output or 3.5 mm jack (`VOICE_OUTPUT=respeaker`, the default). Without one, replies can be cast to a Google Nest display (`VOICE_OUTPUT=cast:<device name>`), at the cost of a few seconds, no wake chime and not listening while it talks.
- Optional: a display running a kiosk web page, which gets the state overlay by one script tag.

## Setup

Layout: the code is a Python package named `voice`, so clone this repo into a directory called `voice` under a parent, for example `/home/pi/household-mcp/voice`. The unit, `install.sh` and the commands below assume that parent and run from it (`venv-voice/bin/python voice/daemon.py`). Change the paths in `systemd/household-voice.service` if yours differ.

1. In the parent directory, a Python 3.13 venv: `python3 -m venv venv-voice && venv-voice/bin/pip install -r voice/requirements.txt`. openWakeWord is pinned `--no-deps` because tflite-runtime has no Python 3.13 wheels; ONNX is used instead. `sudo apt install libportaudio2 alsa-utils` (the unit uses `amixer`). `install.sh` does the venv and the unit over SSH.
2. Models in `/home/pi/voice-models/`: `hey_claude.onnx` (openWakeWord model from huggingface.co/gdiamos/hey-claude, Apache-2.0). Piper voices are only needed for the local fallback.
3. `scripts/respeaker-udev.sh` for control access to the array (LED brightness, firmware reboot through Seeed's `xvf_host`).
4. Claude Code installed on the Pi and logged in as the service user (`claude` on the PATH of that user). No API key is needed; `brain.py` is an alternative brain that uses the Anthropic API if `ANTHROPIC_API_KEY` is set.
5. A Deepgram API key for speech in both directions.
6. Copy `.env.example` to your env file, set `ENV_FILE` in the unit, `sudo install -m 644 systemd/household-voice.service /etc/systemd/system/`, `sudo systemctl enable --now household-voice.service`.
7. Kiosk overlay: add the loader from `web/README-overlay.md` to the kiosk page; it retries until the daemon's web server is up, since the kiosk starts first at boot.

## Sound

- On the wake word the speaker plays a short rising two-tone and recording starts the moment it ends. That is the cue to talk.
- The service sets the array's amplifier to full volume at start (`amixer -D hw:Array sset PCM 100%`). On the XVF3800 the control called `Headset` is the microphone gain, not the speaker; leave it alone.
- Deepgram's renders peak at about a third of full scale, so replies are normalised before playback. `VOICE_GAIN` in `.env` scales further.
- Voice: `VOICE_DEEPGRAM_TTS`. `voice/audition.py` fetches Deepgram's current Aura list (`--list`) and speaks a sample from each name you give it through the speaker, so you can choose by ear.

## Tools

`mcp.json` lists the MCP servers the brain gets. The example attaches `store_mcp.py`, the household's own memory and lists server (shared memory, shopping list, notes between the family's assistants, and the weather at the house from Open-Meteo). Replace it with whatever servers your house has; the brain's prompt in `brain.py` describes the tools in plain words for speech. Bash, file editing and file reading are disallowed for the voice brain on purpose.

## Lessons that cost time

- Never cap or de-prioritise the daemon's service. Its Claude Code child restarts after idling, and under a CPU quota the warm-up starves the audio reader: input overflows, chopped recordings. The unit runs at Nice -10 with a high CPU weight.
- Loudness is not speech. An energy gate either chops a quiet speaker to a syllable or never starts. Silero VAD, shipped with openWakeWord, costs 10 ms per 80 ms frame and only runs while recording.
- After a hard power cut the XVF3800 can enumerate and answer its control interface while its audio pipeline is wedged (capture stream "running", hardware pointer stuck, `arecord` gives I/O errors). A firmware reboot through `xvf_host REBOOT 1` fixes it; `mic-check.sh` does that before every start and the daemon exits if no frames arrive for 20 s so systemd re-runs the check.
- Never change the Pi's Wi-Fi connection from a session running over it.
- A kiosk page's one-shot `<script src>` for the overlay loses the boot race against the daemon and never retries. Use a loader that retries.

## Status

Working in one house since October 2026, replies through a speaker on the array's amplifier since 24 October. Known limits: the published wake-word model misses some non-US accents and false-wakes on near-homophones; a wake during the Claude Code warm-up burst can lose frames.

## License

MIT; see `LICENSE`. The hey_claude wake-word model and openWakeWord are Apache-2.0.
