"""Central configuration. Override any value with an environment variable of the same name."""
import os


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


# Map centre. Default is approximate (Tekkali, Srikakulam). Replace with your exact area.
CENTER_LAT = _f("CENTER_LAT", 18.6066)
CENTER_LON = _f("CENTER_LON", 84.2290)

# Optional real road network exported with tools/fetch_map.py (osmnx). If missing, a synthetic grid is used.
GRAPHML_PATH = os.getenv("GRAPHML_PATH", os.path.join(os.path.dirname(__file__), "..", "..", "data", "area.graphml"))

# Fusion
CLUSTER_RADIUS_M = _f("CLUSTER_RADIUS_M", 300)
CLUSTER_WINDOW_S = _f("CLUSTER_WINDOW_S", 15 * 60)
PUBLISH_CONFIDENCE = _f("PUBLISH_CONFIDENCE", 0.7)   # shown to commuters + used in routing
REVIEW_CONFIDENCE = _f("REVIEW_CONFIDENCE", 0.4)     # shown to officers as "unverified"

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

# How far an incident affects nearby road segments (m)
INCIDENT_EDGE_RADIUS_M = _f("INCIDENT_EDGE_RADIUS_M", 50)

# Flood hotspots. Offsets are metres east/north of the map centre; replace with real coordinates.
HOTSPOTS = [
    {
        "id": "hs-underpass",
        "name": "Railway underpass",
        "sensor_id": "sensor-01",
        "east_m": 0, "north_m": 75,   # on the main road
        "k_rain_cm_per_mm": 0.9,   # depth rise per mm of rain per hour, per hour
        "drain_cm_per_hr": 12.0,   # what the drains remove per hour
    },
    {
        "id": "hs-market",
        "name": "Market road dip",
        "sensor_id": "sensor-02",
        "east_m": -300, "north_m": -225,
        "k_rain_cm_per_mm": 0.5,
        "drain_cm_per_hr": 8.0,
    },
]

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
