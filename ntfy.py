"""Text commands from the owner's phone through ntfy, each authenticated by a Google Authenticator
code (TOTP, RFC 6238), answered on a second ntfy topic.

A message on NTFY_COMMAND_TOPIC must contain a six-digit code anywhere in its text, for example
"483920 is the barn door closed". The code must match NTFY_TOTP_SECRET within one 30 s step either way,
may not be reused, and after five bad codes in ten minutes the topic is ignored for ten minutes. The
rest of the text goes to the brain like a spoken question and the reply is published to NTFY_REPLY_TOPIC.
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

log = logging.getLogger("voice.ntfy")
CODE = re.compile(r"(?<!\d)(\d{6})(?!\d)")


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

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="ntfy").start()
        log.info("ntfy: listening on %s/%s, replies to %s", self.server, self.command_topic, self.reply_topic)

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
            log.warning("ntfy: rejected a message (no valid code)")
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


def start_if_configured(brain) -> Listener | None:
    topic, secret = os.environ.get("NTFY_COMMAND_TOPIC"), os.environ.get("NTFY_TOTP_SECRET")
    if not topic or not secret:
        return None
    lst = Listener(brain, os.environ.get("NTFY_SERVER", "https://ntfy.sh"), topic,
                   os.environ.get("NTFY_REPLY_TOPIC", topic + "-replies"), secret)
    lst.start()
    return lst
