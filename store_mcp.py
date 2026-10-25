"""The household store as a stdio MCP server for Claude Code's headless mode (the voice brain
when Claude runs on the owner's subscription instead of an API key). Run with the server venv, which
has FastMCP: venv/bin/python voice/store_mcp.py. Writes are stamped voice/claude-voice.
"""
import os
import re
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
def leave_note(for_assistant: str, text: str, topic: str | None = None) -> dict:
    """Leave a note for another assistant: claude (the owner's claude.ai Claude: trips, plans), chatgpt (the partner's), claude-code (maintains the house computers). Tag it with a topic when it is about one."""
    return store.leave_note(for_assistant, text, USER, ASSISTANT, topic)


WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "rain showers", 81: "rain showers",
       82: "violent rain showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorms",
       96: "thunderstorms with hail", 99: "thunderstorms with heavy hail"}


@mcp.tool
def weather(days: int = 3) -> dict:
    """Current conditions and the forecast at the house (Open-Meteo, no key). days: how many days ahead to
    include, 1 to 7. Metric: Celsius, km/h, millimetres, times local. Use for any weather question."""
    import json
    import urllib.request
    lat = os.environ.get("VOICE_HOME_LAT", "32.78"); lon = os.environ.get("VOICE_HOME_LON", "-96.81")
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&timezone=auto"
           "&temperature_unit=celsius&wind_speed_unit=kmh&precipitation_unit=mm&forecast_days=%d"
           "&current=temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m"
           "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,sunrise,sunset"
           % (lat, lon, max(1, min(int(days), 7))))
    with urllib.request.urlopen(url, timeout=8) as r:
        d = json.load(r)
    c, dy = d["current"], d["daily"]
    return {"place": os.environ.get("VOICE_HOME_NAME", "home"), "as_of": c["time"],
            "now": {"temp_c": round(c["temperature_2m"]), "feels_like_c": round(c["apparent_temperature"]),
                    "humidity_pct": c["relative_humidity_2m"], "sky": WMO.get(c["weather_code"], "unknown"),
                    "wind_kmh": round(c["wind_speed_10m"]), "precip_mm": c["precipitation"]},
            "days": [{"date": dy["time"][i], "sky": WMO.get(dy["weather_code"][i], "unknown"),
                      "high_c": round(dy["temperature_2m_max"][i]), "low_c": round(dy["temperature_2m_min"][i]),
                      "rain_chance_pct": dy["precipitation_probability_max"][i], "precip_mm": dy["precipitation_sum"][i],
                      "sunrise": dy["sunrise"][i][-5:], "sunset": dy["sunset"][i][-5:]} for i in range(len(dy["time"]))]}


def _hubitat_devices() -> list[dict]:
    import json
    import urllib.request
    base, token = os.environ.get("HUBITAT_URL"), os.environ.get("HUBITAT_TOKEN")
    if not base or not token:
        raise RuntimeError("Hubitat is not configured (HUBITAT_URL and HUBITAT_TOKEN)")
    with urllib.request.urlopen(f"{base}/devices/all?access_token={token}", timeout=8) as r:
        return json.load(r)


def _place(name: str) -> str:
    """'Temperature - Barn' -> 'Barn'; 'Rock House Contact - Front door' -> 'Rock House front door'."""
    house = "Rock House " if name.startswith("Rock House ") else ""
    rest = name[len(house):]
    rest = rest.split("-", 1)[-1].strip() if " - " in rest else rest
    return (house + (rest[0].lower() + rest[1:] if house and rest else rest)).strip()


def _num(v):
    try:
        return round(float(v), 1)
    except (TypeError, ValueError):
        return None


@mcp.tool
def house(what: str = "all") -> dict:
    """Live readings from the house's sensors and switches (the same Hubitat telemetry the kitchen dashboard
    shows). what: all, temperatures, doors, water, plants, power, lights, batteries, or part of a device name.
    Temperatures are Celsius, power in watts, plant moisture in percent. Use for anything about the state of
    the house right now: how warm a room is, whether a door is open, leaks, which lights are on, what is
    drawing power, dry plants, low batteries."""
    want = (what or "all").strip().lower()
    out: dict = {"temperatures": [], "doors": [], "water": [], "plants": [], "power": [], "lights": [], "low_batteries": []}
    for d in _hubitat_devices():
        name = (d.get("label") or d.get("name") or "").strip()
        if "on Hubitat Rock House" in name:               # the second house's hub, mirrored into this one
            name = "Rock House " + re.sub(r"\s*on Hubitat Rock House\s*$", "", name).replace("RH ", "", 1)
        a = d.get("attributes") or {}
        kind = (d.get("type") or "").lower()
        low = name.lower()
        if want not in ("all", "temperatures", "doors", "water", "plants", "power", "lights", "batteries") and want not in low:
            continue
        batt = _num(a.get("battery"))
        if batt is not None and batt <= 20:
            out["low_batteries"].append({"device": name, "battery_pct": batt})
        if "soil moisture" in kind or low.startswith("moisture -"):
            out["plants"].append({"plant": _place(name), "moisture_pct": _num(a.get("humidity")),
                                  "temp_c": _num(a.get("temperature"))})
        elif "moisture sens" in kind or "water" in a:
            out["water"].append({"sensor": _place(name), "state": a.get("water")})
        elif "contact" in a:
            out["doors"].append({"door": _place(name), "state": a.get("contact"),
                                 "temp_c": _num(a.get("temperature"))})
        elif "circuit" in kind:
            label = re.sub(r"^Emp-\d+-", "", name)
            label = re.sub(r"\s*\([^)]*\)\s*$", "", label)
            out["power"].append({"circuit": label, "watts": _num(a.get("power"))})
        elif "switch" in a:
            out["lights"].append({"device": name.replace("Virtual - ", ""), "state": a.get("switch"),
                                  "level_pct": _num(a.get("level")) if a.get("level") is not None else None,
                                  "watts": _num(a.get("power")) if a.get("power") is not None else None})
        elif a.get("temperature") is not None:
            out["temperatures"].append({"place": _place(name), "temp_c": _num(a.get("temperature")),
                                        "humidity_pct": _num(a.get("humidity"))})
    if want in out:
        return {want: out[want]}
    if want == "batteries":
        return {"low_batteries": out["low_batteries"]}
    return {k: v for k, v in out.items() if v}


if __name__ == "__main__":
    mcp.run(show_banner=False)
