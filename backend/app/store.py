"""In-memory state for the demo. Swap for PostgreSQL/PostGIS after the hackathon; the interface stays the same."""
from __future__ import annotations

import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field

from . import config
from .geo import haversine_m, offset
from .models import Incident


@dataclass
class HotspotState:
    id: str
    name: str
    sensor_id: str
    lat: float
    lon: float
    k_rain_cm_per_mm: float
    drain_cm_per_hr: float
    depth_cm: float = 0.0
    last_reading: float | None = None
    above_count: int = 0
    simulated_location: bool = False  # demo point, not a surveyed flood location
    last_simulated: bool = False
    history: deque = field(default_factory=lambda: deque(maxlen=500))  # (ts, depth)


class Store:
    def __init__(self):
        self.lock = threading.RLock()
        self.incidents: dict[str, Incident] = {}
        self.hotspots: dict[str, HotspotState] = {}
        items, self.hotspot_source = config.load_hotspots()
        for h in items:
            if "lat" in h:
                lat, lon = float(h["lat"]), float(h["lon"])
            else:
                lat, lon = offset(config.CENTER_LAT, config.CENTER_LON, h["east_m"], h["north_m"])
            self.hotspots[h["id"]] = HotspotState(
                id=h["id"], name=h["name"], sensor_id=h["sensor_id"], lat=lat, lon=lon,
                k_rain_cm_per_mm=h["k_rain_cm_per_mm"], drain_cm_per_hr=h["drain_cm_per_hr"],
            )
        # edge (u, v) -> deque of (ts, speed_kph, vehicle_id)
        self.probes: dict[tuple, deque] = defaultdict(lambda: deque(maxlen=200))
        self.probe_last_signal: dict[tuple, float] = {}
        self.reporter_trust: dict[str, float] = defaultdict(lambda: 1.0)
        self.rain_override: float | None = None
        self.events: deque = deque(maxlen=200)  # activity log for the dashboard
        # speed-probe rule v2 state, keyed by road LINK (see probe_rules.py)
        self.link_samples: dict[tuple, deque] = defaultdict(lambda: deque(maxlen=600))
        self.link_anomaly_since: dict[tuple, float] = {}
        self.link_state: dict[tuple, str] = {}
        self.baseline: dict[tuple, tuple[float, int]] = {}   # (link, hour-of-week) -> (normal speed km/h, updates)
        # alert delivery (see alerts.py)
        self.units: dict[str, dict] = {}                      # police unit -> {lat, lon, ts}
        self.subscribers: dict = {}
        self.alert_log: deque = deque(maxlen=300)             # redacted: no names, no coordinates

    def place_demo_hotspots(self, rg) -> None:
        """Move SIMULATED demo hotspots onto the midpoint of a real road segment near their target, so the demo
        works on any loaded map. Surveyed hotspots from data/hotspots.json are never moved.

        Choice (see demo_site_tier): a NAMED two-way road whose flooding forces a real diversion for two-wheelers
        and whose ends are proper junctions comes first; each rule is relaxed in turn, and the last resort is an
        unnamed two-way road, shown as "unnamed road". Nothing here claims the place floods in real life: the
        point stays flagged simulated_location and is labelled SIMULATED in the UI."""
        if self.hotspot_source != "synthetic":
            return
        taken: list[tuple[float, float]] = []
        for h in self.hotspots.values():
            best = None  # (tier, distance_m, (u, v))
            for radius in (400, 900):  # nearest to the target first; widen only if nothing ideal is close
                seen = set()
                for u, v in rg.edges_near(h.lat, h.lon, radius):
                    if not rg.g.has_edge(v, u) or frozenset((u, v)) in seen:  # two-way roads only, each once
                        continue
                    seen.add(frozenset((u, v)))
                    mid = _midpoint(rg, u, v)
                    if any(haversine_m(*mid, *t) < 150 for t in taken):
                        continue  # keep demo hotspots apart
                    key = (demo_site_tier(rg, u, v), haversine_m(h.lat, h.lon, *mid))
                    if best is None or key < best[:2]:
                        best = (*key, (u, v))
                if best and best[0] == IDEAL_SITE:
                    break
            if best is None:
                continue
            u, v = best[2]
            h.lat, h.lon = _midpoint(rg, u, v)
            taken.append((h.lat, h.lon))
            road = rg.g.edges[u, v].get("name") or "unnamed road"
            base = h.name.split(" · ")[0]
            h.name = base if rg.mode == "synthetic" else f"{base} · {road}"
            h.simulated_location = True

    def snapshot(self) -> list[Incident]:
        """Copy of the incident list taken under the lock, safe to iterate from any thread."""
        with self.lock:
            return list(self.incidents.values())

    def hotspot_by_sensor(self, sensor_id: str) -> HotspotState | None:
        return next((h for h in self.hotspots.values() if h.sensor_id == sensor_id), None)

    def log(self, ts: float, text: str):
        self.events.appendleft({"ts": ts, "text": text})

    def reset(self):
        with self.lock:
            self.__init__()


