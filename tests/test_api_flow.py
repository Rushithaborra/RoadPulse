"""Integration test of the required demo chain:
report → assessed → affected road identified → alternative route → dashboard updated → auto-clear."""
from conftest import DEV


def hotspot(client):
    return next(h for h in client.get("/api/hotspots").json()["hotspots"] if h["sensor_id"] == "sensor-01")


def test_waterlogging_to_rerouting_flow(client, officer_headers):
    hs = hotspot(client)

    # 1. sensor rises: first reading alone does not open an incident (splash filter), second does
    r1 = client.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": 34}, headers=DEV).json()
    assert r1["incident"] is None
    r2 = client.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": 35}, headers=DEV).json()
    inc_id = r2["incident"]
    assert inc_id

    # 2. assessed: sensor alone = 0.8 confidence → verified + published, severity from depth
    inc = client.get(f"/api/incidents/{inc_id}").json()
    assert inc["status"] == "Verified" and inc["severity"] == "Severe"
    assert "two_wheeler" in inc["affected_vehicles"] and "bus" not in inc["affected_vehicles"]

    # 3. affected road identified
    assert len(inc["affected_segments"]) >= 1

    # 4. a citizen report nearby merges into the same incident and raises confidence
    rep = client.post("/api/reports", json={"type": "flood", "lat": hs["lat"], "lon": hs["lon"],
                                            "reporter_id": "tg-1"}).json()
    assert rep["incident_id"] == inc_id
    assert client.get(f"/api/incidents/{inc_id}").json()["confidence"] > 0.8

    # 5. alternative route: the bike's usual route hits the flood, RoadPulse routes avoid it
    body = {"from_lat": hs["lat"] - 0.004, "from_lon": hs["lon"], "to_lat": hs["lat"] + 0.004,
            "to_lon": hs["lon"], "vehicle": "two_wheeler"}
    bike = client.post("/api/routes", json=body).json()
    assert bike["usual_route_hits"] and bike["usual_route_hits"][0]["id"] == inc_id
    assert bike["routes"] and bike["routes"][0]["coords"] != bike["usual_route"]
    bus = client.post("/api/routes", json=body | {"vehicle": "bus"}).json()
    assert bus["routes"][0]["coords"] == bus["usual_route"], "bus may still pass at 35 cm"

    # 6. dashboard: officer sees it and approves the diversion
    allinc = client.get("/api/incidents?scope=all", headers=officer_headers).json()["incidents"]
    assert any(i["id"] == inc_id for i in allinc)
    resp = client.post(f"/api/incidents/{inc_id}/respond", headers=officer_headers, json={}).json()
    assert resp["status"] == "Responding"

    # 7. water drains → lifecycle auto-clears (clear window is 0 s in tests)
    client.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": 3}, headers=DEV)
    from app.main import S
    from app.services import tick
    from app.store import store
    tick(store, S.rg)
    tick(store, S.rg)
    assert store.incidents[inc_id].status == "Cleared"
    assert client.get("/api/incidents").json()["incidents"] == []


def test_officer_endpoints_require_login(client):
    assert client.get("/api/incidents?scope=all").status_code == 401
    assert client.post("/api/incidents/x/verify").status_code == 401
    bad = client.post("/api/auth/login", json={"username": "officer", "password": "wrong"})
    assert bad.status_code == 401


def test_device_key_required(client):
    r = client.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": 20})
    assert r.status_code == 401


def test_validation_rejects_bad_input(client):
    assert client.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": 999},
                       headers=DEV).status_code == 422
    assert client.post("/api/reports", json={"type": "volcano", "lat": 1, "lon": 2}).status_code == 422


def test_single_report_stays_unverified(client):
    h = hotspot(client)
    r = client.post("/api/reports", json={"type": "accident", "lat": h["lat"], "lon": h["lon"] + 0.003,
                                          "reporter_id": "u9"}).json()
    assert r["status"] == "Reported"
    assert client.get("/api/incidents").json()["incidents"] == []  # not shown to commuters yet


def test_probe_slowdown_detection(client):
    from app.main import S
    u, v = "n-2_0", "n-2_1"
    (a, b), (c, d) = S.rg.node_latlon(u), S.rg.node_latlon(v)
    results = [client.post("/api/probes", json={"vehicle_id": f"bus-{i}", "lat": (a + c) / 2, "lon": (b + d) / 2,
                                                "speed_kph": 4}, headers=DEV).json() for i in range(4)]
    assert results[0]["status"] == "unknown"  # fewer than 3 vehicles: no judgement yet
    assert any(r.get("incident") for r in results)


def test_planned_works_window(client, officer_headers):
    import time
    h = hotspot(client)
    now = time.time()
    w = client.post("/api/planned-works", headers=officer_headers, json={
        "title": "Pipeline digging", "lat": h["lat"], "lon": h["lon"] - 0.004, "starts": now - 60,
        "ends": now + 3600, "full_closure": True}).json()
    assert w["type"] == "construction"
    from app.main import S
    from app.services import tick
    from app.store import store
    tick(store, S.rg)
    assert store.incidents[w["id"]].status == "Verified"


def test_rate_limit_on_reports(client, monkeypatch):
    from app import security
    monkeypatch.setattr(security.REPORT_LIMIT, "burst", 2)
    security.REPORT_LIMIT.state.clear()
    codes = [client.post("/api/reports", json={"type": "blockage", "lat": 1, "lon": 1}).status_code for _ in range(6)]
    assert 429 in codes
    security.REPORT_LIMIT.state.clear()


def test_slowdown_merges_with_nearby_accident_not_distant_flood(client, officer_headers):
    import math
    h = hotspot(client)
    # flood at the hotspot
    for cm in (30, 32):
        client.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": cm}, headers=DEV)
    # accident ~210 m away: speed drop then a report at the same spot
    lat = h["lat"] - 150 / 111_320
    lon = h["lon"] - 150 / (111_320 * math.cos(math.radians(h["lat"])))
    for i in range(4):
        client.post("/api/probes", json={"vehicle_id": f"b{i}", "lat": lat, "lon": lon, "speed_kph": 3}, headers=DEV)
    rep = client.post("/api/reports", json={"type": "accident", "lat": lat, "lon": lon, "reporter_id": "u1"}).json()
    # probe 0.5 + report 0.35 → 0.675 (still needs one more source or an officer)
    assert rep["confidence"] > 0.6
    all_inc = client.get("/api/incidents?scope=all", headers=officer_headers).json()["incidents"]
    flood = next(i for i in all_inc if i["type"] == "flood")
    assert "probe" not in flood["signals_by_source"]
