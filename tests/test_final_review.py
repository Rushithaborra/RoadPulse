"""Regression tests for the final targeted review: segment snapping, stream tickets, simulated-data labels."""
import json
import socket
import threading
import time

import httpx
import networkx as nx
import pytest

from conftest import DEV


# ---------------- 1. route snapping uses road SEGMENTS, path still uses nodes ----------------

@pytest.fixture()
def long_road(client):
    """Swap in a map whose roads are 2 km long with junctions only at the ends."""
    from app.geo import offset
    from app.main import S
    from app.roadgraph import RoadGraph, _edge_attrs
    lat0, lon0 = 18.6, 84.2
    g = nx.DiGraph()
    for n, (e, nm) in {"A": (0, 0), "B": (0, 2000), "C": (600, 0), "D": (600, 2000)}.items():
        la, lo = offset(lat0, lon0, e, nm)
        g.add_node(n, lat=la, lon=lo)
    for u, v, length in (("A", "B", 2000), ("C", "D", 2000), ("A", "C", 600), ("B", "D", 600)):
        g.add_edge(u, v, **_edge_attrs(length, 40, 1, "Long Road"))
        g.add_edge(v, u, **_edge_attrs(length, 40, 1, "Long Road"))
    old = S.rg
    S.rg = RoadGraph(g, "test-long-road")
    yield lambda e, n: offset(lat0, lon0, e, n)
    S.rg = old


def test_point_on_long_road_far_from_junctions_is_accepted(client, long_road):
    start = long_road(0, 1100)       # on road A-B, ~900 m from the nearest junction
    dest = long_road(600, 1800)      # on road C-D, near junction D
    r = client.post("/api/routes", json={"from_lat": start[0], "from_lon": start[1],
                                         "to_lat": dest[0], "to_lon": dest[1]})
    assert r.status_code == 200, r.json()
    assert r.json()["routes"], "a path is still computed from the nearest graph nodes"


def test_point_beside_long_road_beyond_limit_is_rejected(client, long_road):
    from app import config
    start = long_road(-(config.MAX_SNAP_M + 60), 1000)   # beside road A-B, just past the limit
    dest = long_road(600, 1800)
    r = client.post("/api/routes", json={"from_lat": start[0], "from_lon": start[1],
                                         "to_lat": dest[0], "to_lon": dest[1]})
    assert r.status_code == 422 and r.json()["detail"].startswith("Start is")
    assert " m from the nearest mapped road" in r.json()["detail"]


def test_point_beside_long_road_within_limit_is_accepted(client, long_road):
    start = long_road(-120, 1000)    # 120 m beside road A-B, 1 km from any junction
    dest = long_road(600, 1800)
    r = client.post("/api/routes", json={"from_lat": start[0], "from_lon": start[1],
                                         "to_lat": dest[0], "to_lon": dest[1]})
    assert r.status_code == 200


def test_report_on_long_road_accepted(client, long_road):
    p = long_road(0, 1000)
    assert client.post("/api/reports", json={"type": "flood", "lat": p[0], "lon": p[1]}).status_code == 201


def test_far_outside_map_is_fast_and_rejected(client):
    t0 = time.perf_counter()
    r = client.post("/api/routes", json={"from_lat": -33.9, "from_lon": 151.2, "to_lat": 51.5, "to_lon": -0.1})
    assert r.status_code == 422 and "km from the nearest mapped road" in r.json()["detail"]
    assert time.perf_counter() - t0 < 1.0


# ---------------- 2. officer stream uses one-time tickets, never the bearer token ----------------

def test_ticket_requires_officer_login(client):
    assert client.post("/api/stream/ticket").status_code == 401