IDEAL_SITE = (False, False, False)   # named, diversion exists, both ends are real junctions
MIN_SITE_M = 60                      # shorter than the 50 m incident radius would spill onto neighbouring roads
MAX_DIVERSION_M = 800                # a detour longer than this is not a useful demo


def _midpoint(rg, u, v) -> tuple[float, float]:
    (alat, alon), (blat, blon) = rg.node_latlon(u), rg.node_latlon(v)
    return (alat + blat) / 2, (alon + blon) / 2


def demo_diversion(rg, u, v) -> tuple[float, float] | None:
    """Length in metres of the shortest legal route u->v and v->u for a TWO-WHEELER when a severe waterlogging
    incident sits at the middle of the road, or None if either direction has no route within MAX_DIVERSION_M.
    Uses the same incident rules as routing (edges within INCIDENT_EDGE_RADIUS_M, vehicle depth limits), so the
    result is what a commuter would really get."""
    import networkx as nx
    from .routing import edge_time, incident_effects
    inc = Incident(type="flood", lat=_midpoint(rg, u, v)[0], lon=_midpoint(rg, u, v)[1], severity="Severe",
                   depth_cm=35)
    fx = incident_effects(rg, [inc])
    blocked = {e for e in fx if edge_time(rg, e, "two_wheeler", fx) is None}

    def weight(a, b, d):
        return None if (a, b) in blocked else d["length"]
    out = []
    for a, b in ((u, v), (v, u)):
        try:
            out.append(nx.single_source_dijkstra(rg.g, a, target=b, cutoff=MAX_DIVERSION_M, weight=weight)[0])
        except nx.NetworkXNoPath:
            return None
    return tuple(out)


def demo_site_tier(rg, u, v) -> tuple[bool, bool, bool]:
    """Sort key for a candidate demo site, lower is better: (unnamed, no usable diversion, a dead-end side)."""
    d = rg.g.edges[u, v]
    named = bool(d.get("name"))
    length = d.get("length", 0)
    div = demo_diversion(rg, u, v) if length >= MIN_SITE_M else None
    viable = div is not None and min(div) >= 1.3 * length  # a real detour, not the same road again
    ends = _junction_degree(rg, u, v) >= 3 and _junction_degree(rg, v, u) >= 3
    return (not named, not viable, not ends)


def _junction_degree(rg, n, came_from) -> int:
    """How many roads meet at the first real junction reached by leaving `came_from` through `n`. Curve points that
    only shape a road (degree 2) are walked through, so a segment between two curve points still counts as part
    of the street that links to real junctions at its ends."""
    seen = {came_from, n}
    while True:
        around = set(rg.g.successors(n)) | set(rg.g.predecessors(n))
        if len(around) != 2:
            return len(around)  # a real junction (3+) or a dead end (1)
        nxt = next(iter(around - seen), None)
        if nxt is None:
            return 2  # a closed loop of curve points
        seen.add(nxt)
        n = nxt


store = Store()
