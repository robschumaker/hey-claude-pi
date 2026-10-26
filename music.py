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
NOWPLAYING = re.compile(r'\|>\s+"(.+?)" by "(.+?)" on "(.+?)"\s*(\[LOVED\]|\[BANNED\]|\[TIRED\])?')
SONGITEM = re.compile(r'^\s*(\d+)\)\s+"(.+?)" by "(.+?)"\s*(\[LOVED\]|\[BANNED\]|\[TIRED\])?\s*$')
ONSTATION = re.compile(r"\|>\s+Station \"(.+?)\"")


class Pianobar:
    LEVEL_DB = {n: -21 + 3 * (n - 1) for n in range(1, 11)}      # 1 -> -21 dB, 8 -> 0 dB, 10 -> +6 dB (Pandora streams sit ~10 dB under full scale)
    CONTROL = "Pandora"                                           # the softvol control in /etc/asound.conf

    def __init__(self, user: str, password: str, level: int = 8, bin_path: str = "pianobar"):
        self.user, self.password, self.bin = user, password, bin_path
        self.level = max(1, min(10, int(level)))
        self.volume_db = 0                                     # pianobar's own gain stays at 0: level lives on the Music softvol
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.stations: list[str] = []
        self.stations_ready = threading.Event()      # the "[?] Select station:" prompt is open
        self.started = threading.Event()             # either the prompt is open or a song is playing
        self.song: dict = {}
        self.playing = False            # a station is running and not paused
        self.ducked = False             # paused by us for an exchange
        self.last_station: str | None = None
        self.level_pending = False
        self.menu_items: list = []                   # numbered song list while a "Select song:" prompt is open
        self.menu_prompt = threading.Event()         # the "Delete ... feedback?" question is open
        self.song_prompt = threading.Event()         # the "Select song:" prompt is open
        self.feedback_done = threading.Event()       # pianobar answered "Deleting feedback... Ok." (or an error)
        self.loved_ok = threading.Event()            # pianobar answered "Loving song... Ok."
        self.on_old_love = None                      # callback(title, artist): a loved song outside the newest 200 started

    # ---- process ------------------------------------------------------------------------
    def _config(self) -> None:
        d = os.path.expanduser("~/.config/pianobar")
        os.makedirs(d, exist_ok=True)
        cfg = os.path.join(d, "config")
        with open(cfg, "w") as f:
            f.write(f"user = {self.user}\npassword = {self.password}\naudio_quality = high\nvolume = {self.volume_db}\n"
                    "format_nowplaying_song = |> \"%t\" by \"%a\" on \"%l\"%r\nformat_nowplaying_station = |> Station \"%n\"\n"
                    "love_icon = [LOVED]\nban_icon = [BANNED]\ntired_icon = [TIRED]\nformat_list_song = %i) \"%t\" by \"%a\"%r\n")
        os.chmod(cfg, 0o600)

    def _start(self) -> None:
        self._config()
        self.stations, self.song = [], {}
        self.stations_ready.clear(); self.started.clear()
        self.proc = subprocess.Popen([self.bin], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     bufsize=0)
        threading.Thread(target=self._reader, daemon=True, name="pianobar-out").start()
        log.info("pianobar started pid %d", self.proc.pid)
        self.level_pending = True      # the Music control appears once pianobar opens the device; apply then

    def _reader(self) -> None:
        """pianobar's prompt "[?] Select station:" has no newline, so read in chunks, not lines."""
        proc = self.proc
        fd = proc.stdout.fileno()
        buf = ""
        while True:
            chunk = os.read(fd, 4096).decode("utf-8", "replace")
            if not chunk:
                break
            buf += chunk
            while "\n" in buf or "\r" in buf:
                i = min(j for j in (buf.find("\n"), buf.find("\r")) if j >= 0)
                line, buf = buf[:i], buf[i + 1:]
                self._line(ANSI.sub("", line).strip())
            tail = ANSI.sub("", buf)
            if "Select station" in tail:
                self.stations_ready.set(); self.started.set()
                buf = ""
            elif "Select song" in tail:
                self.song_prompt.set(); buf = ""
            elif "eedback" in tail and "?" in tail:           # "[?] Delete [s]ong seeds or [f]eedback? Manage [m]ode? " (note the [f])
                self.menu_prompt.set(); buf = ""
            elif tail.strip() and len(tail) < 200:
                log.info("pianobar prompt? %r", tail[-120:])
        self.playing = False
        log.info("pianobar exited")

    def _line(self, line: str) -> None:
        if True:
            if not line:
                return
            m = SONGITEM.match(line)
            if m:
                self.menu_items.append({"index": int(m.group(1)), "title": m.group(2), "artist": m.group(3),
                                        "rating": (m.group(4) or "").strip("[]").lower()})
                return
            m = STATION.match(line)
            if m:
                i, name = int(m.group(1)), m.group(2)
                if i == len(self.stations):
                    self.stations.append(name)
                return
            if "Loving song" in line and "Ok" in line:
                self.song["loved"] = True; self.loved_ok.set(); return
            if "Deleting feedback" in line:
                if "Ok" in line:
                    self.song["loved"] = False
                self.feedback_done.set(); return
            if "Select station" in line:
                self.stations_ready.set(); self.started.set()
                return
            m = NOWPLAYING.search(line)
            if m:
                self.song = {"title": m.group(1), "artist": m.group(2), "album": m.group(3), "station": self.last_station,
                             "loved": (m.group(4) or "") == "[LOVED]"}
                if self.song["loved"] and self.on_old_love:
                    threading.Thread(target=self._check_old_love, args=(m.group(1), m.group(2)), daemon=True).start()
                self.playing = True; self.started.set()
                log.info("pianobar: %s by %s%s", m.group(1), m.group(2), " " + m.group(4) if m.group(4) else "")
                if self.level_pending:                       # pianobar has opened the device: the control exists now
                    self.level_pending = False
                    self._apply_level()
                return
            m = ONSTATION.search(line)
            if m:
                self.last_station = m.group(1)
                return
            if "Error" in line and ("playlist" in line or "Access denied" in line):
                # Pandora refused to hand out songs (skip/rate limit, outage): nothing is playing, say so
                self.playing = False; self.song = {}
                log.warning("pianobar: Pandora refused the playlist (%s); music marked as stopped", line[:80])
                return
            if "Error" in line or "Login" in line or "(i)" in line:
                log.info("pianobar: %s", line[:120])

    def _send(self, keys: str) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.stdin.write(keys.encode())
            self.proc.stdin.flush()

    def _alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # ---- commands -----------------------------------------------------------------------
    def play(self, station: str | None = None) -> str:
        """Start or resume. pianobar either opens its station prompt or (sometimes) starts a station by itself."""
        with self.lock:
            fresh = not self._alive()
            if fresh:
                self._start()
                if not self.started.wait(timeout=25):
                    return "Pandora did not answer; check the login."
            if station is None:
                if self.stations_ready.is_set() and not self.playing:     # prompt open: take the first station
                    self._pick(0)
                else:
                    self._send("P"); self.playing = True; self.ducked = False
                return self.status()
            if not (self.stations_ready.is_set() and not self.playing):  # no prompt open: ask for the list
                self.stations, self.stations_ready = [], threading.Event()
                self._send("s")
                if not self.stations_ready.wait(timeout=15):
                    return "Pandora did not answer with the station list."
            if not self.stations:
                self._send("\n")
                return "No stations on this account."
            want = station.strip().lower()
            hits = [i for i, n in enumerate(self.stations) if want in n.lower()]
            if not hits:
                self._send("\n")                                         # leave the prompt as it was
                return "No station called " + station + ". Stations: " + ", ".join(self.stations[:12]) + "."
            self._pick(hits[0])
            return f"Playing {self.stations[hits[0]]}."

    def _pick(self, idx: int) -> None:
        self._send(f"{idx}\n")
        self.stations_ready.clear()
        self.last_station = self.stations[idx] if idx < len(self.stations) else self.last_station
        self.playing = True; self.ducked = False

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
            if self.song.get("loved"):
                return self._unlove()
            self._send("+"); self.song["loved"] = True; return "Thumbs up."

    def unlove(self) -> str:
        with self.lock:
            return self._unlove()

    def _unlove(self) -> str:
        """Remove this song's thumbs-up: pianobar has no key for it, but its station menu ('=') can delete
        feedback: answer 'f', then pick the song's number from the list it prints. Pandora returns only the
        newest 200 thumbs, so an older one is loved again first (that makes it the newest) and then deleted.
        Caller holds the lock."""
        title = self.song.get("title")
        if not self._alive() or not title:
            return "Nothing is playing."
        out = self._delete_feedback(title)
        if out is None:                                   # not in the list: refresh it to the top, then delete
            log.info("unlove: %r is older than the newest 200 thumbs; re-loving it to bring it into reach", title)
            self.loved_ok = threading.Event()
            self._send("+")
            if not self.loved_ok.wait(timeout=15):
                return "Pandora did not answer the thumbs-up."
            out = self._delete_feedback(title)
            if out is None:
                return "Pandora did not list this song even after re-rating it; remove it in the Pandora app."
        return out

    def _list_feedback(self):
        """Open the '=' menu and list the station's feedback; leaves the "Select song:" prompt open.
        Returns None if pianobar did not play along (the prompt is closed again in that case)."""
        self.menu_items, self.menu_prompt, self.song_prompt = [], threading.Event(), threading.Event()
        self._send("=")
        if not self.menu_prompt.wait(timeout=15):
            return None
        self._send("f")
        if not self.song_prompt.wait(timeout=15):
            self._send("\n"); return None
        return list(self.menu_items)

    def _check_old_love(self, title: str, artist: str) -> None:
        """Background: a loved song started; see whether its thumbs-up is among the newest 200 the station
        lists. If not, tell the owner (so the screen's removal path can be tried on it)."""
        time.sleep(3)
        with self.lock:
            if self.song.get("title") != title:
                return
            items = self._list_feedback()
            if items is None:
                return
            self._send("\n")                                      # just looking
            norm = lambda t: " ".join((t or "").casefold().split())
            found = any(norm(it["title"]) == norm(title) for it in items)
            self.song["old_love"] = not found
        if not found and self.on_old_love:
            try:
                self.on_old_love(title, artist)
            except Exception as e:
                log.warning("old-love notice failed: %s", e)

    def _delete_feedback(self, title: str):
        """One pass through the '=' menu. Returns a result string, or None if the song was not in the list."""
        if self._list_feedback() is None:
            return "Pandora did not list the feedback."
        norm = lambda t: " ".join((t or "").casefold().split())
        artist = self.song.get("artist")
        hits = [it for it in self.menu_items if norm(it["title"]) == norm(title) and norm(it["artist"]) == norm(artist)] \
            or [it for it in self.menu_items if norm(it["title"]) == norm(title)]
        if not hits:
            import difflib
            near = difflib.get_close_matches(title, [it["title"] for it in self.menu_items], n=3, cutoff=0.5)
            log.warning("unlove: %r not in the %d feedback items; closest %r", title, len(self.menu_items), near)
            self._send("\n")
            return None
        self.feedback_done = threading.Event()
        self._send(f"{hits[0]['index']}\n")
        if not self.feedback_done.wait(timeout=15):
            return "Pandora did not confirm removing the thumbs-up."
        return "Thumbs up removed." if not self.song.get("loved") else "Pandora refused to remove the thumbs-up."

    def ban(self) -> str:
        with self.lock:
            self._send("-"); return "Thumbs down, skipping."

    def volume(self, level) -> str:
        """Music level 1 to 10 (10 loudest); 'up' and 'down' move two steps."""
        with self.lock:
            if isinstance(level, str) and level.strip().lower() in ("up", "louder"):
                level = self.level + 2
            elif isinstance(level, str) and level.strip().lower() in ("down", "quieter", "softer"):
                level = self.level - 2
            try:
                new = max(1, min(10, int(level)))
            except (TypeError, ValueError):
                return "Volume is a number from one to ten."
            self.level = new
            self._apply_level()
            return f"Music volume {new}."

    def _apply_level(self) -> None:
        """Instant and clean: an ALSA softvol ("Music") on the shared mixer, not pianobar's keystroke gain."""
        import subprocess
        try:
            # Raw step, not dB (amixer under HAOS rejects the dB form for this softvol): 0 = -40 dB, 40 = 0 dB, 46 = +6 dB.
            # Ceiling 0 dB (step 40): above that the USB-powered amplifier trips the Pi's USB rail (measured 2026-10-26).
            step = min(40, 40 + self.LEVEL_DB[self.level])
            r = subprocess.run(["amixer", "-q", "-D", "hw:Array", "sset", self.CONTROL, str(step)],
                               timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if r.returncode != 0:
                log.warning("music level not applied (amixer rc %s)", r.returncode)
        except Exception as e:
            log.warning("music level: %s", e)

    def state(self) -> dict:
        """For the kitchen screen."""
        return {"on": self._alive(), "playing": self.playing, "level": self.level, "stations": list(self.stations),
                "title": self.song.get("title"), "artist": self.song.get("artist"), "album": self.song.get("album"),
                "station": self.song.get("station") or self.last_station, "loved": bool(self.song.get("loved"))}

    def status(self) -> str:
        if not self._alive():
            return "Music is off."
        if self.song and self.playing:
            return f"Playing {self.song['title']} by {self.song['artist']} on {self.song.get('station') or 'Pandora'}."
        if self.song:
            return f"Paused on {self.song.get('station') or 'Pandora'}: {self.song['title']} by {self.song['artist']}."
        return "Pandora is starting."

    def list_stations(self) -> str:
        with self.lock:
            if not self._alive():
                self._start()
                self.started.wait(timeout=25)
            if not self.stations:
                self.stations, self.stations_ready = [], threading.Event()
                self._send("s")
                if self.stations_ready.wait(timeout=15):
                    self._send("\n")                                     # close the prompt, keep what was playing
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
        """station doubles as the argument for volume (a level 1-10, or up/down)."""
        action = (action or "status").strip().lower()
        table = {"play": lambda: self.play(station), "resume": lambda: self.play(None) if self._alive() else self.play(station),
                 "pause": self.pause, "stop": self.stop, "next": self.next, "skip": self.next, "love": self.love,
                 "like": self.love, "unlove": self.unlove, "unlike": self.unlove, "ban": self.ban, "dislike": self.ban, "status": self.status, "stations": self.list_stations,
                 "volume": lambda: self.volume(station)}
        if action not in table:
            return "Unknown music action " + action + "."
        t = time.monotonic()
        out = table[action]()
        log.info("music %s %s -> %r (%.1fs)", action, station or "", out, time.monotonic() - t)
        return out
