#!/bin/bash
# Run the voice benchmark at the daemon's priority. Needs root for the negative nice: sudo ./voice/bench-prio.sh
cd /home/pi/household-mcp || exit 1
exec nice -n -10 venv-voice/bin/python voice/bench.py /home/pi/voice-models/en_US-lessac-medium.onnx tiny.en base.en 2>&1 | grep -E "^(piper|tiny|base|small)"
