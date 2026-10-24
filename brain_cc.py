"""The Claude side of the voice daemon when Claude runs on the owner's subscription: one Claude Code
headless process (claude -p with streaming JSON in and out) stays alive with the store attached
as a stdio MCP server (voice/store_mcp.py), so a turn costs only the model's time, not the
seven seconds Claude Code takes to start on the Pi. The process is restarted after the
conversation has been idle for session_seconds, so a fresh wake starts a fresh conversation.
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
                 session_seconds: int = 300, timeout: int = 90):
        self.claude, self.model, self.workdir = claude_bin, model, workdir
        self.session_seconds, self.timeout = session_seconds, timeout
        self.proc: subprocess.Popen | None = None
        self.last_turn = 0.0
        self.lock = threading.Lock()
        os.makedirs(workdir, exist_ok=True)
        self.start()
        threading.Thread(target=self._idle_reaper, daemon=True).start()

    # ---- process -----------------------------------------------------------------------------
    def start(self) -> None:
        cmd = [self.claude, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
               "--verbose", "--system-prompt", SYSTEM,
               "--mcp-config", os.path.join(HERE, "mcp.json"), "--strict-mcp-config",
               "--allowedTools", "mcp__household__*,WebSearch,WebFetch", "--disallowedTools", NOT_FOR_VOICE,
               "--max-turns", "8"]
        if self.model:
            cmd += ["--model", self.model]
        self.proc = subprocess.Popen(cmd, cwd=self.workdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1)
        log.info("claude code started pid %d (model %s)", self.proc.pid, self.model or "default")
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
            return "Sorry, I couldn't reach my brain just now."
