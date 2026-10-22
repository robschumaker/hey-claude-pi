"""The kitchen screen (official DSI touchscreen): wake the backlight for an exchange and put it
back the way it was. The PIR controller owns it otherwise; we only touch it around a wake."""
from __future__ import annotations

import logging
import os

log = logging.getLogger("voice.screen")
BACKLIGHT = os.environ.get("VOICE_BACKLIGHT", "/sys/class/backlight/10-0045/brightness")


class Screen:
    def __init__(self):
        self.was = None

    def _read(self) -> int | None:
        try:
            return int(open(BACKLIGHT).read().strip())
        except OSError:
            return None

    def _write(self, v: int) -> None:
        try:
            with open(BACKLIGHT, "w") as f:
                f.write(str(v))
        except OSError as e:
            log.warning("backlight: %s", e)

    def wake(self) -> None:
        self.was = self._read()
        if self.was == 0:
            self._write(255)
            log.info("screen on for the exchange")

    def restore(self) -> None:
        if self.was == 0 and self._read() == 255:
            self._write(0)
            log.info("screen back off")
        self.was = None
