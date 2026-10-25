"""Live readings from a Hubitat hub through its Maker API (HUBITAT_URL, HUBITAT_TOKEN). Standard library
only, so the voice daemon can use it without the MCP server's venv. Temperatures are Celsius, power watts."""
from __future__ import annotations

import json
import os
import re
import urllib.request

CATEGORIES = ("temperatures", "doors", "water", "plants", "power", "lights", "low_batteries")


def devices() -> list[dict]:
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


def summary(what: str = "all") -> dict:
    """Readings grouped by category. what: all, one of CATEGORIES, batteries, or part of a device name."""
    want = (what or "all").strip().lower()
    out: dict = {k: [] for k in CATEGORIES}
    for d in devices():
        name = (d.get("label") or d.get("name") or "").strip()
        if "on Hubitat Rock House" in name:               # the second house's hub, mirrored into this one
            name = "Rock House " + re.sub(r"\s*on Hubitat Rock House\s*$", "", name).replace("RH ", "", 1)
        a = d.get("attributes") or {}
        kind = (d.get("type") or "").lower()
        if want not in ("all", "batteries") and want not in CATEGORIES and want not in name.lower():
            continue
        batt = _num(a.get("battery"))
        if batt is not None and batt <= 20:
            out["low_batteries"].append({"device": name, "battery_pct": batt})
        if "soil moisture" in kind or name.lower().startswith("moisture -"):
            out["plants"].append({"plant": _place(name), "moisture_pct": _num(a.get("humidity")), "temp_c": _num(a.get("temperature"))})
        elif "moisture sens" in kind or "water" in a:
            out["water"].append({"sensor": _place(name), "state": a.get("water")})
        elif "contact" in a:
            out["doors"].append({"door": _place(name), "state": a.get("contact"), "temp_c": _num(a.get("temperature"))})
        elif "circuit" in kind:
            label = re.sub(r"\s*\([^)]*\)\s*$", "", re.sub(r"^Emp-\d+-", "", name))
            out["power"].append({"circuit": label, "watts": _num(a.get("power"))})
        elif "switch" in a:
            out["lights"].append({"device": name.replace("Virtual - ", ""), "state": a.get("switch"),
                                  "level_pct": _num(a.get("level")) if a.get("level") is not None else None,
                                  "watts": _num(a.get("power")) if a.get("power") is not None else None})
        elif a.get("temperature") is not None:
            out["temperatures"].append({"place": _place(name), "temp_c": _num(a.get("temperature")), "humidity_pct": _num(a.get("humidity"))})
    if want in out:
        return {want: out[want]}
    if want == "batteries":
        return {"low_batteries": out["low_batteries"]}
    return {k: v for k, v in out.items() if v}


def say_temperatures() -> str:
    rows = summary("temperatures")["temperatures"]
    return "Temperatures: " + "; ".join(f"{r['place']} {r['temp_c']}°C" + (f", {r['humidity_pct']:.0f}% humidity" if r["humidity_pct"] is not None else "") for r in rows) if rows else "No temperature sensors reported."


def say_plants() -> str:
    rows = summary("plants")["plants"]
    return "Plant moisture: " + "; ".join(f"{r['plant']} {r['moisture_pct']:.0f}%" for r in rows if r["moisture_pct"] is not None) if rows else "No plant sensors reported."
