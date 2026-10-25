"""The Claude side of the voice daemon when Claude runs on the owner's subscription: one Claude Code
headless process (claude -p with streaming JSON in and out) stays alive with the store attached
as a stdio MCP server (voice/store_mcp.py), so a turn costs only the model's time, not the
seven seconds Claude Code takes to start on the Pi. The process is restarted after the
conversation has been idle for session_seconds to give the Pi its memory back, but the conversation
itself is resumed (claude --resume) for up to session_hours, so wakes through the day share one thread.
The session id lives in STORE_DIR/voice-session.json and survives daemon restarts and reboots.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time

from voice.brain import SYSTEM

log = logging.getLogger("voice.brain_cc")
HERE = os.path.dirname(os.path.abspath(__file__))
NOT_FOR_VOICE = "Bash,Edit,Write,Read,Glob,Grep,Agent,NotebookEdit,TodoWrite,Skill"


class ClaudeCodeBrain:
    def __init__(self, claude_bin: str = os.path.expanduser("~/.local/bin/claude"),
                 model: str | None = None, workdir: str = os.path.expanduser("~/voice-work"),
                 session_seconds: int = 300, timeout: int = 90,
                 session_file: str | None = None, session_hours: float = 24.0):
        self.claude, self.model, self.workdir = claude_bin, model, workdir
        self.session_seconds, self.timeout = session_seconds, timeout
        self.session_file = session_file or os.path.join(os.environ.get("STORE_DIR", "/home/pi/household"), "voice-session.json")
        self.session_hours = session_hours
        self.session_id: str | None = None
        self.session_started = 0.0
        self.resumed = False
        self._load_session()
        self.proc: subprocess.Popen | None = None
        self.last_turn = 0.0
        self.lock = threading.Lock()
        os.makedirs(workdir, exist_ok=True)
        self.start()
        threading.Thread(target=self._idle_reaper, daemon=True).start()

    # ---- session -----------------------------------------------------------------------------
    def _load_session(self) -> None:
        try:
            with open(self.session_file) as f:
                d = json.load(f)
            if time.time() - float(d.get("started", 0)) < self.session_hours * 3600:
                self.session_id, self.session_started = d.get("id"), float(d["started"])
        except (OSError, ValueError, KeyError):
            pass

    def _save_session(self) -> None:
        try:
            with open(self.session_file, "w") as f:
                json.dump({"id": self.session_id, "started": self.session_started}, f)
        except OSError:
            log.exception("could not save the session id")

    def new_conversation(self) -> None:
        """Forget the running thread; the next turn starts fresh."""
        with self.lock:
            self.session_id, self.session_started = None, 0.0
            self._save_session()
            self.stop(); self.start()

    # ---- process -----------------------------------------------------------------------------
    def start(self) -> None:
        if self.session_id and time.time() - self.session_started > self.session_hours * 3600:
            log.info("conversation is %.0f h old; starting a new one", (time.time() - self.session_started) / 3600)
            self.session_id, self.session_started = None, 0.0
        cmd = [self.claude, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
               "--verbose", "--system-prompt", SYSTEM,
               "--mcp-config", os.path.join(HERE, "mcp.json"), "--strict-mcp-config",
               "--allowedTools", "mcp__household__*,WebSearch,WebFetch", "--disallowedTools", NOT_FOR_VOICE,
               "--max-turns", "8"]
        self.resumed = bool(self.session_id)
        if self.session_id:
            cmd += ["--resume", self.session_id]
        if self.model:
            cmd += ["--model", self.model]
        self.proc = subprocess.Popen(cmd, cwd=self.workdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1)
        log.info("claude code started pid %d (model %s%s)", self.proc.pid, self.model or "default",
                 ", resuming the conversation" if self.resumed else ", new conversation")
        threading.Thread(target=self._warm_up, daemon=True).start()

    def _warm_up(self) -> None:
        """Claude Code launches the store MCP server on the first turn, which costs about seven
        seconds on the Pi; spend them now rather than on the first real question."""
        with self.lock:
            t = time.monotonic()
            self._turn("Call list_topics, then reply with the single word ready.", quiet=True)
            log.info("warm-up done in %.1fs", time.monotonic() - t)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
        self.proc = None

    def _idle_reaper(self) -> None:
        while True:
            time.sleep(15)
            with self.lock:
                if self.proc and self.last_turn and time.monotonic() - self.last_turn > self.session_seconds:
                    log.info("conversation idle; starting a fresh one")
                    self.stop(); self.start(); self.last_turn = 0.0

    # ---- one turn ----------------------------------------------------------------------------
    def reply(self, heard: str) -> str:
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self.start()
            return self._turn(heard)

    def _turn(self, heard: str, quiet: bool = False) -> str:
        if True:
            self.last_turn = time.monotonic()
            t0 = time.monotonic()
            try:
                self.proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": heard}}) + "\n")
                self.proc.stdin.flush()
                deadline = t0 + self.timeout
                while time.monotonic() < deadline:
                    line = self.proc.stdout.readline()
                    if not line:
                        raise RuntimeError("claude code exited")
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    sid = ev.get("session_id")
                    if sid and sid != self.session_id:
                        self.session_id, self.session_started = sid, time.time()
                        self._save_session()
                    if ev.get("type") == "result":
                        text = (ev.get("result") or "").strip()
                        if not quiet:
                            log.info("claude code %.1fs (%s): %r", time.monotonic() - t0, ev.get("subtype"), text)
                        if ev.get("is_error") or ev.get("subtype") != "success":
                            return text or "Sorry, something went wrong."
                        self.last_turn = time.monotonic()
                        return text or "Done."
                log.error("claude code: no result within %ss", self.timeout)
            except Exception:
                log.exception("claude code turn failed")
            self.stop()
            if self.resumed:                              # a stale or unknown session id: do not keep trying it
                log.info("resume failed; the next conversation starts fresh")
                self.session_id, self.session_started = None, 0.0
                self._save_session()
            return "Sorry, I couldn't reach my brain just now."
