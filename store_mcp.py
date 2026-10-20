"""The household store as a stdio MCP server for Claude Code's headless mode (the voice brain
when Claude runs on the owner's subscription instead of an API key). Run with the server venv, which
has FastMCP: venv/bin/python voice/store_mcp.py. Writes are stamped voice/claude-voice.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastmcp import FastMCP  # noqa: E402
from store import Store  # noqa: E402

USER = os.environ.get("VOICE_USER", "voice")
ASSISTANT = "claude-voice"
store = Store(os.environ.get("STORE_DIR", "/home/pi/household"))
mcp = FastMCP("household")


@mcp.tool
def recall(query: str, topic: str | None = None) -> dict:
    """Search the shared household memory. Use for any question about the household, people, places, plans or past decisions."""
    return store.recall(query, topic)


@mcp.tool
def remember(topic: str, fact: str) -> dict:
    """Store one fact or decision in shared memory under a short hyphenated topic such as household, people, garden, cars."""
    return store.remember(topic, fact, USER, ASSISTANT)


@mcp.tool
def list_topics() -> list[dict]:
    """List the memory topics that exist."""
    return store.list_topics()


@mcp.tool
def get_list(list: str = "shopping") -> dict:
    """Read a household list (shopping unless told otherwise)."""
    return store.get_list(list)


@mcp.tool
def add_item(item: str, qty: int = 1, list: str = "shopping") -> dict:
    """Add an item to a household list. Returns the whole list."""
    return store.add_item(list, item, qty, f"{USER}/{ASSISTANT}")


@mcp.tool
def remove_item(item: str, list: str = "shopping") -> dict:
    """Remove an item from a household list. Returns the whole list."""
    return store.remove_item(list, item, f"{USER}/{ASSISTANT}")


@mcp.tool
def leave_note(for_assistant: str, text: str) -> dict:
    """Leave a note for another assistant: claude (the owner's claude.ai Claude: trips, plans), chatgpt (the partner's), claude-code (maintains the house computers)."""
    return store.leave_note(for_assistant, text, USER, ASSISTANT)


if __name__ == "__main__":
    mcp.run(show_banner=False)
