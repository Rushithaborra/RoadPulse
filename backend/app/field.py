"""Field validation: traffic police on the scene.

A citizen report never publishes on its own. The flow we agreed on:
  1. A report arrives (untrusted).
  2. The control room sends the nearest police unit to check (assign).
  3. The unit accepts, travels, arrives, and reports what it SEES: blocked, one lane, not real, or open.
  4. The control room confirms (one tap, using the police finding as the default severity) and drivers are warned.
The control room can fast-track a real emergency (urgent), which is recorded. Water sensors, cameras and planned works
are already trusted and do not need a police check.

A police report is a strong signal but it does NOT publish by itself: both people validate.
"""
from __future__ import annotations

from . import config
from .geo import haversine_m
from .models import Incident, Signal

FINDING_SEVERITY = {"blocked": "Closed", "one_lane": "Medium"}
OPEN_CALL = ("assigned", "accepted", "on_way", "arrived", "scene_safe")
ORDER = ["assigned", "accepted", "on_way", "arrived", "scene_safe"]


def needs_field(inc: Incident) -> bool:
    """True when the control room must wait for a police confirmation (or mark the incident urgent)."""
    if not config.field_required() or inc.urgent or inc.field_confirmed_at or inc.type == "construction":
        return False
    return not any(s.source in ("sensor", "cctv", "planned") for s in inc.signals)


def field_state(inc: Incident) -> str:
    """One word for the screens: none, assigned, on_way, arrived, confirmed, not_real, open."""
    if inc.field_open_at:
        return "open"
    if inc.field_reports and inc.field_reports[-1]["finding"] == "not_real":
        return "not_real"
    if inc.field_confirmed_at:
        return "confirmed"
    live = [a for a in inc.assignments if a["status"] in OPEN_CALL]
    if not live:
        return "none"
    return max(live, key=lambda a: ORDER.index(a["status"]))["status"]


def assign(inc: Incident, unit: str, by: str, ts: float) -> dict:
    for a in inc.assignments:
        if a["unit"] == unit and a["status"] in OPEN_CALL:
            return a                                   # already on it
    a = {"unit": unit, "status": "assigned", "by": by, "at": ts, "history": [["assigned", ts]]}
    inc.assignments.append(a)
    inc.updated = ts
    return a


def set_status(inc: Incident, unit: str, status: str, ts: float) -> dict | None:
    a = next((x for x in inc.assignments if x["unit"] == unit and x["status"] in OPEN_CALL + ("declined",)), None)
    if not a:
        return None
    a["status"] = status
    a["history"].append([status, ts])
    inc.updated = ts
    return a


def add_report(inc: Incident, unit: str, finding: str, need: list, note: str | None, ts: float) -> dict:
    """Record what police saw. Adds a strong (but not auto-publishing) signal."""
    rep = {"unit": unit, "finding": finding, "need": list(need), "note": note, "at": ts}
    inc.field_reports.append(rep)
    for kind in need:
        if not any(r["unit"] == unit and r["kind"] == kind and not r.get("done") for r in inc.requests):
            inc.requests.append({"unit": unit, "kind": kind, "at": ts})
    if finding in FINDING_SEVERITY:
        inc.field_confirmed_at = ts
        inc.field_severity = FINDING_SEVERITY[finding]
        inc.field_open_at = None
        inc.signals.append(Signal(source="field", type=inc.type, lat=inc.lat, lon=inc.lon, ts=ts,
                                  weight=config.SOURCE_WEIGHTS["field"], source_id=unit,
                                  note=f"on scene: {finding}" + (f" ({note})" if note else "")))
    elif finding == "open":
        inc.field_open_at = ts
    inc.updated = ts
    return rep


def busy_counts(store) -> dict[str, int]:
    busy: dict[str, int] = {}
    for i in store.snapshot():
        for a in i.assignments:
            if a["status"] in OPEN_CALL and i.status != "Cleared":
                busy[a["unit"]] = busy.get(a["unit"], 0) + 1
    return busy


def units_by_distance(store, inc: Incident, names: list[str]) -> list[dict]:
    """Police units ranked by straight-line distance to the incident (a rough ETA at 20 km/h). Units that have
    not shared a position are listed last."""
    out = []
    busy = {}
    for i in store.snapshot():
        for a in i.assignments:
            if a["status"] in OPEN_CALL and i.status != "Cleared":
                busy[a["unit"]] = busy.get(a["unit"], 0) + 1
    for n in names:
        pos = store.units.get(n)
        d = haversine_m(pos["lat"], pos["lon"], inc.lat, inc.lon) if pos else None
        out.append({"unit": n, "metres": int(d) if d is not None else None,
                    "eta_min": max(1, round(d / 333)) if d is not None else None, "calls": busy.get(n, 0)})
    return sorted(out, key=lambda u: (u["metres"] is None, u["metres"] or 0, u["calls"]))
