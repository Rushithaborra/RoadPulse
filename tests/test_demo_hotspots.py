"""Demo hotspot placement. The simulated hotspots must land on a NAMED two-way road whose flooding forces a real
diversion, never on a one-way road, and stay labelled simulated. Unit tests use small hand-built graphs; the
end-to-end tests run the real app on the exported OpenStreetMap graph (data/area.graphml), read-only."""
import os

import pytest
from conftest import DEV

from app import config
from app.geo import haversine_m, offset
from app.models import Incident
from app.roadgraph import RoadGraph, _edge_attrs, _from_graphml
from app.routing import incident_effects
from app.store import Store, demo_diversion, demo_site_tier

REAL_GRAPH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "area.graphml"))
LAT0, LON0 = 17.72867, 83.308634
needs_real_map = pytest.mark.skipif(not os.path.exists(REAL_GRAPH),
                                    reason="data/area.graphml not present (the app would use the synthetic grid)")


# ---------------- small hand-built graphs ----------------

def grid(named=True, one_way_edges=(), unnamed_edges=(), n=4, spacing=150.0) -> RoadGraph:
    """n x n street grid centred on the demo centre; a road is "(i,j)-(k,l)". Every road is two-way unless listed."""
    import networkx as nx
    g = nx.DiGraph()
    pos = {(i, j): offset(LAT0, LON0, (i - (n - 1) / 2) * spacing, (j - (n - 1) / 2) * spacing)
           for i in range(n) for j in range(n)}
    for k, (lat, lon) in pos.items():
        g.add_node(k, lat=lat, lon=lon)
    for (i, j) in pos:
        for (k, m) in ((i + 1, j), (i, j + 1)):
            if (k, m) not in pos:
                continue
            nm = f"Street {i}{j}-{k}{m}" if named and ((i, j), (k, m)) not in unnamed_edges else ""
            attrs = _edge_attrs(spacing, 25, 1, nm, "residential")
            g.add_edge((i, j), (k, m), **attrs)
            if ((i, j), (k, m)) not in one_way_edges:
                g.add_edge((k, m), (i, j), **attrs)
    return RoadGraph(g, "test-grid")


@pytest.fixture()
def centred(monkeypatch):
    monkeypatch.setattr(config, "CENTER_LAT", LAT0)
    monkeypatch.setattr(config, "CENTER_LON", LON0)


def place(rg) -> dict:
    s = Store()
    s.place_demo_hotspots(rg)
    return s.hotspots


def edge_of(rg, h):
    return rg.nearest_edge(h.lat, h.lon, 2)


MIDDLE = ((1, 2), (2, 2))  # the grid edge whose midpoint is exactly hotspot A's target (0 m east, 75 m north)


def test_named_road_beats_a_closer_unnamed_one(centred):
    rg = grid(unnamed_edges=(MIDDLE,))
    h = place(rg)["hs-a"]
    e = edge_of(rg, h)
    assert rg.g.edges[e]["name"], "the unnamed edge sits exactly on the target but a named road must win"
    assert h.name.startswith("Demo hotspot A (simulated)") and "unnamed" not in h.name
    assert h.simulated_location is True


def test_unnamed_fallback_is_clearly_labelled_when_no_road_has_a_name(centred):
    rg = grid(named=False)
    h = place(rg)["hs-a"]
    assert h.name == "Demo hotspot A (simulated) · unnamed road"
    assert h.simulated_location is True


def test_one_way_roads_are_never_chosen(centred):
    rg = grid(one_way_edges=(MIDDLE,))
    for h in place(rg).values():
        u, v = edge_of(rg, h)
        assert rg.g.has_edge(u, v) and rg.g.has_edge(v, u), "a one-way segment must not host a demo hotspot"
        assert {u, v} != set(MIDDLE)


