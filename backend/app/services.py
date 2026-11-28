"""Input handlers (sensor, citizen report, speed probe, planned works) and the lifecycle tick."""
from __future__ import annotations

import statistics

from . import config, probe_rules
from .fusion import ingest, refresh, set_status
from .geo import haversine_m
from .models import CitizenReport, Incident, PlannedWork, ProbeSample, SensorReading, Signal, now
from .roadgraph import RoadGraph
from .store import Store

# ---------------- sensors ----------------

def handle_sensor(store: Store, r: SensorReading) -> dict:
    ts = r.ts or now()
    h = store.hotspot_by_sensor(r.sensor_id)
    if h is None:
        return {"ok": False, "error": f"unknown sensor {r.sensor_id}"}
    with store.lock:
        if h.last_reading is not None and ts < h.last_reading:
            # late (buffered) reading: keep it in history, but never let it overwrite newer state
            h.history.append((ts, max(0.0, r.depth_cm)))
            return {"ok": True, "incident": None, "depth_cm": h.depth_cm, "stale": True}
        h.depth_cm, h.last_reading, h.last_simulated = max(0.0, r.depth_cm), ts, r.simulated
        h.history.append((ts, h.depth_cm))
        h.above_count = h.above_count + 1 if h.depth_cm >= config.DEPTH_LOW else 0
        open_inc = next((i for i in store.incidents.values() if i.hotspot_id == h.id and i.status != "Cleared"), None)
        sig = Signal(source="sensor", type="flood", lat=h.lat, lon=h.lon, ts=ts,
                     weight=config.SOURCE_WEIGHTS["sensor"], depth_cm=h.depth_cm, source_id=h.sensor_id,
                     simulated=r.simulated)
        if open_inc is not None:
            # keep tracking depth (also below threshold) so auto-clear can see the water going down
            open_inc.signals.append(sig)
            refresh(store, open_inc, h.name)
            return {"ok": True, "incident": open_inc.id, "depth_cm": h.depth_cm}
        if h.above_count >= 2:  # two consecutive readings filter splashes and passing vehicles
            inc = ingest(store, sig, place=h.name, hotspot_id=h.id)
            return {"ok": True, "incident": inc.id, "depth_cm": h.depth_cm}
    return {"ok": True, "incident": None, "depth_cm": h.depth_cm}


# ---------------- citizen reports ----------------

def handle_report(store: Store, r: CitizenReport, road: str | None = None) -> Incident:
    # fixed weight: an unauthenticated reporter id cannot buy extra trust
    sig = Signal(source="report", type=r.type, lat=r.lat, lon=r.lon,
                 weight=config.SOURCE_WEIGHTS["report"], source_id=r.reporter_id, note=r.note, simulated=r.simulated)
    # only a flood report is named after a flood-watch point; an accident 150 m away must not be titled "Demo hotspot A"
    place = (_hotspot_name_near(store, r.lat, r.lon) if r.type == "flood" else None) or road
    hs = _hotspot_id_near(store, r.lat, r.lon) if r.type == "flood" else None
    return ingest(store, sig, place=place, hotspot_id=hs)


def _hotspot_near(store: Store, lat, lon, radius=200):
    return next((h for h in store.hotspots.values() if haversine_m(h.lat, h.lon, lat, lon) <= radius), None)


def _hotspot_name_near(store, lat, lon):
    h = _hotspot_near(store, lat, lon)
    return h.name if h else None


def _hotspot_id_near(store, lat, lon):
    h = _hotspot_near(store, lat, lon)
    return h.id if h else None


def reward_reporters(store: Store, inc: Incident, confirmed: bool):
    """Track reporter history for officers. Informational only: it does NOT change report weight,
    because reporter ids are client-supplied."""
    for s in inc.signals:
        if s.source == "report" and s.source_id:
            t = store.reporter_trust[s.source_id]
            store.reporter_trust[s.source_id] = min(1.7, t + 0.1) if confirmed else max(0.3, t - 0.3)


# ---------------- speed probes ----------------

def handle_probe(store: Store, rg: RoadGraph, p: ProbeSample) -> dict:
    """Record a speed sample. The old per-edge samples are kept for routing and recovery checks; whether this is a
    slowdown is decided by probe_rules (many vehicles, per road link, normal for this time, must persist)."""
    ts = p.ts or now()
    edge = rg.nearest_edge(p.lat, p.lon, max_m=40)
    if edge is None:
        return {"ok": True, "edge": None}
    with store.lock:
        store.probes[edge].append((ts, p.speed_kph, p.vehicle_id))
        res = probe_rules.update(store, rg, edge, p.vehicle_id, p.lat, p.lon, p.speed_kph, ts)
        link = res["link"]
        status = "unknown" if res["status"] == "insufficient" else res["status"]
        out = {"ok": True, "edge": list(edge), "status": status, "vehicles": res.get("n"),
               "median_kph": res.get("median"), "normal_kph": res.get("baseline")}
        if res["status"] == "anomaly" and res["sustained"] and ts - store.probe_last_signal.get(link.key, 0) > 60:
            store.probe_last_signal[link.key] = ts
            sig = Signal(source="probe", type="slowdown", lat=res["lat"], lon=res["lon"], ts=ts,
                         weight=config.SOURCE_WEIGHTS["probe"], source_id=f"link:{link.key[0]}-{link.key[1]}",
                         note=f"{res['n']} vehicles, median {res['median']} km/h vs normal {res['baseline']}",
                         simulated=p.simulated)
            inc = ingest(store, sig, place=rg.g.edges[edge].get("name") or None)
            out["incident"] = inc.id
        return out


