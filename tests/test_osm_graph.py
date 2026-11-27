"""Real-map handling: OSMnx GraphML loading (directed, parallel edges, speeds, curves, components) and exact
road-segment routing with temporary split nodes. The graphs here are small hand-built test fixtures in OSMnx's
file format, not real map data."""
import math

import networkx as nx
import pytest

from app.geo import haversine_m, offset
from app.roadgraph import RoadGraph, _edge_attrs, _from_graphml, parse_lanes, parse_maxspeed
from app.routing import graph_with_endpoints, route_alternatives

LAT0, LON0 = 17.72867, 83.308634


def P(e, n):
    return offset(LAT0, LON0, e, n)


def write_osmnx_graph(tmp_path, edges, nodes):
    """edges: (u, v, attrs). Written like OSMnx: MultiDiGraph, x/y node attrs, list values as strings."""
    g = nx.MultiDiGraph()
    for n, (e, nm) in nodes.items():
        lat, lon = P(e, nm)
        g.add_node(n, x=lon, y=lat)
    for u, v, a in edges:
        g.add_edge(u, v, **a)
    path = tmp_path / "area.graphml"
    nx.write_graphml(g, path)
    return str(path)


def straight(u, v, nodes):
    (a, b), (c, d) = nodes[u], nodes[v]
    return math.hypot(c - a, d - b)


@pytest.fixture()
def osm_graph(tmp_path):
    nodes = {"A": (0, 0), "B": (0, 300), "C": (300, 300), "D": (300, 0), "H": (0, 450),
             "E": (600, 300), "F": (5000, 5000), "G": (5100, 5000)}
    curve = f"LINESTRING ({P(300, 300)[1]} {P(300, 300)[0]}, {P(450, 380)[1]} {P(450, 380)[0]}, {P(600, 300)[1]} {P(600, 300)[0]})"
    curve_rev = f"LINESTRING ({P(600, 300)[1]} {P(600, 300)[0]}, {P(450, 380)[1]} {P(450, 380)[0]}, {P(300, 300)[1]} {P(300, 300)[0]})"
    edges = [
        ("A", "B", {"highway": "secondary", "name": "One Way St", "length": 300.0, "oneway": True}),  # one-way
        ("B", "C", {"highway": "residential", "name": "Two Way Rd", "length": 300.0, "maxspeed": "IN:urban"}),
        ("C", "B", {"highway": "residential", "name": "Two Way Rd", "length": 300.0, "maxspeed": "IN:urban"}),
        ("C", "D", {"highway": "residential", "name": "Side St", "length": 300.0}),
        ("D", "C", {"highway": "residential", "name": "Side St", "length": 300.0}),
        ("D", "A", {"highway": "residential", "name": "Back Lane", "length": 300.0}),
        ("A", "D", {"highway": "residential", "name": "Back Lane", "length": 300.0}),
        # parallel A->D: shorter but slow service road vs. the faster primary with maxspeed
        ("A", "D", {"highway": "service", "name": "Service Road", "length": 290.0}),
        ("A", "D", {"highway": "primary", "name": "Fast Road", "length": 310.0, "maxspeed": "50",
                    "lanes": "['2', '3']"}),
        ("B", "H", {"highway": "residential", "name": "Dead End", "length": 150.0, "oneway": True}),  # dead end
        ("C", "E", {"highway": "tertiary", "name": "Curve Rd", "length": 340.0, "geometry": curve}),
        ("E", "C", {"highway": "tertiary", "name": "Curve Rd", "length": 340.0, "geometry": curve_rev}),
        ("F", "G", {"highway": "residential", "name": "Island", "length": 100.0}),  # disconnected fragment
        ("G", "F", {"highway": "residential", "name": "Island", "length": 100.0}),
    ]
    return _from_graphml(write_osmnx_graph(tmp_path, edges, nodes))


def test_one_way_roads_keep_a_single_direction(osm_graph):
    assert osm_graph.has_edge("A", "B") and not osm_graph.has_edge("B", "A")
    assert osm_graph.has_edge("B", "C") and osm_graph.has_edge("C", "B")


def test_parallel_edges_keep_the_fastest_by_travel_time(osm_graph):
    d = osm_graph.edges["A", "D"]
    assert d["name"] == "Fast Road" and d["speed_kph"] == 50 and d["speed_source"] == "osm_maxspeed"
    assert d["lanes"] == 2 and d["highway"] == "primary" and d["length"] == pytest.approx(310.0)


def test_unparseable_maxspeed_falls_back_to_labelled_estimate(osm_graph):
    d = osm_graph.edges["B", "C"]
    assert d["speed_source"] == "estimate" and d["speed_kph"] == 25


def test_weak_component_keeps_one_way_dead_end_and_drops_fragment(osm_graph):
    assert "H" in osm_graph, "a one-way dead end is part of the road network"
    assert "F" not in osm_graph and "G" not in osm_graph


def test_curved_road_follows_geometry_and_keeps_length(osm_graph):
    shape = [n for n, d in osm_graph.nodes(data=True) if d.get("shape")]
    assert len(shape) == 1, "both directions of a two-way curved road share the intermediate point"
    s = shape[0]
    assert osm_graph.has_edge("C", s) and osm_graph.has_edge(s, "E")
    assert osm_graph.has_edge("E", s) and osm_graph.has_edge(s, "C")
    total = osm_graph.edges["C", s]["length"] + osm_graph.edges[s, "E"]["length"]
    assert total == pytest.approx(340.0), "OSM's measured length is preserved"


