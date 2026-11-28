"""Regression tests for the issues found in the engineering review."""
import math
import time

from conftest import DEV, field_login


def centre(client):
    return client.get("/api/health").json()["center"]


def moved(lat, lon, east_m, north_m):
    return lat + north_m / 111_320.0, lon + east_m / (111_320.0 * math.cos(math.radians(lat)))


# ---------------- P0: public reports must never auto-verify ----------------

def test_one_public_report_stays_unverified(client):
    lat, lon = moved(*centre(client), -150, -75)
    r = client.post("/api/reports", json={"type": "blockage", "lat": lat, "lon": lon, "reporter_id": "a"}).json()
    assert r["status"] == "Reported"
    assert client.get("/api/incidents").json()["incidents"] == []


def test_three_reports_with_fabricated_ids_stay_unverified(client, officer_headers):
    lat, lon = moved(*centre(client), -150, -75)
    for i in range(3):
        r = client.post("/api/reports", json={"type": "blockage", "lat": lat, "lon": lon,
                                              "reporter_id": f"fake-{i}"}).json()
    assert r["status"] == "Reported"
    # all public reports count as ONE source: confidence stays at a single report's weight
    assert r["confidence"] == 0.35
    assert client.get("/api/incidents").json()["incidents"] == []
    allinc = client.get("/api/incidents?scope=all", headers=officer_headers).json()["incidents"]
    assert len(allinc) == 1 and allinc[0]["published"] is False


def test_many_reports_never_verify(client, officer_headers, monkeypatch):
    from app import security
    monkeypatch.setattr(security.REPORT_LIMIT, "burst", 100)  # isolate the fusion rule from the rate limiter
    security.REPORT_LIMIT.state.clear()
    lat, lon = moved(*centre(client), -150, -75)
    for i in range(20):
        r = client.post("/api/reports", json={"type": "flood", "lat": lat, "lon": lon, "reporter_id": f"x{i}"})
        assert r.status_code == 201 and r.json()["status"] == "Reported"
    security.REPORT_LIMIT.state.clear()


def test_sensor_incident_still_verifies(client):
    for cm in (32, 34):
        res = client.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": cm}, headers=DEV).json()
    inc = client.get(f"/api/incidents/{res['incident']}").json()
    assert inc["status"] == "Verified" and inc["published"] is True


def test_officer_verification_publishes_report(client, officer_headers):
    lat, lon = moved(*centre(client), -150, -75)
    r = client.post("/api/reports", json={"type": "accident", "lat": lat, "lon": lon}).json()
    c = client.post(f"/api/incidents/{r['incident_id']}/verify", headers=officer_headers, json={}).json()
    assert c["status"] == "Reported" and c["published"] is False, "check 1 of 2 (control room) does not publish"
    assert client.get("/api/incidents").json()["incidents"] == []
    v = client.post(f"/api/field/incidents/{r['incident_id']}/confirm", headers=field_login(client), json={}).json()
    assert v["status"] == "Verified" and v["published"] is True
    assert [i["id"] for i in client.get("/api/incidents").json()["incidents"]] == [r["incident_id"]]
    # and it now affects routing
    body = {"from_lat": lat - 0.003, "from_lon": lon, "to_lat": lat + 0.003, "to_lon": lon, "vehicle": "car"}
    assert client.post("/api/routes", json=body).status_code == 200


# ---------------- P0: road-network snapping ----------------

def test_route_with_valid_local_coordinates(client):
    lat, lon = centre(client)
    r = client.post("/api/routes", json={"from_lat": lat - 0.004, "from_lon": lon, "to_lat": lat + 0.004, "to_lon": lon})
    assert r.status_code == 200 and r.json()["routes"]


def test_route_just_outside_snap_distance(client):
    from app import config
    lat, lon = centre(client)
    # the grid's north-east corner node is at +600 m / +600 m; go limit + 50 m further north
    far_lat, far_lon = moved(lat, lon, 600, 600 + config.MAX_SNAP_M + 50)
    r = client.post("/api/routes", json={"from_lat": lat, "from_lon": lon, "to_lat": far_lat, "to_lon": far_lon})
    assert r.status_code == 422
    assert "Destination" in r.json()["detail"] and "mapped road" in r.json()["detail"]


def test_route_between_unrelated_places_rejected(client):
    r = client.post("/api/routes", json={"from_lat": 0, "from_lon": 0, "to_lat": 28, "to_lon": 77})
    assert r.status_code == 422


def test_route_points_on_same_spot_rejected(client):
    # road-segment routing: two points closer than 10 m along the road are the same spot
    lat, lon = centre(client)
    r = client.post("/api/routes", json={"from_lat": lat + 0.0002, "from_lon": lon,
                                         "to_lat": lat + 0.00022, "to_lon": lon})
    assert r.status_code == 422 and "same spot" in r.json()["detail"]
    # ...while points ~45 m apart on the same road now get a real route (no longer forced to one junction)
    r = client.post("/api/routes", json={"from_lat": lat + 0.0002, "from_lon": lon,
                                         "to_lat": lat + 0.0006, "to_lon": lon})
    assert r.status_code == 200 and r.json()["routes"]


