"""Phase 6 gaps found while validating the product end to end: the map fallback must never be silent, and a citizen
must be able to see what happened to their report without that report being published."""
import os

import pytest
from conftest import DEV

from app import config

REAL_GRAPH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "area.graphml"))


def start(tmp_path, monkeypatch, graphml):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(config, "GRAPHML_PATH", graphml)
    monkeypatch.setattr(config, "CENTER_LAT", config.CENTER_LAT)  # the OSM start-up moves the centre; restore it
    monkeypatch.setattr(config, "CENTER_LON", config.CENTER_LON)
    from fastapi.testclient import TestClient
    from app.main import S, app
    from app.store import store
    S.saved = {}
    store.reset()
    return TestClient(app)


def test_corrupt_graphml_falls_back_loudly(tmp_path, monkeypatch):
    bad = tmp_path / "area.graphml"
    bad.write_text("this is not graphml")
    with start(tmp_path, monkeypatch, str(bad)) as c:
        h = c.get("/api/health").json()
        assert h["map_mode"] == "synthetic" and h["graph"] == "synthetic-grid"
        assert "could not be loaded" in h["map_warning"] and "SYNTHETIC" in h["map_warning"]
        assert c.get("/api/network").json()["map_warning"] == h["map_warning"]


def test_missing_graphml_is_reported_not_silent(tmp_path, monkeypatch):
    with start(tmp_path, monkeypatch, str(tmp_path / "missing.graphml")) as c:
        h = c.get("/api/health").json()
        assert h["map_mode"] == "synthetic"
        assert "missing" in h["map_warning"] and "not real roads" in h["map_warning"]


@pytest.mark.skipif(not os.path.exists(REAL_GRAPH), reason="data/area.graphml not present")
def test_real_graph_has_no_warning(tmp_path, monkeypatch):
    with start(tmp_path, monkeypatch, REAL_GRAPH) as c:
        h = c.get("/api/health").json()
        assert h["map_mode"] == "osm" and h["map_warning"] is None


def post_report(client, lat, lon, kind="accident", who="tg-1"):
    r = client.post("/api/reports", json={"type": kind, "lat": lat, "lon": lon, "reporter_id": who})
    assert r.status_code == 201, r.text
    return r.json()


def test_report_status_follows_the_report_without_publishing_it(client, officer_headers):
    hs = next(h for h in client.get("/api/hotspots").json()["hotspots"] if h["sensor_id"] == "sensor-01")
    rep = post_report(client, hs["lat"] + 0.002, hs["lon"])
    assert rep["status"] == "Reported"
    iid = rep["incident_id"]

    st = client.get(f"/api/reports/{iid}/status").json()
    assert st["outcome"] == "pending" and st["published"] is False
    assert set(st) == {"incident_id", "outcome", "status", "published"}, "no location or evidence is exposed"
    assert all(i["id"] != iid for i in client.get("/api/incidents").json()["incidents"]), "still not public"

    client.post(f"/api/incidents/{iid}/verify", headers=officer_headers)
    assert client.get(f"/api/reports/{iid}/status").json()["outcome"] == "published"

    client.post(f"/api/incidents/{iid}/clear", headers=officer_headers)
    assert client.get(f"/api/reports/{iid}/status").json()["outcome"] == "resolved"


def test_rejected_report_is_reported_as_rejected(client, officer_headers):
    hs = next(h for h in client.get("/api/hotspots").json()["hotspots"] if h["sensor_id"] == "sensor-01")
    iid = post_report(client, hs["lat"] + 0.002, hs["lon"], kind="blockage")["incident_id"]
    client.post(f"/api/incidents/{iid}/reject", headers=officer_headers)
    st = client.get(f"/api/reports/{iid}/status").json()
    assert st["outcome"] == "rejected" and st["published"] is False


def test_report_status_unknown_id_is_404(client):
    assert client.get("/api/reports/inc-doesnotexist/status").status_code == 404


def test_sensor_evidence_is_visible_to_officers_only(client, officer_headers):
    for d in (34, 35):
        r = client.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": d, "simulated": True},
                        headers=DEV).json()
    iid = r["incident"]
    pub = client.get(f"/api/incidents/{iid}").json()
    assert "signals" not in pub, "the public view carries no raw evidence"
    full = client.get(f"/api/incidents/{iid}", headers=officer_headers).json()
    assert full["signals"] and full["signals"][-1]["source"] == "sensor" and full["signals"][-1]["simulated"] is True