def test_dead_end_without_diversion_loses_to_a_road_with_one(centred):
    import networkx as nx
    rg = grid()
    g = rg.g.copy()
    g.remove_edges_from([MIDDLE, MIDDLE[::-1]])  # open space where the target is, then a named dead end into it
    lat, lon = offset(LAT0, LON0, -5, 75)         # the closest road to the target, but there is no way round it
    g.add_node("spur_end", lat=lat, lon=lon)
    base = rg.node_latlon((1, 2))
    attrs = _edge_attrs(haversine_m(*base, lat, lon), 25, 1, "Dead End Lane", "residential")
    g.add_edge((1, 2), "spur_end", **attrs)
    g.add_edge("spur_end", (1, 2), **attrs)
    assert nx.is_weakly_connected(g)
    rg2 = RoadGraph(g, "test-grid-spur")
    spur = ((1, 2), "spur_end")
    assert demo_diversion(rg2, *spur) is None
    assert demo_site_tier(rg2, *spur)[1] is True      # flagged "no usable diversion"
    h = place(rg2)["hs-a"]
    assert rg2.g.edges[edge_of(rg2, h)]["name"] != "Dead End Lane"


def test_hotspots_are_kept_apart(centred):
    a, b = place(grid()).values()
    assert haversine_m(a.lat, a.lon, b.lat, b.lon) >= 150


def test_surveyed_hotspots_are_never_moved(centred):
    s = Store()
    s.hotspot_source = "file:hotspots.json"
    before = [(h.lat, h.lon, h.name) for h in s.hotspots.values()]
    s.place_demo_hotspots(grid())
    assert [(h.lat, h.lon, h.name) for h in s.hotspots.values()] == before
    assert not any(h.simulated_location for h in s.hotspots.values())


# ---------------- the real OpenStreetMap graph ----------------

@pytest.fixture(scope="module")
def real_rg():
    return RoadGraph(_from_graphml(REAL_GRAPH), "osm:area.graphml")


