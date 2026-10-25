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


# The kitchen dashboard's System Status tile, reproduced: same rules, same thresholds.
SWITCH_ON_LABELS = ("Workshop Fan", "Shopvac", "RH Kitchen Fan")
STALE_DAYS, DOOR_MINUTES, LOW_BATTERY = 5, 5, 10


def _clean(label: str) -> str:
    label = re.sub(r"^(Temperature|Moisture|Motion|Contact|Vibration|Water|Weather)\s*-\s*", "", label, flags=re.I)
    return re.sub(r"\s+on Hubitat Rock House$", "", label, flags=re.I).strip()


def _age_minutes(date: str | None) -> float | None:
    """Maker API dates look like 2026-10-25T13:02:11+0000."""
    import datetime as dt
    if not date:
        return None
    try:
        t = dt.datetime.strptime(date.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None
    return (dt.datetime.now(dt.timezone.utc) - t).total_seconds() / 60


def alerts() -> list[dict]:
    """What the System Status tile would show: septic pump on, named switches on, vibration, doors open
    more than five minutes, batteries under 10%, devices silent for five days or never seen."""
    out: list[dict] = []
    devs = devices()
    septic = next((d for d in devs if "septic" in (d.get("label") or d.get("name") or "").lower() and "power" in (d.get("attributes") or {})), None)
    if septic and (_num((septic.get("attributes") or {}).get("power")) or 0) > 100:
        out.append({"name": "Septic Pump", "value": "On"})
    for d in devs:
        lbl = d.get("label") or d.get("name") or ""
        a = d.get("attributes") or {}
        name = _clean(lbl)
        age = _age_minutes(d.get("date"))
        if name in SWITCH_ON_LABELS:
            if a.get("switch") == "on":
                out.append({"name": name, "value": "On"})
            continue
        if a.get("shock") == "detected":
            out.append({"name": name, "value": "Vibration"})
        if a.get("contact") == "open" and age is not None and age >= DOOR_MINUTES:
            out.append({"name": name, "value": "Open"})
        batt = _num(a.get("battery"))
        if batt is not None and batt < LOW_BATTERY:
            out.append({"name": name, "value": f"battery {batt:.0f}%"})
        if len(a) > 2 and "EmporiaVue" not in lbl:
            if age is None:
                out.append({"name": name, "value": "Unresponsive"})
            elif age >= STALE_DAYS * 1440:
                out.append({"name": name, "value": f"silent {int(age // 1440)} days"})
    return out


def say_alerts() -> str:
    rows = alerts()
    if not rows:
        return "All clear."
    return "; ".join(f"{r['name']}: {r['value']}" for r in rows)


def say_temperatures() -> str:
    rows = summary("temperatures")["temperatures"]
    return "Temperatures: " + "; ".join(f"{r['place']} {r['temp_c']}°C" + (f", {r['humidity_pct']:.0f}% humidity" if r["humidity_pct"] is not None else "") for r in rows) if rows else "No temperature sensors reported."


def say_plants() -> str:
    rows = summary("plants")["plants"]
    return "Plant moisture: " + "; ".join(f"{r['plant']} {r['moisture_pct']:.0f}%" for r in rows if r["moisture_pct"] is not None) if rows else "No plant sensors reported."
