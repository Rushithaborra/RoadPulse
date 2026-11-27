"""Central configuration. Override any value with an environment variable of the same name."""
import os


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


# Demonstration area: Dwaraka Nagar, Visakhapatnam, Andhra Pradesh (17.728670 N, 83.308634 E).
# This is the centre used by tools/fetch_map.py for data/area.graphml and by the synthetic fallback grid.
# With a real OpenStreetMap graph loaded, the centre is taken from the graph's own extent.
CENTER_LAT = _f("CENTER_LAT", 17.728670)
CENTER_LON = _f("CENTER_LON", 83.308634)

# Optional real road network exported with tools/fetch_map.py (osmnx). If missing, a synthetic grid is used.
GRAPHML_PATH = os.getenv("GRAPHML_PATH", os.path.join(os.path.dirname(__file__), "..", "..", "data", "area.graphml"))

# Fusion
CLUSTER_RADIUS_M = _f("CLUSTER_RADIUS_M", 300)
CLUSTER_WINDOW_S = _f("CLUSTER_WINDOW_S", 15 * 60)
PUBLISH_CONFIDENCE = _f("PUBLISH_CONFIDENCE", 0.7)   # shown to commuters + used in routing
REVIEW_CONFIDENCE = _f("REVIEW_CONFIDENCE", 0.4)     # shown to officers as "unverified"

# Sources that authenticate (device key, officer token, agency entry). Public reports are NOT trusted:
# their reporter_id is client-supplied, so any number of reports counts as one untrusted source.
TRUSTED_SOURCES = {"sensor", "probe", "cctv", "planned", "officer"}

SOURCE_WEIGHTS = {
    "sensor": 0.8,
    "probe": 0.5,
    "cctv": 0.5,
    "report": 0.35,
    "planned": 1.0,
    "officer": 1.0,
}

# Speed-probe anomaly rule
PROBE_WINDOW_S = _f("PROBE_WINDOW_S", 5 * 60)
PROBE_MIN_SAMPLES = int(_f("PROBE_MIN_SAMPLES", 3))
PROBE_DROP_RATIO = _f("PROBE_DROP_RATIO", 0.4)       # median below 40% of normal speed = anomaly
PROBE_RECOVER_RATIO = _f("PROBE_RECOVER_RATIO", 0.8)

# Lifecycle
CLEAR_WINDOW_S = _f("CLEAR_WINDOW_S", 120)           # demo default; use 600 (10 min) in a real pilot
REPORT_EXPIRY_S = _f("REPORT_EXPIRY_S", 60 * 60)
TICK_S = _f("TICK_S", 5)

# Water-depth severity thresholds (cm). Starting defaults, to be tuned on pilot data.
DEPTH_LOW = 10
DEPTH_MEDIUM = 30
DEPTH_SEVERE = 45

# Max safe water depth per vehicle class (cm). Starting defaults.
VEHICLE_SAFE_DEPTH = {
    "two_wheeler": 10,
    "auto": 20,
    "car": 30,
    "ambulance": 30,
    "bus": 45,
}
VEHICLE_CLASSES = list(VEHICLE_SAFE_DEPTH.keys())

# A point further than this from the mapped road network is rejected (HTTP 422), never silently snapped.
MAX_SNAP_M = _f("MAX_SNAP_M", 250)

# How far an incident affects nearby road segments (m)
INCIDENT_EDGE_RADIUS_M = _f("INCIDENT_EDGE_RADIUS_M", 50)

# Flood hotspots.
# Real deployment: put surveyed locations in data/hotspots.json (see docs/hotspots.example.json); they are used as-is.
# Without that file, two SIMULATED demo hotspots are placed at the midpoint of a real two-way road segment near
# the offsets below (on whichever map is loaded). They are demonstration points, not known flood locations.
HOTSPOTS_PATH = os.getenv("HOTSPOTS_PATH", os.path.join(os.path.dirname(__file__), "..", "..", "data", "hotspots.json"))
SYNTHETIC_HOTSPOTS = [
    {
        "id": "hs-a", "name": "Demo hotspot A (simulated)", "sensor_id": "sensor-01",
        "east_m": 0, "north_m": 75,   # target offset from the centre; snapped onto a real road segment
        "k_rain_cm_per_mm": 0.9,      # depth rise per mm/h of rain, per hour (assumed)
        "drain_cm_per_hr": 12.0,      # what the drains remove per hour (assumed)
    },
    {
        "id": "hs-b", "name": "Demo hotspot B (simulated)", "sensor_id": "sensor-02",
        "east_m": -300, "north_m": -225,
        "k_rain_cm_per_mm": 0.5, "drain_cm_per_hr": 8.0,
    },
]


def load_hotspots() -> tuple[list[dict], str]:
    """Return (hotspots, source). Real file entries must give lat/lon; nothing is invented."""
    import json
    path = os.path.abspath(HOTSPOTS_PATH)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            items = json.load(f)
        for h in items:
            missing = {"id", "name", "sensor_id", "lat", "lon"} - set(h)
            if missing:
                raise ValueError(f"{path}: hotspot {h.get('id')} is missing {sorted(missing)}")
            h.setdefault("k_rain_cm_per_mm", 0.7)
            h.setdefault("drain_cm_per_hr", 10.0)
        return items, f"file:{os.path.basename(path)}"
    return SYNTHETIC_HOTSPOTS, "synthetic"


OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
