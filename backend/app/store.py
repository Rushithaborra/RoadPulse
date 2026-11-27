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
    history: deque = field(default_factory=lambda: deque(maxlen=500))  # (ts, depth)


class Store:
    def __init__(self):
        self.lock = threading.RLock()
        self.incidents: dict[str, Incident] = {}
        self.hotspots: dict[str, HotspotState] = {}
        for h in config.HOTSPOTS:
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

    def hotspot_by_sensor(self, sensor_id: str) -> HotspotState | None:
        return next((h for h in self.hotspots.values() if h.sensor_id == sensor_id), None)

    def log(self, ts: float, text: str):
        self.events.appendleft({"ts": ts, "text": text})

    def reset(self):
        with self.lock:
            self.__init__()


store = Store()
