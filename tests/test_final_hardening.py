"""Final-phase hardening: input that used to crash, headers, rate limits, naming, and the lifecycle rules a commuter
relies on (stale incidents expire, resolved incidents stop affecting routes)."""
import time

from conftest import DEV

TOKEN = "X-Subscriber-Token"


def hotspot(client):
    return next(h for h in client.get("/api/hotspots").json()["hotspots"] if h["sensor_id"] == "sensor-01")


def flood(client, depth=35):
    """Two consecutive trusted sensor readings open and verify a flood incident."""
    for d in (depth - 1, depth):
        r = client.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": d, "simulated": True},
                        headers=DEV).json()
    assert r["incident"]
    return r["incident"]


def route(client, hs, vehicle="two_wheeler"):
    body = {"from_lat": hs["lat"] - 0.004, "from_lon": hs["lon"], "to_lat": hs["lat"] + 0.004, "to_lon": hs["lon"],
            "vehicle": vehicle}
    r = client.post("/api/routes", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------- input that used to crash ----------------

def test_non_ascii_password_is_a_clean_401_not_a_server_error(client):
    for user, pw in (("officer", "pässwörd✓"), ("unknown-ü", "x"), ("officer", "")):
        r = client.post("/api/auth/login", json={"username": user, "password": pw})
        assert r.status_code == 401, (user, pw, r.status_code, r.text)


def test_non_ascii_subscriber_token_is_a_clean_401_not_a_server_error(client):
    r = client.post("/api/subscribers", json={"name": "Ravi", "vehicle": "car", "consent_location": True})
    sid = r.json()["id"]
    bad = "tökén".encode("latin-1")   # raw bytes: a header with a non-ASCII byte, which servers decode as latin-1
    assert client.get(f"/api/subscribers/{sid}", headers={TOKEN: bad}).status_code == 401
    assert client.get(f"/api/subscribers/{sid}/alerts", headers={TOKEN: bad}).status_code == 401


def test_the_correct_password_still_works(client):
    assert client.post("/api/auth/login", json={"username": "officer", "password": "test-pass"}).status_code == 200


# ---------------- headers ----------------

def test_safe_headers_are_sent_on_pages_and_api(client):
    for path in ("/", "/api/health", "/control"):
        h = client.get(path).headers
        assert h["x-content-type-options"] == "nosniff", path
        assert h["x-frame-options"] == "DENY", path
        assert "frame-ancestors 'none'" in h["content-security-policy"], path
        assert "camera=()" in h["permissions-policy"], path


def test_private_responses_are_never_cacheable(client):
    login = client.post("/api/auth/login", json={"username": "officer", "password": "test-pass"})
    assert login.headers["cache-control"] == "no-store"
    sub = client.post("/api/subscribers", json={"name": "R", "vehicle": "car", "consent_location": True})
    assert sub.headers["cache-control"] == "no-store"
    sid, tok = sub.json()["id"], sub.json()["token"]
    assert client.get(f"/api/subscribers/{sid}", headers={TOKEN: tok}).headers["cache-control"] == "no-store"


# ---------------- rate limits ----------------

def test_the_public_evaluation_endpoint_is_rate_limited(client):
    flood(client)
    codes = [client.get("/api/evaluation?trips=50").status_code for _ in range(6)]
    assert codes[0] == 200 and 429 in codes, codes


# ---------------- naming ----------------

def test_only_a_flood_report_is_named_after_a_flood_watch_point(client):
    hs = hotspot(client)
    acc = client.post("/api/reports", json={"type": "accident", "lat": hs["lat"], "lon": hs["lon"], "reporter_id": "n1"}).json()
    flo = client.post("/api/reports", json={"type": "flood", "lat": hs["lat"], "lon": hs["lon"], "reporter_id": "n2"}).json()
    from app.store import store
    assert "hotspot" not in store.incidents[acc["incident_id"]].title.lower()
    assert "hotspot" in store.incidents[flo["incident_id"]].title.lower()


# ---------------- lifecycle rules a commuter relies on ----------------

def test_a_resolved_incident_stops_affecting_routes_and_alerts(client, officer_headers):
    hs = hotspot(client)
    before = route(client, hs)
    assert before["usual_route_hits"] == []
    iid = flood(client)
    hit = route(client, hs)
    assert hit["usual_route_hits"] and hit["usual_route_hits"][0]["id"] == iid
    assert client.get("/api/feed").json()["incidents"], "the open feed lists it while it is active"

    assert client.post(f"/api/incidents/{iid}/clear", json={}, headers=officer_headers).status_code == 200
    after = route(client, hs)
    assert after["usual_route_hits"] == []
    assert after["routes"][0]["length_m"] == before["routes"][0]["length_m"], "the original way is available again"
    assert client.get("/api/incidents").json()["incidents"] == []
    assert client.get("/api/feed").json()["incidents"] == []


def test_an_incident_with_no_new_signals_expires_and_stops_affecting_routes(client):
    from app import config, services
    from app.main import S
    from app.store import store
    hs = hotspot(client)
    iid = flood(client)
    assert route(client, hs)["usual_route_hits"], "active while evidence is fresh"

    services.tick(store, S.rg, time.time() + config.REPORT_EXPIRY_S + 60)   # a long time passes, sensor silent
    inc = client.get("/api/incidents?include_cleared=true").json()["incidents"]
    assert all(i["status"] == "Cleared" for i in inc if i["id"] == iid)
    assert client.get("/api/incidents").json()["incidents"] == []
    assert route(client, hs)["usual_route_hits"] == []


def test_a_citizen_report_alone_never_publishes_even_when_repeated(client):
    hs = hotspot(client)
    for i in range(5):
        client.post("/api/reports", json={"type": "accident", "lat": hs["lat"] + 0.002, "lon": hs["lon"], "reporter_id": f"dup-{i}"})
    assert client.get("/api/incidents").json()["incidents"] == []


def test_duplicate_reports_of_one_event_are_one_incident(client, officer_headers):
    hs = hotspot(client)
    ids = {client.post("/api/reports", json={"type": "blockage", "lat": hs["lat"] + 0.002, "lon": hs["lon"], "reporter_id": f"d-{i}"}).json()["incident_id"]
           for i in range(3)}
    assert len(ids) == 1
    mine = [i for i in client.get("/api/incidents?scope=all", headers=officer_headers).json()["incidents"] if i["id"] in ids]
    assert len(mine) == 1 and mine[0]["status"] == "Reported"