def _recent(store: Store, edge, ts):
    return [s for s in store.probes.get(edge, ()) if ts - s[0] <= config.PROBE_WINDOW_S]


def _edge_status(store: Store, rg: RoadGraph, edge, ts) -> str:
    recent = _recent(store, edge, ts)
    if len({v for _, _, v in recent}) < config.PROBE_MIN_SAMPLES:
        return "unknown"
    med = statistics.median(s for _, s, _ in recent)
    normal = rg.g.edges[edge]["speed_kph"]
    if med < config.PROBE_DROP_RATIO * normal:
        return "anomaly"
    if med >= config.PROBE_RECOVER_RATIO * normal:
        return "normal"
    return "slow"


def observed_speed(store: Store, edge, ts) -> float | None:
    recent = _recent(store, edge, ts)
    if len(recent) < 2:
        return None
    return statistics.median(s for _, s, _ in recent)


def traffic_recovered_near(store: Store, rg: RoadGraph, inc: Incident, ts) -> bool | None:
    """True if probes near the incident are back to normal, False if still slow, None if no data."""
    edges = rg.edges_near(inc.lat, inc.lon, config.INCIDENT_EDGE_RADIUS_M)
    states = [_edge_status(store, rg, e, ts) for e in edges]
    known = [s for s in states if s != "unknown"]
    if not known:
        return None
    return all(s == "normal" for s in known)


# ---------------- planned works ----------------

def handle_planned(store: Store, w: PlannedWork) -> Incident:
    with store.lock:
        inc = Incident(type="construction", lat=w.lat, lon=w.lon, starts=w.starts, ends=w.ends,
                       severity="Closed" if w.full_closure else "Medium",
                       title=f"Construction · {w.title} ({w.agency})")
        inc.signals.append(Signal(source="planned", type="construction", lat=w.lat, lon=w.lon,
                                  weight=1.0, source_id=w.agency, note=w.title, simulated=w.simulated))
        refresh(store, inc)
        inc.status = "Reported"
        inc.status_times["Reported"] = now()
        store.incidents[inc.id] = inc
        store.log(now(), f"Planned works registered: {inc.title}")
        return inc


def is_active(inc: Incident, ts: float) -> bool:
    if inc.status == "Cleared":
        return False
    if inc.starts and ts < inc.starts:
        return False
    return True


def is_published(inc: Incident, ts: float) -> bool:
    """Shown to commuters and used in routing only after verification (trusted evidence or an officer)."""
    return is_active(inc, ts) and inc.status in ("Verified", "Responding")


# ---------------- lifecycle ----------------

def tick(store: Store, rg: RoadGraph, ts: float | None = None):
    """Advance every incident: planned windows, auto-clear, expiry."""
    ts = ts or now()
    with store.lock:
        for inc in list(store.incidents.values()):
            if inc.status == "Cleared":
                continue
            if inc.type == "construction":
                if inc.ends and ts >= inc.ends:
                    set_status(store, inc, "Cleared", ts, "planned window ended")
                elif inc.starts and ts >= inc.starts and inc.status == "Reported":
                    set_status(store, inc, "Verified", ts)
                continue

            recovered = traffic_recovered_near(store, rg, inc, ts)
            water_ok = True
            if inc.type == "flood":
                h = store.hotspots.get(inc.hotspot_id) if inc.hotspot_id else None
                water_ok = (h.depth_cm < config.DEPTH_LOW) if h and h.last_reading else False
                if not h or not h.last_reading:
                    water_ok = recovered is True  # report-only flood: rely on traffic
            road_ok = water_ok and recovered is not False
            if inc.type != "flood" and recovered is None:
                road_ok = False  # no evidence either way; wait for expiry or officer

            if road_ok:
                if inc.clear_candidate_since is None:
                    inc.clear_candidate_since = ts
                    store.log(ts, f"{inc.title}: conditions back to normal, will auto-clear in {int(config.CLEAR_WINDOW_S)} s")
                elif ts - inc.clear_candidate_since >= config.CLEAR_WINDOW_S:
                    reason = "water below 10 cm and traffic normal" if inc.type == "flood" else "traffic back to normal"
                    set_status(store, inc, "Cleared", ts, f"auto-clear: {reason}")
            else:
                inc.clear_candidate_since = None

            last = max((s.ts for s in inc.signals), default=inc.created)
            if inc.status != "Cleared" and ts - last > config.REPORT_EXPIRY_S:
                set_status(store, inc, "Cleared", ts, "expired: no new signals")
