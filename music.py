"""Pandora through pianobar, owned by the voice daemon: it starts pianobar as a child, feeds it
keystrokes on stdin, reads its output for the station list and the current song, and pauses it for the
length of every exchange (duck on wake, resume when the exchange ends). Audio goes to the ALSA device
"array", a dmix on the ReSpeaker's speaker shared with the daemon's own playback (see /etc/asound.conf).
Needs: apt install pianobar; ~/.libao with default_driver=alsa and dev=array; PANDORA_USER and
PANDORA_PASSWORD in the env file. The password is written to pianobar's own config file, mode 600.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time

log = logging.getLogger("voice.music")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\r")
STATION = re.compile(r"^\s*(\d+)\)\s+(?:[qQS]\s+)?(.+?)\s*$")
NOWPLAYING = re.compile(r'^\|>\s+"(.+?)" by "(.+?)" on "(.+?)"')
ONSTATION = re.compile(r"^\|>\s+Station \"(.+?)\"")


class Pianobar:
    def __init__(self, user: str, password: str, volume_db: int = -6, bin_path: str = "pianobar"):
        self.user, self.password, self.volume_db, self.bin = user, password, volume_db, bin_path
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.stations: list[str] = []
        self.stations_ready = threading.Event()
        self.song: dict = {}
        self.playing = False            # a station is running and not paused
        self.ducked = False             # paused by us for an exchange
        self.last_station: str | None = None

    # ---- process ------------------------------------------------------------------------
    def _config(self) -> None:
        d = os.path.expanduser("~/.config/pianobar")
        os.makedirs(d, exist_ok=True)
        cfg = os.path.join(d, "config")
        with open(cfg, "w") as f:
            f.write(f"user = {self.user}\npassword = {self.password}\naudio_quality = high\nvolume = {self.volume_db}\n"
                    "format_nowplaying_song = |> \"%t\" by \"%a\" on \"%l\"\nformat_nowplaying_station = |> Station \"%n\"\n")
        os.chmod(cfg, 0o600)

    def _start(self) -> None:
        self._config()
        self.stations, self.song = [], {}
        self.stations_ready.clear()
        self.proc = subprocess.Popen([self.bin], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, bufsize=1)
        threading.Thread(target=self._reader, daemon=True, name="pianobar-out").start()
        log.info("pianobar started pid %d", self.proc.pid)

    def _reader(self) -> None:
        proc = self.proc
        for raw in proc.stdout:
            line = ANSI.sub("", raw).strip()
            if not line:
                continue
            m = STATION.match(line)
            if m:
                i, name = int(m.group(1)), m.group(2)
                if i == len(self.stations):
                    self.stations.append(name)
                continue
            if "Select station" in line:
                self.stations_ready.set()
                continue
            m = NOWPLAYING.match(line)
            if m:
                self.song = {"title": m.group(1), "artist": m.group(2), "album": m.group(3), "station": self.last_station}
                self.playing = True
                log.info("pianobar: %s by %s", m.group(1), m.group(2))
                continue
            m = ONSTATION.match(line)
            if m:
                self.last_station = m.group(1)
                continue
            if "Error" in line or "Login" in line or "(i)" in line:
                log.info("pianobar: %s", line[:120])
        self.playing = False
        log.info("pianobar exited")

    def _send(self, keys: str) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.stdin.write(keys)
            self.proc.stdin.flush()

    def _alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # ---- commands -----------------------------------------------------------------------
    def play(self, station: str | None = None) -> str:
        with self.lock:
            if not self._alive():
                self._start()
            elif station is None:
                self._send("P")
                self.playing = True; self.ducked = False
                return self.status()
            else:
                self.stations, self.stations_ready = [], threading.Event()
                self._send("s")
            if not self.stations_ready.wait(timeout=25):
                self._send("\n")
                return "Pandora did not answer with the station list; check the login."
            if not self.stations:
                return "No stations on this account."
            want = (station or "").strip().lower()
            idx = 0
            if want:
                hits = [i for i, n in enumerate(self.stations) if want in n.lower()]
                if not hits:
                    self._send("\n")   # leave the prompt as it was
                    return "No station called " + station + ". Stations: " + ", ".join(self.stations[:12]) + "."
                idx = hits[0]
            self._send(f"{idx}\n")
            self.last_station = self.stations[idx]
            self.playing = True; self.ducked = False
            return f"Playing {self.stations[idx]}."

    def pause(self) -> str:
        with self.lock:
            self._send("S"); self.playing = False; self.ducked = False
            return "Paused."

    def stop(self) -> str:
        with self.lock:
            self._send("q"); self.playing = False; self.ducked = False
            try:
                if self.proc: self.proc.wait(timeout=5)
            except Exception:
                if self.proc: self.proc.kill()
            self.proc = None
            return "Stopped."

    def next(self) -> str:
        with self.lock:
            self._send("n"); return "Skipping."

    def love(self) -> str:
        with self.lock:
            self._send("+"); return "Thumbs up."

    def ban(self) -> str:
        with self.lock:
            self._send("-"); return "Thumbs down, skipping."

    def status(self) -> str:
        if not self._alive():
            return "Music is off."
        if self.song and self.playing:
            return f"Playing {self.song['title']} by {self.song['artist']} on {self.song.get('station') or 'Pandora'}."
        if self.song:
            return f"Paused on {self.song.get('station') or 'Pandora'}: {self.song['title']} by {self.song['artist']}."
        return "Pandora is starting."

    def list_stations(self) -> str:
        if not self._alive():
            with self.lock:
                self._start()
                if not self.stations_ready.wait(timeout=25):
                    return "Pandora did not answer; check the login."
                self._send("\n")
        return "Stations: " + ", ".join(self.stations) + "." if self.stations else "No stations."

    # ---- the exchange -------------------------------------------------------------------
    def duck(self) -> None:
        with self.lock:
            if self._alive() and self.playing:
                self._send("S"); self.playing = False; self.ducked = True

    def unduck(self) -> None:
        with self.lock:
            if self._alive() and self.ducked:
                self._send("P"); self.playing = True; self.ducked = False

    def command(self, action: str, station: str | None = None) -> str:
        action = (action or "status").strip().lower()
        table = {"play": lambda: self.play(station), "resume": lambda: self.play(None) if self._alive() else self.play(station),
                 "pause": self.pause, "stop": self.stop, "next": self.next, "skip": self.next, "love": self.love,
                 "like": self.love, "ban": self.ban, "dislike": self.ban, "status": self.status, "stations": self.list_stations}
        if action not in table:
            return "Unknown music action " + action + "."
        t = time.monotonic()
        out = table[action]()
        log.info("music %s %s -> %r (%.1fs)", action, station or "", out, time.monotonic() - t)
        return out
