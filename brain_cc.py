"""The Claude side of the voice daemon when Claude runs on the owner's subscription: each spoken
sentence goes to Claude Code's headless mode (claude -p) with the store attached as a stdio MCP
server (voice/store_mcp.py). Follow-ups resume the same session for five minutes.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time

from voice.brain import SYSTEM

log = logging.getLogger("voice.brain_cc")
HERE = os.path.dirname(os.path.abspath(__file__))
NOT_FOR_VOICE = "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,Agent,NotebookEdit,TodoWrite,Skill"


class ClaudeCodeBrain:
    def __init__(self, claude_bin: str = os.path.expanduser("~/.local/bin/claude"),
                 model: str | None = None, workdir: str = os.path.expanduser("~/voice-work"),
                 session_seconds: int = 300, timeout: int = 60):
        self.claude, self.model, self.workdir = claude_bin, model, workdir
        self.session_seconds, self.timeout = session_seconds, timeout
        self.session_id: str | None = None
        self.last_turn = 0.0
        os.makedirs(workdir, exist_ok=True)

    def reply(self, heard: str) -> str:
        now = time.monotonic()
        if now - self.last_turn > self.session_seconds:
            self.session_id = None
        self.last_turn = now
        cmd = [self.claude, "-p", heard, "--output-format", "json",
               "--system-prompt", SYSTEM,
               "--mcp-config", os.path.join(HERE, "mcp.json"), "--strict-mcp-config",
               "--allowedTools", "mcp__household__*", "--disallowedTools", NOT_FOR_VOICE,
               "--max-turns", "8"]
        if self.model:
            cmd += ["--model", self.model]
        if self.session_id:
            cmd += ["--resume", self.session_id]
        t0 = time.monotonic()
        try:
            p = subprocess.run(cmd, cwd=self.workdir, capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            log.error("claude -p timed out after %ss", self.timeout)
            return "Sorry, that took too long."
        if p.returncode != 0:
            log.error("claude -p exit %d: %s", p.returncode, (p.stderr or p.stdout)[-400:])
            return "Sorry, I couldn't reach my brain just now."
        try:
            out = json.loads(p.stdout)
        except json.JSONDecodeError:
            log.error("claude -p gave non-JSON: %r", p.stdout[-400:])
            return "Sorry, something went wrong."
        self.session_id = out.get("session_id") or self.session_id
        text = (out.get("result") or "").strip()
        log.info("claude code %.1fs (%s turns, $%.4f): %r", time.monotonic() - t0,
                 out.get("num_turns"), out.get("total_cost_usd") or 0, text)
        if out.get("is_error"):
            log.error("claude -p is_error: %r", text)
            return "Sorry, something went wrong."
        return text or "Done."
