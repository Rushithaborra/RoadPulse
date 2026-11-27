"""Severity rules and vehicle-aware passability."""
from .config import DEPTH_LOW, DEPTH_MEDIUM, DEPTH_SEVERE, VEHICLE_SAFE_DEPTH


def depth_to_severity(depth_cm: float) -> str:
    if depth_cm < DEPTH_LOW:
        return "Low"
    if depth_cm < DEPTH_MEDIUM:
        return "Medium"
    if depth_cm < DEPTH_SEVERE:
        return "Severe"
    return "Closed"


DEFAULT_SEVERITY = {
    "accident": "Severe",
    "blockage": "Closed",
    "slowdown": "Medium",
    "construction": "Closed",
    "flood": "Medium",  # used only until a depth is known
}

# Travel-time multiplier on affected roads when the road is still passable.
SEVERITY_MULTIPLIER = {"Low": 1.3, "Medium": 2.0, "Severe": 4.0, "Closed": None}  # None = impassable


def blocked_vehicles(incident_type: str, severity: str, depth_cm: float | None) -> list[str]:
    """Vehicle classes that should not use the affected road."""
    if incident_type == "flood" and depth_cm is not None:
        return [v for v, safe in VEHICLE_SAFE_DEPTH.items() if depth_cm > safe]
    if severity == "Closed":
        return list(VEHICLE_SAFE_DEPTH.keys())
    return []


def edge_multiplier(incident_type: str, severity: str, depth_cm: float | None, vehicle: str) -> float | None:
    """Return a travel-time multiplier for this vehicle, or None if it must avoid the road."""
    if vehicle in blocked_vehicles(incident_type, severity, depth_cm):
        return None
    if incident_type == "flood" and depth_cm is not None:
        safe = VEHICLE_SAFE_DEPTH[vehicle]
        if depth_cm <= 2:
            return 1.0
        # passable but slow: wading slows traffic more as depth approaches the safe limit
        return 1.0 + 1.5 * (depth_cm / safe)
    m = SEVERITY_MULTIPLIER.get(severity)
    return m