def test_report_and_probe_off_network_rejected(client):
    assert client.post("/api/reports", json={"type": "flood", "lat": 0, "lon": 0}).status_code == 422
    r = client.post("/api/probes", json={"vehicle_id": "v1", "lat": 0, "lon": 0, "speed_kph": 10}, headers=DEV)
    assert r.status_code == 422


# ---------------- P0: input and timestamp validation ----------------

def test_coordinate_ranges_and_lengths(client):
    assert client.post("/api/reports", json={"type": "flood", "lat": 95, "lon": 0}).status_code == 422
    assert client.post("/api/reports", json={"type": "flood", "lat": 0, "lon": 200}).status_code == 422
    lat, lon = centre(client)
    assert client.post("/api/reports", json={"type": "flood", "lat": lat, "lon": lon,
                                             "note": "x" * 201}).status_code == 422
    assert client.post("/api/reports", json={"type": "flood", "lat": lat, "lon": lon,
                                             "reporter_id": "y" * 65}).status_code == 422


def test_non_finite_numbers_rejected(client):
    r = client.post("/api/routes", content='{"from_lat":NaN,"from_lon":0,"to_lat":1,"to_lon":1}',
                    headers={"content-type": "application/json"})
    assert r.status_code == 422
    r = client.post("/api/sensors/readings", content='{"sensor_id":"sensor-01","depth_cm":Infinity}',
                    headers={"content-type": "application/json", **DEV})
    assert r.status_code == 422


def test_depth_speed_rain_bounds(client, officer_headers):
    assert client.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": -1},
                       headers=DEV).status_code == 422
    assert client.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": 301},
                       headers=DEV).status_code == 422
    lat, lon = centre(client)
    assert client.post("/api/probes", json={"vehicle_id": "v", "lat": lat, "lon": lon, "speed_kph": 151},
                       headers=DEV).status_code == 422
    assert client.post("/api/weather/override", json={"rain_mm_per_hr": -5},
                       headers=officer_headers).status_code == 422
    assert client.post("/api/weather/override", json={"rain_mm_per_hr": 500},
                       headers=officer_headers).status_code == 422


def test_future_and_ancient_timestamps_rejected(client):
    for ts in (time.time() + 3600, time.time() - 7 * 86400):
        r = client.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": 5, "ts": ts}, headers=DEV)
        assert r.status_code == 422
    lat, lon = centre(client)
    r = client.post("/api/probes", json={"vehicle_id": "v", "lat": lat, "lon": lon, "speed_kph": 10,
                                         "ts": time.time() + 3600}, headers=DEV)
    assert r.status_code == 422


def test_small_clock_skew_accepted(client):
    r = client.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": 5,
                                                   "ts": time.time() + 30}, headers=DEV)
    assert r.status_code == 200


def test_old_reading_cannot_overwrite_newer_state(client):
    t = time.time()
    client.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": 30, "ts": t}, headers=DEV)
    late = client.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": 2, "ts": t - 120},
                       headers=DEV).json()
    assert late.get("stale") is True
    hs = next(h for h in client.get("/api/hotspots").json()["hotspots"] if h["sensor_id"] == "sensor-01")
    assert hs["depth_cm"] == 30


def test_map_mode_is_reported(client):
    h = client.get("/api/health").json()
    assert h["map_mode"] == "synthetic"
    assert client.get("/api/network").json()["map_mode"] == "synthetic"


# ---------------- P1: consistency fixes ----------------

def test_forged_forwarded_for_does_not_bypass_rate_limit(client, monkeypatch):
    from app import security
    monkeypatch.setattr(security.REPORT_LIMIT, "burst", 2)
    security.REPORT_LIMIT.state.clear()
    lat, lon = centre(client)
    codes = [client.post("/api/reports", json={"type": "blockage", "lat": lat, "lon": lon},
                         headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code for i in range(6)]
    assert 429 in codes, "a fake X-Forwarded-For per request must not give a fresh bucket"
    security.REPORT_LIMIT.state.clear()


def test_evidence_is_bounded_and_confidence_unchanged(client, monkeypatch):
    from app import security
    from app.store import store
    monkeypatch.setattr(security.DEVICE_LIMIT, "burst", 1000)
    security.DEVICE_LIMIT.state.clear()
    for i in range(400):
        res = client.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": 30 + (i % 3)},
                          headers=DEV).json()
    inc = store.incidents[res["incident"]]
    assert len(inc.signals) <= 120
    assert inc.confidence == 0.8 and inc.status == "Verified"
    security.DEVICE_LIMIT.state.clear()


def test_forecast_failure_is_backed_off(monkeypatch):
    import httpx
    from app import forecast
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(forecast.httpx, "get", boom)
    forecast._cache.update(data=None, good_at=0.0, failed_at=0.0)
    for _ in range(5):
        assert forecast.fetch_forecast(18.6, 84.2) is None
    assert calls["n"] == 1, "after one failure the API is not called again during the backoff window"


def test_validation_error_is_readable(client):
    r = client.post("/api/reports", json={"type": "flood", "lat": 95, "lon": 0})
    assert r.status_code == 422 and "lat" in r.json()["detail"]
