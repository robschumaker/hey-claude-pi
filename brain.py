"""The Claude side of the voice daemon: one spoken turn in, one spoken reply out, with the
household store's tools available in process. The MCP server is not involved.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sys
import time

import anthropic

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from store import Store, StoreError  # noqa: E402

log = logging.getLogger("voice.brain")

SYSTEM = """You are Claude, the voice of a small household's shared memory, answering through a speaker in the house.

How to speak: one or two short sentences, plain words, no lists, no markdown, no emoji, no URLs. You are heard, not read. Numbers and dates as people say them aloud. If you did something, say what you did in a few words. If you are not sure what was said, say what you heard and ask.

What you can do: search and add to the shared memory (facts, decisions, where things are), read and change the household lists (the shopping list is the one that matters), and leave a note for the owner's other Claude or for the partner's ChatGPT. When someone tells you a fact worth keeping, remember it under a sensible topic without being asked twice. When asked for something you have no tool for, say so briefly rather than guessing.

Keep the conversation going naturally if the person asks a follow-up; do not repeat the previous answer."""

TOOLS = [
    {"name": "recall", "description": "Search the shared household memory. Use for any question about the household, people, places, plans or past decisions.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "topic": {"type": ["string", "null"]}},
                      "required": ["query", "topic"], "additionalProperties": False}, "strict": True},
    {"name": "remember", "description": "Store one fact or decision in shared memory under a short hyphenated topic such as household, people, garden, cars.",
     "input_schema": {"type": "object", "properties": {"topic": {"type": "string"}, "fact": {"type": "string"}},
                      "required": ["topic", "fact"], "additionalProperties": False}, "strict": True},
    {"name": "list_topics", "description": "List the memory topics that exist.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}, "strict": True},
    {"name": "get_list", "description": "Read a household list. The list name is shopping unless told otherwise.",
     "input_schema": {"type": "object", "properties": {"list": {"type": "string"}}, "required": ["list"], "additionalProperties": False}, "strict": True},
    {"name": "add_item", "description": "Add an item to a household list, with a quantity.",
     "input_schema": {"type": "object", "properties": {"list": {"type": "string"}, "item": {"type": "string"}, "qty": {"type": "integer"}},
                      "required": ["list", "item", "qty"], "additionalProperties": False}, "strict": True},
    {"name": "remove_item", "description": "Remove an item from a household list.",
     "input_schema": {"type": "object", "properties": {"list": {"type": "string"}, "item": {"type": "string"}},
                      "required": ["list", "item"], "additionalProperties": False}, "strict": True},
    {"name": "leave_note", "description": "Leave a note for another assistant: claude is the owner's claude.ai Claude (trips, itineraries, plans), chatgpt is the partner's assistant, claude-code is the one that maintains the house computers.",
     "input_schema": {"type": "object", "properties": {"for_assistant": {"type": "string", "enum": ["claude", "chatgpt", "claude-code"]}, "text": {"type": "string"}, "topic": {"type": ["string", "null"]}},
                      "required": ["for_assistant", "text", "topic"], "additionalProperties": False}, "strict": True},
]


class Brain:
    def __init__(self, store_dir: str, model: str, user: str = "voice", assistant: str = "claude-voice",
                 session_seconds: int = 300):
        self.client = anthropic.Anthropic()
        self.store = Store(store_dir)
        self.model, self.user, self.assistant = model, user, assistant
        self.session_seconds = session_seconds
        self.history: list[dict] = []
        self.last_turn = 0.0

    # ---- tools -----------------------------------------------------------------------------
    def run_tool(self, name: str, args: dict):
        s, by = self.store, f"{self.user}/{self.assistant}"
        if name == "recall":
            return s.recall(args["query"], args.get("topic") or None)
        if name == "remember":
            return s.remember(args["topic"], args["fact"], self.user, self.assistant)
        if name == "list_topics":
            return s.list_topics()
        if name == "get_list":
            return s.get_list(args["list"])
        if name == "add_item":
            return s.add_item(args["list"], args["item"], args["qty"], by)
        if name == "remove_item":
            return s.remove_item(args["list"], args["item"], by)
        if name == "leave_note":
            return s.leave_note(args["for_assistant"], args["text"], self.user, self.assistant, args.get("topic"))
        raise StoreError(f"unknown tool {name}")

    # ---- one turn --------------------------------------------------------------------------
    def reply(self, heard: str) -> str:
        now = time.monotonic()
        if now - self.last_turn > self.session_seconds:
            self.history = []
        self.last_turn = now
        self.history.append({"role": "user", "content": heard})
        today = dt.datetime.now().strftime("%A %d %B %Y, %H:%M")
        system = [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}},
                  {"type": "text", "text": f"Now: {today}."}]
        t0 = time.monotonic()
        for _ in range(6):
            resp = self.client.beta.messages.create(
                model=self.model, max_tokens=600, system=system, messages=self.history, tools=TOOLS,
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
            self.history.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason == "refusal":
                log.warning("refusal: %s", getattr(resp, "stop_details", None))
                return "I can't help with that one."
            if resp.stop_reason != "tool_use":
                break
            results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                try:
                    out = self.run_tool(block.name, block.input)
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(out, default=str)})
                except StoreError as e:
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(e), "is_error": True})
                log.info("tool %s %s", block.name, json.dumps(block.input)[:120])
            self.history.append({"role": "user", "content": results})
        text = " ".join(b.text for b in resp.content if b.type == "text").strip()
        log.info("claude %.1fs (%s): %r", time.monotonic() - t0, resp.model, text)
        self.history = self.history[-20:]
        return text or "Done."