@pytest.fixture()
def osm_client(tmp_path, monkeypatch):
    """The whole app started on the real exported map, exactly as in the demo."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(config, "GRAPHML_PATH", REAL_GRAPH)
    monkeypatch.setattr(config, "CENTER_LAT", config.CENTER_LAT)  # restored afterwards: the OSM start-up moves them
    monkeypatch.setattr(config, "CENTER_LON", config.CENTER_LON)
    from fastapi.testclient import TestClient
    from app.main import S, app
    from app.store import store
    S.saved = {}
    store.reset()
    with TestClient(app) as c:
        assert S.rg.mode == "osm"
        yield c
    store.reset()


def hotspots(client):
    return {h["sensor_id"]: h for h in client.get("/api/hotspots").json()["hotspots"]}


@needs_real_map
def test_real_map_default_hotspot_prefers_a_named_two_way_road(osm_client):
    from app.main import S
    hs = hotspots(osm_client)
    assert set(hs) == {"sensor-01", "sensor-02"}
    for h in hs.values():
        assert h["simulated_location"] is True
        assert h["name"].startswith("Demo hotspot") and "(simulated)" in h["name"]
        assert "unnamed road" not in h["name"], h["name"]
        u, v = S.rg.nearest_edge(h["lat"], h["lon"], 2)
        assert S.rg.g.has_edge(u, v) and S.rg.g.has_edge(v, u), "two-way road"
        assert S.rg.g.edges[u, v]["name"]
        assert S.rg.g.edges[u, v]["length"] >= 60


@needs_real_map
def test_real_map_sensor_coordinates_lie_on_the_mapped_road(osm_client):
    from app.main import S
    for h in hotspots(osm_client).values():
        assert S.rg.distance_to_network(h["lat"], h["lon"]) < 1.0
        assert S.rg.bbox[0] <= h["lat"] <= S.rg.bbox[2] and S.rg.bbox[1] <= h["lon"] <= S.rg.bbox[3]


@needs_real_map
def test_real_map_selection_has_a_diversion_in_both_directions(real_rg, monkeypatch):
    monkeypatch.setattr(config, "CENTER_LAT", real_rg.centre()[0])
    monkeypatch.setattr(config, "CENTER_LON", real_rg.centre()[1])
    for h in place(real_rg).values():
        e = edge_of(real_rg, h)
        div = demo_diversion(real_rg, *e)
        assert div is not None and min(div) >= 1.3 * real_rg.g.edges[e]["length"]
        assert demo_site_tier(real_rg, *e) == (False, False, False)


def flood(client, depth=35):
    client.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": depth - 1, "simulated": True},
                headers=DEV)
    r = client.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": depth, "simulated": True},
                    headers=DEV).json()
    assert r["incident"]
    return client.get(f"/api/incidents/{r['incident']}").json()


@needs_real_map
def test_incident_affects_exactly_the_hotspot_road_segments(osm_client):
    from app.main import S
    h = hotspots(osm_client)["sensor-01"]
    u, v = S.rg.nearest_edge(h["lat"], h["lon"], 2)
    inc = flood(osm_client)
    assert inc["status"] == "Verified" and inc["severity"] == "Severe" and inc["simulated"] is True
    assert inc["road"] == S.rg.g.edges[u, v]["name"]
    segs = inc["affected_segments"]
    assert S.rg.edge_geo(u, v) in segs or S.rg.edge_geo(v, u) in segs, "the hotspot's own segment is affected"
    for (a, b) in segs:  # nothing farther than the incident radius is touched
        assert min(haversine_m(h["lat"], h["lon"], *p) for p in (a, b)) <= config.INCIDENT_EDGE_RADIUS_M + 110
    effects = incident_effects(S.rg, [Incident(type="flood", lat=h["lat"], lon=h["lon"], severity="Severe",
                                               depth_cm=35)])
    assert (u, v) in effects and (v, u) in effects, "both directions of the two-way road are affected"


@needs_real_map
def test_two_wheeler_route_changes_after_severe_waterlogging_and_a_valid_diversion_exists(osm_client):
    from app.main import S
    h = hotspots(osm_client)["sensor-01"]
    u, v = S.rg.nearest_edge(h["lat"], h["lon"], 2)
    a, b = S.rg.node_latlon(u), S.rg.node_latlon(v)
    direct = S.rg.g.edges[u, v]["length"]

    def route(vehicle, src, dst):
        r = osm_client.post("/api/routes", json={"from_lat": src[0], "from_lon": src[1], "to_lat": dst[0],
                                                 "to_lon": dst[1], "vehicle": vehicle})
        assert r.status_code == 200, r.text
        return r.json()

    before = route("two_wheeler", a, b)
    assert before["routes"][0]["length_m"] == pytest.approx(direct, abs=5)
    assert before["usual_route_hits"] == []

    inc = flood(osm_client)

    after = route("two_wheeler", a, b)
    assert after["usual_route_hits"] and after["usual_route_hits"][0]["id"] == inc["id"]
    assert not after["no_route"] and after["routes"], "a valid alternative route exists"
    best = after["routes"][0]
    assert best["length_m"] > 2 * direct, (best["length_m"], direct)
    assert best["coords"] != after["usual_route"]
    assert all(haversine_m(h["lat"], h["lon"], *p) > 40 for p in best["coords"]), "the diversion avoids the flood"
    assert best["coords"][0] == pytest.approx(list(a), abs=1e-5) and best["coords"][-1] == pytest.approx(list(b), abs=1e-5)

    back = route("two_wheeler", b, a)             # the reverse direction also has a way round
    assert back["routes"] and back["routes"][0]["length_m"] > 2 * direct

    bus = route("bus", a, b)                      # a bus may still wade through 35 cm
    assert bus["routes"][0]["length_m"] == pytest.approx(direct, abs=5)
    assert bus["routes"][0]["coords"] == bus["usual_route"]


@needs_real_map
def test_real_map_hotspot_and_sensor_workflows_still_work(osm_client, officer_headers_osm):
    inc = flood(osm_client)
    assert osm_client.get("/api/incidents").json()["incidents"], "verified sensor incident is public"
    hs = hotspots(osm_client)["sensor-01"]
    assert hs["depth_cm"] == pytest.approx(35) and hs["risk"] != "normal"
    rep = osm_client.post("/api/reports", json={"type": "flood", "lat": hs["lat"], "lon": hs["lon"],
                                                "reporter_id": "tg-1"}).json()
    assert rep["incident_id"] == inc["id"]
    done = osm_client.post(f"/api/incidents/{inc['id']}/clear", headers=officer_headers_osm)
    assert done.status_code == 200


@pytest.fixture()
def officer_headers_osm(osm_client):
    t = osm_client.post("/api/auth/login", json={"username": "officer", "password": "test-pass"}).json()["token"]
    return {"Authorization": f"Bearer {t}"}