def test_bad_used_or_expired_ticket_is_rejected(client, officer_headers, monkeypatch):
    from app import security
    assert client.get("/api/stream?ticket=made-up").status_code == 401
    t = client.post("/api/stream/ticket", headers=officer_headers).json()
    assert t["expires_in"] == 60
    assert security.consume_stream_ticket(t["ticket"]) is not None              # first use works
    assert client.get(f"/api/stream?ticket={t['ticket']}").status_code == 401   # second use rejected

    t2 = client.post("/api/stream/ticket", headers=officer_headers).json()
    real_time = time.time

    class Later:  # 61 s in the future
        @staticmethod
        def time():
            return real_time() + 61
    monkeypatch.setattr(security, "time", Later)
    assert security.consume_stream_ticket(t2["ticket"]) is None                 # expired


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def live_server(client):
    """Real uvicorn server on a thread (in-process test clients cannot read an endless SSE stream)."""
    import uvicorn
    from app.main import app
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    th.join(timeout=5)


def _first_event(url: str) -> dict:
    with httpx.stream("GET", url, timeout=10) as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line.startswith("data:"):
                return json.loads(line[5:])
    raise AssertionError("no event")


def test_officer_stream_with_ticket_and_public_stream(live_server, officer_headers):
    with httpx.Client(base_url=live_server) as c:
        for cm in (32, 34):  # one verified incident
            c.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": cm}, headers=DEV)
        ticket = c.post("/api/stream/ticket", headers=officer_headers).json()["ticket"]
    officer = _first_event(f"{live_server}/api/stream?ticket={ticket}")
    assert "events" in officer and officer["incidents"], "officer snapshot includes the activity log"
    public = _first_event(f"{live_server}/api/stream")
    assert "events" not in public and public["incidents"]
    with httpx.Client(base_url=live_server) as c:  # the ticket was spent by the first connection
        assert c.get(f"/api/stream?ticket={ticket}").status_code == 401
    # the long-lived bearer token in the URL (old ?token= style) no longer grants the officer stream
    bearer = officer_headers["Authorization"].split(" ", 1)[1]
    assert "events" not in _first_event(f"{live_server}/api/stream?token={bearer}")


# ---------------- 3. demo readiness: simulated data is labelled ----------------

def test_simulated_data_is_labelled(client):
    for cm in (32, 34):
        res = client.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": cm, "simulated": True},
                          headers=DEV).json()
    inc = client.get(f"/api/incidents/{res['incident']}").json()
    assert inc["simulated"] is True
    hs = next(h for h in client.get("/api/hotspots").json()["hotspots"] if h["sensor_id"] == "sensor-01")
    assert hs["last_reading_simulated"] is True


def test_real_data_is_not_labelled_simulated(client):
    for cm in (32, 34):
        res = client.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": cm},
                          headers=DEV).json()
    assert client.get(f"/api/incidents/{res['incident']}").json()["simulated"] is False


def test_rain_override_is_labelled_as_manual(client, officer_headers):
    client.post("/api/weather/override", json={"rain_mm_per_hr": 40}, headers=officer_headers)
    assert "manual override" in client.get("/api/hotspots").json()["rain_source"]
    client.post("/api/weather/override", json={"rain_mm_per_hr": None}, headers=officer_headers)


def test_operator_errors_are_plain_sentences(client, officer_headers):
    r = client.post("/api/incidents/does-not-exist/verify", headers=officer_headers, json={})
    assert r.status_code == 404 and r.json()["detail"] == "Incident not found"
    lat, lon = client.get("/api/health").json()["center"]
    r = client.post("/api/routes", json={"from_lat": lat, "from_lon": lon, "to_lat": lat, "to_lon": lon})
    assert r.status_code == 422 and "same spot" in r.json()["detail"]
    for cm in (32, 34):
        res = client.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": cm}, headers=DEV).json()
    client.post(f"/api/incidents/{res['incident']}/clear", headers=officer_headers, json={})
    r = client.post(f"/api/incidents/{res['incident']}/respond", headers=officer_headers, json={})
    assert r.status_code == 409 and r.json()["detail"] == "Incident already cleared"


def test_simulated_planned_works_are_labelled(client, officer_headers):
    lat, lon = client.get("/api/health").json()["center"]
    now = time.time()
    w = client.post("/api/planned-works", headers=officer_headers, json={
        "title": "Demo digging", "lat": lat, "lon": lon + 0.001, "starts": now, "ends": now + 3600,
        "simulated": True}).json()
    assert w["simulated"] is True
