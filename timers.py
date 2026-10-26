"""Kitchen timers for the voice daemon: "set a timer for ten minutes" counts down on the screen (the overlay
reads state.json), rings on the speaker when it reaches zero, and "stop" silences it. One timer at a time
is enough for a kitchen; a second request replaces the first after saying so."""
from __future__ import annotations

import logging
import threading
import time

import numpy as np

from voice import audio

log = logging.getLogger("voice.timers")
RING_SECONDS = 120          # ring this long at most, then give up quietly


class Timers:
    def __init__(self, output_device: int):
        self.out = output_device
        self.lock = threading.Lock()
        self.end: float | None = None      # monotonic deadline of the running timer
        self.total = 0                     # seconds it was set for
        self.label = ""
        self.ringing = False
        self.rang_at: float | None = None
        threading.Thread(target=self._loop, daemon=True, name="timers").start()

    # ---- state for the screen --------------------------------------------------------
    def state(self) -> dict:
        with self.lock:
            if self.ringing:
                return {"active": True, "ringing": True, "remaining": 0, "total": self.total, "label": self.label}
            if self.end is None:
                return {"active": False, "ringing": False, "remaining": 0, "total": 0, "label": ""}
            return {"active": True, "ringing": False, "remaining": max(0, int(round(self.end - time.monotonic()))),
                    "total": self.total, "label": self.label}

    @property
    def busy(self) -> bool:
        return self.ringing or self.end is not None

    # ---- commands ---------------------------------------------------------------------
    def start(self, seconds: float, label: str = "") -> str:
        seconds = int(round(seconds))
        if seconds <= 0:
            return "A timer needs a length, like five minutes."
        with self.lock:
            replaced = self.end is not None or self.ringing
            self.end, self.total, self.label, self.ringing = time.monotonic() + seconds, seconds, (label or "").strip(), False
        return ("Replaced the running timer. " if replaced else "") + f"Timer set for {_say(seconds)}" + (f" for the {label}" if label else "") + "."

    def stop(self) -> str:
        """'Stop': silence a ringing timer, or cancel a running one."""
        with self.lock:
            if self.ringing:
                self.ringing = False; self.end = None
                return "Timer stopped."
            if self.end is not None:
                left = int(round(self.end - time.monotonic())); self.end = None
                return f"Timer cancelled with {_say(left)} left."
        return "No timer is running."

    def status(self) -> str:
        st = self.state()
        if st["ringing"]:
            return "The timer is going off."
        if not st["active"]:
            return "No timer is running."
        return f"{_say(st['remaining'])} left" + (f" on the {st['label']} timer" if st["label"] else "") + "."

    def command(self, body: dict) -> str:
        action = (body.get("action") or "status").strip().lower()
        if action == "start":
            secs = float(body.get("seconds") or 0) + 60 * float(body.get("minutes") or 0) + 3600 * float(body.get("hours") or 0)
            return self.start(secs, body.get("label") or "")
        if action in ("stop", "cancel", "dismiss"):
            return self.stop()
        return self.status()

    # ---- the clock and the bell -------------------------------------------------------
    def _loop(self) -> None:
        while True:
            time.sleep(0.25)
            with self.lock:
                due = self.end is not None and not self.ringing and time.monotonic() >= self.end
                if due:
                    self.ringing, self.rang_at = True, time.monotonic()
                    log.info("timer done: %s", self.label or _say(self.total))
            if due:
                threading.Thread(target=self._ring, daemon=True).start()

    def _ring(self) -> None:
        import sounddevice as sd
        audio.set_volume()
        rate = audio.RATE
        n = int(rate * 0.12)
        beep = np.concatenate([np.sin(2 * np.pi * 880 * np.arange(n) / rate) * np.hanning(n),
                               np.zeros(int(rate * 0.08)),
                               np.sin(2 * np.pi * 1320 * np.arange(n) / rate) * np.hanning(n),
                               np.zeros(int(rate * 0.08)),
                               np.sin(2 * np.pi * 1760 * np.arange(n) / rate) * np.hanning(n)])
        beep = (0.6 * beep * 32767).astype(np.int16)
        started = time.monotonic()
        while True:
            with self.lock:
                if not self.ringing:
                    return
                if time.monotonic() - started > RING_SECONDS:
                    self.ringing = False; self.end = None
                    log.info("timer: nobody stopped it; gave up ringing")
                    return
            try:
                sd.play(beep, samplerate=rate, device=self.out, blocking=True)
            except Exception as e:
                log.warning("timer ring: %s", e); time.sleep(1)
            time.sleep(0.9)


def _say(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    parts = []
    if h: parts.append(f"{h} hour" + ("s" if h != 1 else ""))
    if m: parts.append(f"{m} minute" + ("s" if m != 1 else ""))
    if s and not h: parts.append(f"{s} second" + ("s" if s != 1 else ""))
    return " ".join(parts) or "0 seconds"
