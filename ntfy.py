"""Text commands from the owner's phone through ntfy, each authenticated by a Google Authenticator
code (TOTP, RFC 6238), answered on a second ntfy topic.

A message on NTFY_COMMAND_TOPIC must contain a six-digit code anywhere in its text, for example
"483920 is the barn door closed". The code must match NTFY_TOTP_SECRET within one 30 s step either way,
may not be reused, and after five bad codes in ten minutes the topic is ignored for ten minutes. The
rest of the text goes to the brain like a spoken question and the reply is published to NTFY_REPLY_TOPIC.

Open queries: a message without a valid code is matched against a short allow-list of harmless readings
(NTFY_OPEN_QUERIES, default "temperatures,plants") and answered by code that reads the sensors directly.
Such a message never reaches the brain, so it cannot be talked into anything else; anything unmatched is
dropped without a reply, and open queries are limited to 20 an hour. Doors, leaks, power, lights and
everything the brain can do stay behind the code: they say whether anyone is home.
Topics on a public ntfy server are readable by anyone who guesses their names, so use long random ones.
Standard library only.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import struct
import threading
import time
import urllib.parse
import urllib.request

from voice import hubitat

log = logging.getLogger("voice.ntfy")
CODE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
OPEN = {   # category -> (what the message must mention, how to answer). Nothing else is ever open.
    "temperatures": (re.compile(r"\b(temp|temps|temperature|temperatures|warm|cold|hot|humidity)\b", re.I), hubitat.say_temperatures),
    "plants": (re.compile(r"\b(plant|plants|moisture|soil|planter|planters|dry)\b", re.I), hubitat.say_plants),
}


def totp(secret_b32: str, step: int) -> str:
    key = base64.b32decode(secret_b32.strip().replace(" ", "").upper() + "=" * (-len(secret_b32.strip()) % 8))
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    return "%06d" % ((struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 1000000)


class Authenticator:
    def __init__(self, secret_b32: str, window: int = 1):
        self.secret, self.window = secret_b32, window
        self.used: set[int] = set()
        self.failures: list[float] = []

    def check(self, code: str, now: float | None = None) -> bool:
        now = now or time.time()
        self.failures = [t for t in self.failures if now - t < 600]
        if len(self.failures) >= 5:
            return False
        step = int(now // 30)
        for s in range(step - self.window, step + self.window + 1):
            if s not in self.used and hmac.compare_digest(totp(self.secret, s), code):
                self.used.add(s)
                self.used = {u for u in self.used if u >= step - 10}
                return True
        self.failures.append(now)
        return False


class Listener:
    def __init__(self, brain, server: str, command_topic: str, reply_topic: str, secret_b32: str):
        self.brain, self.server = brain, server.rstrip("/")
        self.command_topic, self.reply_topic = command_topic, reply_topic
        self.auth = Authenticator(secret_b32)
        self.since = str(int(time.time()))
        wanted = [w.strip() for w in os.environ.get("NTFY_OPEN_QUERIES", "temperatures,plants").split(",") if w.strip()]
        self.open = {k: OPEN[k] for k in wanted if k in OPEN}
        self.open_times: list[float] = []

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="ntfy").start()
        log.info("ntfy: listening on %s/%s, replies to %s", self.server, self.command_topic, self.reply_topic)
        when = os.environ.get("NTFY_MORNING_TIME", "07:00").strip()
        if when:
            threading.Thread(target=self._morning, args=(when,), daemon=True, name="ntfy-morning").start()
            log.info("ntfy: morning status every day at %s", when)

    def _morning(self, when: str) -> None:
        """Once a day, what the dashboard's System Status tile shows, on the reply topic."""
        import datetime as dt
        hh, mm = (int(x) for x in when.split(":"))
        while True:
            now = dt.datetime.now()
            nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if nxt <= now:
                nxt += dt.timedelta(days=1)
            time.sleep((nxt - now).total_seconds())
            try:
                text = hubitat.say_alerts()
                self.publish(text, title="Morning status")
                log.info("ntfy: morning status sent: %s", text[:80])
            except Exception:
                log.exception("ntfy: morning status failed")

    def publish(self, text: str, title: str = "Claude") -> None:
        req = urllib.request.Request(f"{self.server}/{self.reply_topic}", data=text.encode(), method="POST",
                                     headers={"Title": title, "Content-Type": "text/plain; charset=utf-8"})
        with urllib.request.urlopen(req, timeout=15):
            pass

    def _run(self) -> None:
        while True:
            try:
                url = f"{self.server}/{self.command_topic}/json?since={urllib.parse.quote(self.since)}"
                with urllib.request.urlopen(url, timeout=120) as r:      # ntfy sends a keepalive every ~45 s
                    for raw in r:
                        try:
                            ev = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if ev.get("id"):
                            self.since = ev["id"]
                        if ev.get("event") == "message":
                            self._handle(ev)
            except Exception as e:
                log.warning("ntfy: %s; reconnecting", e)
                time.sleep(10)

    def _handle(self, ev: dict) -> None:
        text = (ev.get("message") or "").strip()
        if time.time() - float(ev.get("time", 0)) > 120:
            log.info("ntfy: ignoring a message older than two minutes")
            return
        m = CODE.search(text)
        if not m or not self.auth.check(m.group(1)):
            self._open_query(text)
            return
        ask = (text[:m.start()] + text[m.end():]).strip(" :,-") or "status"
        log.info("ntfy: command %r", ask)
        if ask.lower() in ("new conversation", "start over", "forget this conversation"):
            self.brain.new_conversation(); answer = "Started a new conversation."
        else:
            try:
                answer = self.brain.reply(f"[text message from the owner's phone] {ask}")
            except Exception:
                log.exception("ntfy: brain failed")
                answer = "Sorry, I couldn't reach my brain just now."
        try:
            self.publish(answer)
        except Exception:
            log.exception("ntfy: publish failed")


    def _open_query(self, text: str) -> None:
        """No valid code: answer only from the allow-list, by code, never through the brain."""
        now = time.time()
        self.open_times = [t for t in self.open_times if now - t < 3600]
        if len(self.open_times) >= 20:
            log.warning("ntfy: open-query limit reached; dropping")
            return
        for name, (pattern, answer) in self.open.items():
            if pattern.search(text):
                self.open_times.append(now)
                log.info("ntfy: open query %s", name)
                try:
                    self.publish(answer())
                except Exception:
                    log.exception("ntfy: open query failed")
                return
        log.warning("ntfy: dropped a message (no valid code, not an open query)")


def start_if_configured(brain) -> Listener | None:
    topic, secret = os.environ.get("NTFY_COMMAND_TOPIC"), os.environ.get("NTFY_TOTP_SECRET")
    if not topic or not secret:
        return None
    lst = Listener(brain, os.environ.get("NTFY_SERVER", "https://ntfy.sh"), topic,
                   os.environ.get("NTFY_REPLY_TOPIC", topic + "-replies"), secret)
    lst.start()
    return lst