def test_parsers():
    assert parse_maxspeed("50") == 50 and parse_maxspeed("30 mph") == pytest.approx(48.27)
    assert parse_maxspeed("IN:urban") is None and parse_maxspeed(None) is None
    assert parse_maxspeed("40;60") == 40
    assert parse_lanes("['2', '4']") == 2 and parse_lanes(None) == 1


# ---------------- exact road-segment routing ----------------

def long_loop(one_way_ab: bool) -> RoadGraph:
    """A 2 km road A-B plus a loop back via D and C (600 m east)."""
    g = nx.DiGraph()
    for n, (e, nm) in {"A": (0, 0), "B": (0, 2000), "C": (600, 0), "D": (600, 2000)}.items():
        lat, lon = P(e, nm)
        g.add_node(n, lat=lat, lon=lon)

    def road(u, v, length, both=True):
        g.add_edge(u, v, **_edge_attrs(length, 40, 1, f"{u}{v} Road", "secondary"))
        if both:
            g.add_edge(v, u, **_edge_attrs(length, 40, 1, f"{u}{v} Road", "secondary"))
    road("A", "B", 2000, both=not one_way_ab)
    road("B", "D", 600)
    road("D", "C", 2000)
    road("C", "A", 600)
    return RoadGraph(g, "test-loop")


def _route(rg, a, b, vehicle="car"):
    g, src, dst, sp, dp = graph_with_endpoints(rg, *a, *b)
    routes = route_alternatives(rg, src, dst, vehicle, {}, g=g)
    return routes, sp, dp


def test_route_between_points_on_the_same_long_road_uses_the_road_itself():
    rg = long_loop(one_way_ab=False)
    routes, sp, dp = _route(rg, P(0, 800), P(0, 1200))
    assert routes[0]["length_m"] == pytest.approx(400, abs=2), "not via the junctions 800 m away"
    assert haversine_m(*routes[0]["coords"][0], *P(0, 800)) < 1
    assert haversine_m(*routes[0]["coords"][-1], *P(0, 1200)) < 1


def test_one_way_road_is_only_used_in_its_direction():
    rg = long_loop(one_way_ab=True)
    fwd, _, _ = _route(rg, P(0, 800), P(0, 1200))
    assert fwd[0]["length_m"] == pytest.approx(400, abs=2)
    back, _, _ = _route(rg, P(0, 1200), P(0, 800))
    # cannot drive 400 m backwards on the one-way road: must go on to B and round the loop
    assert back[0]["length_m"] == pytest.approx(800 + 600 + 2000 + 600 + 800, abs=5)


def test_travel_time_is_split_in_proportion():
    rg = long_loop(one_way_ab=False)
    routes, _, _ = _route(rg, P(0, 500), P(0, 1500))
    assert routes[0]["time_s"] == pytest.approx(1000 / (40 / 3.6), abs=1)


def test_permanent_graph_is_never_modified():
    rg = long_loop(one_way_ab=True)
    before = (rg.g.number_of_nodes(), rg.g.number_of_edges(), dict(rg.g.edges["A", "B"]))
    _route(rg, P(0, 800), P(0, 1200))
    _route(rg, P(0, 1200), P(600, 300))
    assert (rg.g.number_of_nodes(), rg.g.number_of_edges(), dict(rg.g.edges["A", "B"])) == before
    assert not any(d.get("virtual") for _, d in rg.g.nodes(data=True))


def test_incident_on_split_road_still_blocks_the_vehicle():
    from app.models import Incident
    from app.routing import incident_effects
    rg = long_loop(one_way_ab=False)
    inc = Incident(type="flood", lat=P(0, 1000)[0], lon=P(0, 1000)[1], severity="Severe", depth_cm=35)
    fx = incident_effects(rg, [inc])
    g, src, dst, _, _ = graph_with_endpoints(rg, *P(0, 800), *P(0, 1200))
    bike = route_alternatives(rg, src, dst, "two_wheeler", fx, g=g)
    bus = route_alternatives(rg, src, dst, "bus", fx, g=g)
    assert bike[0]["length_m"] > 3000, "two-wheeler must go round the loop"
    assert bus[0]["length_m"] == pytest.approx(400, abs=2), "bus may wade through"


def test_api_route_starts_on_the_road_not_at_a_junction(client):
    from app.main import S
    old = S.rg
    S.rg = long_loop(one_way_ab=False)
    try:
        r = client.post("/api/routes", json={"from_lat": P(0, 800)[0], "from_lon": P(0, 800)[1],
                                             "to_lat": P(0, 1200)[0], "to_lon": P(0, 1200)[1]}).json()
        assert r["routing"] == "road-segment"
        assert r["routes"][0]["length_m"] == pytest.approx(400, abs=2)
        assert haversine_m(*r["snapped"]["start"], *P(0, 800)) < 1
    finally:
        S.rg = old


def test_rider_can_leave_a_long_flooded_road_the_way_they_came():
    from app.models import Incident
    from app.routing import incident_effects
    rg = long_loop(one_way_ab=False)
    inc = Incident(type="flood", lat=P(0, 1000)[0], lon=P(0, 1000)[1], severity="Severe", depth_cm=35)
    fx = incident_effects(rg, [inc])
    # start at 800 m (south of the flood), go to a point on the loop's far side: ride back south via A, not through
    g, src, dst, _, _ = graph_with_endpoints(rg, *P(0, 800), *P(600, 1000))
    bike = route_alternatives(rg, src, dst, "two_wheeler", fx, g=g)
    assert bike and bike[0]["length_m"] == pytest.approx(800 + 600 + 1000, abs=5)
