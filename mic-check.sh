#!/bin/bash
# ExecStartPre for household-voice.service: make sure the ReSpeaker is actually streaming audio.
# After hard power events the XVF3800's audio pipeline can wedge while its control interface still
# answers (symptom: arecord gets Input/output error, kernel floods retire_capture_urb). A firmware
# reboot through the control interface fixes it. Also re-applies the dim LED ring.
XVF=${VOICE_XVF_HOST:-/home/pi/downloads-untrusted/xvf/host_control/rpi_64bit/xvf_host}
card=$(arecord -l 2>/dev/null | sed -nE 's/^card ([0-9]+): Array .*/\1/p' | head -1)   # /proc/asound is empty inside an HAOS add-on
[ -n "$card" ] || { echo "mic-check: no ReSpeaker card"; exit 0; }
ok() { timeout 6 arecord -D hw:$card,0 -f S16_LE -r 16000 -c 2 -d 1 /dev/null >/dev/null 2>&1; }
for attempt in 1 2 3; do
  if ok; then echo "mic-check: array streaming (card $card)"; break; fi
  echo "mic-check: array not streaming; rebooting its firmware (attempt $attempt)"
  [ -x "$XVF" ] && "$XVF" REBOOT 1 >/dev/null 2>&1
  sleep 8
done
[ -x "$XVF" ] && "$XVF" LED_BRIGHTNESS 10 >/dev/null 2>&1
exit 0
