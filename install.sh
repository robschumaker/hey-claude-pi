#!/bin/bash
# Voice assistant install on the Pi, run by the parent repo's install.sh after its rsync, or by hand:
# ./install.sh [pi@raspberrypi] [/home/pi/household-mcp]. Builds venv-voice and installs the unit.
set -euo pipefail
PI=${1:-pi@raspberrypi}
DEST=${2:-/home/pi/household-mcp}
echo "== voice venv (openwakeword pinned without tflite-runtime, which has no Python 3.13 wheels)"
ssh "$PI" "cd $DEST && ( [ -x venv-voice/bin/python ] || python3 -m venv venv-voice ) && venv-voice/bin/pip install -q -r voice/requirements.txt && venv-voice/bin/pip install -q --no-deps openwakeword==0.6.0 && mkdir -p /home/pi/voice-models"
echo "== voice unit"
ssh "$PI" "sudo install -m 644 $DEST/voice/systemd/household-voice.service /etc/systemd/system/ && sudo systemctl daemon-reload"
echo "Voice: needs libportaudio2 (apt), models in /home/pi/voice-models, Claude Code logged in as pi, DEEPGRAM_API_KEY and VOICE_* in the env file; then: sudo systemctl enable --now household-voice.service"
