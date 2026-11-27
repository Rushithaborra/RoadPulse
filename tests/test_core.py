"""Unit tests for the domain rules (no HTTP)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.forecast import countdown  # noqa: E402
from app.fusion import confidence  # noqa: E402
from app.models import Signal  # noqa: E402
from app.roadgraph import RoadGraph  # noqa: E402
from app.routing import incident_effects, route_alternatives  # noqa: E402
from app.severity import blocked_vehicles, depth_to_severity, edge_multiplier  # noqa: E402
from app.store import HotspotState  # noqa: E402


def sig(source, w, sid="x"):
    return Signal(source=source, type="flood", lat=0, lon=0, weight=w, source_id=sid)


def test_confidence_combines_independent_sources():
    assert confidence([sig("sensor", 0.8)]) == 0.8
    assert confidence([sig("sensor", 0.8), sig("report", 0.35, "u1")]) == round(1 - 0.2 * 0.65, 3)


def test_confidence_counts_same_source_once():
    # the same sensor sending 10 readings must not inflate confidence
    assert confidence([sig("sensor", 0.8, "s1")] * 10) == 0.8


def test_depth_severity_thresholds():
    assert depth_to_severity(5) == "Low"
    assert depth_to_severity(15) == "Medium"
    assert depth_to_severity(35) == "Severe"
    assert depth_to_severity(50) == "Closed"


def test_vehicle_aware_blocking():
    blocked = blocked_vehicles("flood", "Severe", 35)
    assert "two_wheeler" in blocked and "car" in blocked
    assert "bus" not in blocked
    assert edge_multiplier("flood", "Severe", 35, "two_wheeler") is None
    assert edge_multiplier("flood", "Severe", 35, "bus") > 1


def test_closed_blocks_everyone():
    assert set(blocked_vehicles("blockage", "Closed", None)) >= {"bus", "car", "two_wheeler"}


def test_countdown_rises_with_rain_and_not_without():
    h = HotspotState(id="h", name="H", sensor_id="s", lat=0, lon=0, k_rain_cm_per_mm=1.0, drain_cm_per_hr=10)
    wet = countdown(h, 40)  # +30 cm/h
    assert wet["eta_min"]["Low"] == 20 and wet["eta_min"]["Medium"] == 60
    dry = countdown(h, 5)
    assert dry["eta_min"]["Low"] is None


def test_routing_avoids_flood_for_bike_but_not_bus():
    from app.models import Incident
    rg = RoadGraph.load()
    lat, lon = rg.node_latlon("n0_0")
    lat2, lon2 = rg.node_latlon("n0_1")
    inc = Incident(type="flood", lat=(lat + lat2) / 2, lon=(lon + lon2) / 2, severity="Severe", depth_cm=35)
    fx = incident_effects(rg, [inc])
    bike = route_alternatives(rg, "n0_-3", "n0_3", "two_wheeler", fx)[0]
    bus = route_alternatives(rg, "n0_-3", "n0_3", "bus", fx)[0]
    flooded = {"n0_0", "n0_1"}
    def segments(route):
        nodes = [rg.nearest_node(a, b) for a, b in route["coords"]]
        return {frozenset(p) for p in zip(nodes, nodes[1:])}
    assert frozenset(flooded) not in segments(bike), "bike must not use the flooded segment"
    assert frozenset(flooded) in segments(bus), "bus may still wade through at 35 cm"
    assert edge_multiplier("flood", "Severe", 35, "bus") is not None


def test_alternatives_share_sums_to_one():
    rg = RoadGraph.load()
    routes = route_alternatives(rg, "n-4_-4", "n4_4", "car", {})
    assert 1 <= len(routes) <= 3
    assert abs(sum(r["share"] for r in routes) - 1) < 0.02
