"""In-memory state for the demo. Swap for PostgreSQL/PostGIS after the hackathon; the interface stays the same."""
from __future__ import annotations

import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field

from . import config
from .geo import offset
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

    def place_demo_hotspots(self, rg) -> None:
        """Move SIMULATED demo hotspots onto the midpoint of a real two-way road segment near their target, so the
        demo works on any loaded map. Surveyed hotspots from data/hotspots.json are never moved."""
        if self.hotspot_source != "synthetic":
            return
        from .roadgraph import HIGHWAY_RANK
        for h in self.hotspots.values():
            cands = [(u, v) for u, v in rg.edges_near(h.lat, h.lon, 400) if rg.g.has_edge(v, u)]
            if not cands:
                continue

            def score(e):
                (alat, alon), (blat, blon) = rg.node_latlon(e[0]), rg.node_latlon(e[1])
                d = ((alat + blat) / 2 - h.lat) ** 2 + ((alon + blon) / 2 - h.lon) ** 2
                major = HIGHWAY_RANK.get(rg.g.edges[e].get("highway"), 0) >= HIGHWAY_RANK["tertiary"]
                return (not major, d)
            u, v = min(cands, key=score)
            (alat, alon), (blat, blon) = rg.node_latlon(u), rg.node_latlon(v)
            h.lat, h.lon = (alat + blat) / 2, (alon + blon) / 2
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


store = Store()
