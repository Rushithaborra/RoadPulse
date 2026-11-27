"""Multi-source fusion: turns raw signals into incidents with a confidence score and severity."""
from __future__ import annotations

import statistics

from . import config
from .geo import haversine_m
from .models import Incident, Signal, SEVERITY_ORDER, now
from .severity import DEFAULT_SEVERITY, blocked_vehicles, depth_to_severity
from .store import Store

TYPE_LABEL = {
    "flood": "Waterlogging", "accident": "Accident", "blockage": "Road blocked",
    "construction": "Construction", "slowdown": "Unexplained slowdown",
}
# A slowdown is a symptom; it merges into any nearby incident and is replaced by the more specific type.
SPECIFICITY = {"slowdown": 0, "construction": 1, "blockage": 2, "accident": 2, "flood": 2}


def confidence(signals: list[Signal]) -> float:
    """C = 1 - prod(1 - w_i), counting each distinct source only once (its strongest signal)."""
    best: dict[tuple, float] = {}
    for s in signals:
        key = (s.source, s.source_id or "")
        best[key] = max(best.get(key, 0.0), s.weight)
    p = 1.0
    for w in best.values():
        p *= 1.0 - min(w, 0.99)
    return round(1.0 - p, 3)


def _compatible(inc: Incident, sig: Signal) -> bool:
    if inc.status == "Cleared" or inc.type == "construction" or sig.type == "construction":
        return False
    if inc.type == sig.type or "slowdown" in (inc.type, sig.type):
        return True
    return {inc.type, sig.type} <= {"accident", "blockage"}


SYMPTOM_RADIUS_M = 120  # a slowdown joins a different-type incident only when this close


def find_match(store: Store, sig: Signal) -> Incident | None:
    best, best_d = None, float("inf")
    for inc in store.incidents.values():
        if not _compatible(inc, sig):
            continue
        last = max((s.ts for s in inc.signals), default=inc.created)
        if sig.ts - last > config.CLUSTER_WINDOW_S:
            continue
        d = haversine_m(inc.lat, inc.lon, sig.lat, sig.lon)
        radius = config.CLUSTER_RADIUS_M if inc.type == sig.type else SYMPTOM_RADIUS_M
        if d <= radius and d < best_d:
            best, best_d = inc, d
    return best


# Callbacks run on every status change: hook(incident, status, ts, reason, actor). Used for persistence + audit.
STATUS_HOOKS: list = []


def set_status(store: Store, inc: Incident, status: str, ts: float | None = None, reason: str | None = None,
               actor: str = "system"):
    ts = ts or now()
    if inc.status == status:
        return
    inc.status = status
    inc.status_times.setdefault(status, ts)
    inc.updated = ts
    if status == "Cleared":
        inc.cleared_reason = reason
    store.log(ts, f"{inc.title} → {status}" + (f" ({reason})" if reason else ""))
    for hook in STATUS_HOOKS:
        hook(inc, status, ts, reason, actor)


def refresh(store: Store, inc: Incident, place: str | None = None):
    """Recompute confidence, severity, affected vehicles and title after any change."""
    inc.confidence = confidence(inc.signals)
    depths = [s for s in inc.signals if s.depth_cm is not None]
    if inc.type == "flood" and depths:
        inc.depth_cm = max(depths, key=lambda s: s.ts).depth_cm
        inc.severity = depth_to_severity(inc.depth_cm)
    elif inc.type != "construction":
        inc.severity = DEFAULT_SEVERITY[inc.type]
    inc.affected_vehicles = blocked_vehicles(inc.type, inc.severity, inc.depth_cm)
    label = TYPE_LABEL[inc.type]
    if place:
        inc.title = f"{label} · {place}"
    elif not inc.title or inc.title.split(" · ")[0] != label:
        inc.title = f"{label} · {inc.lat:.4f}, {inc.lon:.4f}" if not inc.title else f"{label} · {inc.title.split(' · ', 1)[-1]}"
    if inc.status == "Reported" and inc.confidence >= config.PUBLISH_CONFIDENCE:
        set_status(store, inc, "Verified")
    inc.updated = now()


def ingest(store: Store, sig: Signal, place: str | None = None, hotspot_id: str | None = None) -> Incident:
    """Attach a signal to a matching incident or open a new one."""
    with store.lock:
        inc = find_match(store, sig)
        if inc is None:
            inc = Incident(type=sig.type, lat=sig.lat, lon=sig.lon, hotspot_id=hotspot_id)
            inc.status_times["Reported"] = sig.ts
            store.incidents[inc.id] = inc
            created = True
        else:
            created = False
            if SPECIFICITY[sig.type] > SPECIFICITY[inc.type]:
                inc.type = sig.type
                inc.lat, inc.lon = sig.lat, sig.lon  # a specific report pins the location better
            if hotspot_id and not inc.hotspot_id:
                inc.hotspot_id = hotspot_id
        inc.signals.append(sig)
        before = inc.severity
        refresh(store, inc, place)
        if created:
            store.log(sig.ts, f"New: {inc.title} (from {sig.source}, confidence {inc.confidence:.2f})")
        elif SEVERITY_ORDER[inc.severity] != SEVERITY_ORDER[before]:
            store.log(sig.ts, f"{inc.title}: severity {before} → {inc.severity}")
        return inc


def median_speed(samples) -> float | None:
    vals = [s for _, s, _ in samples]
    return statistics.median(vals) if vals else None
