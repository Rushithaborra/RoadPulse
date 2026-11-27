"""Tests for the alert layer, one group per doubt we worked through (see docs/ALERTS.md).

The tests use the synthetic 9x9 grid (150 m blocks). Row 0 / column 0 are the faster arterials.
"""
import time

import pytest

from conftest import DEV

TOKEN = "X-Subscriber-Token"


@pytest.fixture(autouse=True)
def _open_rate_limits(monkeypatch):
    """These tests send hundreds of probe samples and sign-ups in a few seconds: lift the per-IP limits."""
    from app import security
    for lim in (security.DEVICE_LIMIT, security.SUBSCRIBE_LIMIT, security.REPORT_LIMIT):
        monkeypatch.setattr(lim, "burst", 100000)
        monkeypatch.setattr(lim, "rate", 100000.0)
        lim.state.clear()
    yield
    for lim in (security.DEVICE_LIMIT, security.SUBSCRIBE_LIMIT, security.REPORT_LIMIT):
        lim.state.clear()


# ---------------------------------------------------------------- helpers
def ll(n):
    from app.main import S
    return S.rg.node_latlon(n)


def mid(a, b):
    (alat, alon), (blat, blon) = ll(a), ll(b)
    return (alat + blat) / 2, (alon + blon) / 2


def signup(client, **kw):
    body = {"name": "Ravi", "vehicle": "car", "consent_location": True, **kw}
    r = client.post("/api/subscribers", json=body)
    assert r.status_code == 201, r.text
    d = r.json()
    return d["id"], {TOKEN: d["token"]}


def at(client, sid, h, node=None, lat=None, lon=None, ts=None):
    lat, lon = (lat, lon) if node is None else ll(node)
    body = {"lat": lat, "lon": lon}
    if ts:
        body["ts"] = ts
    r = client.post(f"/api/subscribers/{sid}/position", json=body, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def trip(client, sid, h, node):
    lat, lon = ll(node)
    r = client.post(f"/api/subscribers/{sid}/trip", json={"lat": lat, "lon": lon}, headers=h)
    assert r.status_code == 200, r.text


def publish_accident(client, officer_headers, a="n0_0", b="n0_1", severity="Closed"):
    """A citizen report + officer verification (the officer decides the severity). Returns the incident id."""
    lat, lon = mid(a, b)
    rep = client.post("/api/reports", json={"type": "accident", "lat": lat, "lon": lon, "reporter_id": "c1"}).json()
    iid = rep["incident_id"]
    r = client.post(f"/api/incidents/{iid}/verify", json={"severity": severity}, headers=officer_headers)
    assert r.status_code == 200
    assert client.get("/api/incidents").json()["incidents"], "verified incident must be public"
    return iid


def between(a, b, t):
    (alat, alon), (blat, blon) = ll(a), ll(b)
    return alat + (blat - alat) * t, alon + (blon - alon) * t


def probe_batch(client, node_a, node_b, kph, vehicles, ts, t_along=0.5, tag="v"):
    """vehicles distinct vehicles reporting at a point on the road between two nodes (t_along from a to b)."""
    (alat, alon), (blat, blon) = ll(node_a), ll(node_b)
    lat, lon = alat + (blat - alat) * t_along, alon + (blon - alon) * t_along
    out = []
    for i in range(vehicles):
        out.append(client.post("/api/probes", json={"vehicle_id": f"{tag}{i}", "lat": lat, "lon": lon,
                                                    "speed_kph": kph, "ts": ts}, headers=DEV).json())
    return out


def incidents_all(client, officer_headers):
    return client.get("/api/incidents?scope=all", headers=officer_headers).json()["incidents"]


# ================================================================ doubt 2: speed alone cannot decide
class TestSpeedRule:
    """A single slow driver, too few vehicles, signal queues and ordinary congestion must not raise anything."""

    # the grid's streets are 25-40 km/h, so "normal" here is 0.6 x the limit (15-24 km/h) and "slow" is < 40% of that

    def test_one_slow_driver_among_normal_traffic_is_ignored(self, client, officer_headers):
        now = time.time()
        for i in range(12):  # 12 vehicles at a normal speed over 5 minutes, plus one very slow senior citizen
            probe_batch(client, "n0_-3", "n0_-2", 22, 1, now - 290 + i * 25, tag=f"ok{i}")
        r = probe_batch(client, "n0_-3", "n0_-2", 3, 1, now, tag="senior")[0]
        assert r["status"] == "normal"
        assert incidents_all(client, officer_headers) == []

    def test_too_few_vehicles_is_not_enough_data(self, client, officer_headers):
        now = time.time()
        for k in range(4):  # 7 vehicles, all crawling: still below PROBE_MIN_VEHICLES (8)
            res = probe_batch(client, "n0_-3", "n0_-2", 2, 7, now - 400 + k * 100)
        assert res[-1]["status"] == "unknown" and res[-1]["vehicles"] == 7
        assert incidents_all(client, officer_headers) == []

    def test_red_light_queue_near_a_junction_is_ignored(self, client, officer_headers):
        now = time.time()
        # 20 vehicles stopped, but 10 m from the junction (inside the junction zone): a normal signal queue
        for k in range(4):
            probe_batch(client, "n0_-3", "n0_-2", 0, 20, now - 400 + k * 100, t_along=0.07)
        assert incidents_all(client, officer_headers) == []

    def test_a_dip_must_last_longer_than_a_signal_cycle(self, client, officer_headers):
        now = time.time()
        res = probe_batch(client, "n0_-3", "n0_-2", 2, 10, now)  # slow right now, but only for a moment
        assert res[-1]["status"] == "anomaly"
        assert incidents_all(client, officer_headers) == []
        assert not any(r.get("incident") for r in res)

    def test_sustained_slowdown_over_many_vehicles_raises_a_suspicion_only(self, client, officer_headers):
        now = time.time()
        probe_batch(client, "n0_-3", "n0_-2", 2, 10, now - 240, tag="a")
        res = probe_batch(client, "n0_-3", "n0_-2", 2, 10, now, tag="b")
        inc_id = next(r["incident"] for r in res if r.get("incident"))
        inc = next(i for i in incidents_all(client, officer_headers) if i["id"] == inc_id)
        assert inc["type"] == "slowdown" and inc["status"] == "Reported"
        assert not inc["published"], "speed alone never reaches commuters"
        assert client.get("/api/incidents").json()["incidents"] == []
        assert inc["signals_by_source"] == {"probe": 1}

    def test_probes_plus_one_report_still_do_not_publish(self, client, officer_headers):
        now = time.time()
        probe_batch(client, "n0_-3", "n0_-2", 2, 10, now - 240, tag="a")
        probe_batch(client, "n0_-3", "n0_-2", 2, 10, now, tag="b")
        lat, lon = mid("n0_-3", "n0_-2")
        client.post("/api/reports", json={"type": "accident", "lat": lat, "lon": lon, "reporter_id": "u1"})
        inc = incidents_all(client, officer_headers)[0]
        assert inc["type"] == "accident" and inc["confidence"] < 0.7 and inc["status"] == "Reported"
        assert client.get("/api/incidents").json()["incidents"] == []

    def test_area_wide_slowness_is_congestion_not_an_incident(self, client, officer_headers):
        now = time.time()
        # the road AND its neighbours are all slow at once: area-wide, not local
        for a, b in (("n0_-3", "n0_-2"), ("n0_-2", "n0_-1"), ("n0_-4", "n0_-3")):
            probe_batch(client, a, b, 2, 10, now - 240, tag=f"{a}x")
        res = []
        for a, b in (("n0_-2", "n0_-1"), ("n0_-4", "n0_-3"), ("n0_-3", "n0_-2")):
            res = probe_batch(client, a, b, 2, 10, now, tag=f"{a}y")
        assert res[-1]["status"] == "congestion"
        assert not any(r.get("incident") for r in res)

    def test_mid_block_slowdown_is_placed_where_the_slow_vehicles_are(self, client, officer_headers):
        now = time.time()
        # a long stretch with an accident at 70% of the way: vehicles queue around that point
        probe_batch(client, "n0_-3", "n0_-2", 2, 10, now - 240, t_along=0.7, tag="a")
        res = probe_batch(client, "n0_-3", "n0_-2", 2, 10, now, t_along=0.7, tag="b")
        inc = next(i for i in incidents_all(client, officer_headers) if i["id"] == next(r["incident"] for r in res if r.get("incident")))
        (alat, alon), (blat, blon) = ll("n0_-3"), ll("n0_-2")
        assert inc["lon"] == pytest.approx(alon + (blon - alon) * 0.7, abs=1e-4)


# ================================================================ doubts 1 and 5: who updates, what happens
class TestWhoUpdates:
    def test_public_reports_never_publish_and_an_officer_must(self, client, officer_headers):
        lat, lon = mid("n0_0", "n0_1")
        for i in range(5):
            client.post("/api/reports", json={"type": "accident", "lat": lat, "lon": lon, "reporter_id": f"u{i}"})
        assert client.get("/api/incidents").json()["incidents"] == []
        iid = incidents_all(client, officer_headers)[0]["id"]
        assert client.post(f"/api/incidents/{iid}/verify").status_code == 401  # anonymous cannot
        assert client.post(f"/api/incidents/{iid}/verify", headers=officer_headers).status_code == 200
        assert len(client.get("/api/incidents").json()["incidents"]) == 1

    def test_construction_is_scheduled_by_the_agency_not_detected(self, client, officer_headers):
        lat, lon = mid("n0_0", "n0_1")
        now = time.time()
        w = client.post("/api/planned-works", headers=officer_headers, json={
            "title": "Pipe laying", "lat": lat, "lon": lon, "starts": now + 3600, "ends": now + 7200,
            "agency": "GVMC"}).json()
        assert w["type"] == "construction" and w["status"] == "Reported"
        assert not w["published"]       # not yet: its window has not started

    def test_officer_dispatches_units_and_it_is_recorded(self, client, officer_headers):
        iid = publish_accident(client, officer_headers)
        assert client.post(f"/api/incidents/{iid}/dispatch", json={"unit": "ambulance"}).status_code == 401
        for unit in ("police", "ambulance", "tow"):
            r = client.post(f"/api/incidents/{iid}/dispatch", json={"unit": unit}, headers=officer_headers)
            assert r.status_code == 201
        d = client.get(f"/api/incidents/{iid}", headers=officer_headers).json()
        assert [x["unit"] for x in d["dispatched"]] == ["police", "ambulance", "tow"]
        assert client.post(f"/api/incidents/{iid}/dispatch", json={"unit": "helicopter"},
                           headers=officer_headers).status_code == 422
        audit = client.get("/api/audit", headers=officer_headers).json()["entries"]
        assert sum(1 for e in audit if e["action"] == "dispatch") == 3


# ================================================================ doubt 4: who is warned, how, and not everyone
class TestTargeting:
    def test_consent_is_required_and_the_token_protects_the_commuter(self, client):
        r = client.post("/api/subscribers", json={"name": "A", "consent_location": False})
        assert r.status_code == 422
        sid, h = signup(client)
        assert client.get(f"/api/subscribers/{sid}").status_code == 401
        assert client.get(f"/api/subscribers/{sid}", headers={TOKEN: "wrong"}).status_code == 401
        assert client.get(f"/api/subscribers/{sid}", headers=h).status_code == 200
        assert client.delete(f"/api/subscribers/{sid}", headers=h).json() == {"deleted": True}
        assert client.get(f"/api/subscribers/{sid}", headers=h).status_code == 401

    def test_saved_places_and_departure_can_be_updated_and_survive_a_restart(self, client):
        s, h = signup(client)
        hlat, hlon = ll("n0_-4")
        wlat, wlon = ll("n0_4")
        r = client.post(f"/api/subscribers/{s}/places", headers=h, json={
            "home_lat": hlat, "home_lon": hlon, "work_lat": wlat, "work_lon": wlon, "depart_hhmm": "08:30"})
        assert r.status_code == 200 and r.json()["has_home"] and r.json()["has_work"] and r.json()["depart_at"]
        assert client.post(f"/api/subscribers/{s}/places", headers=h, json={}).status_code == 422
        assert client.post(f"/api/subscribers/{s}/places", headers=h, json={"home_lat": 1.0}).status_code == 422
        assert client.post(f"/api/subscribers/{s}/places", json={"depart_hhmm": "09:00"}).status_code == 401
        from app.main import persist_subscribers
        from app.store import store
        persist_subscribers()
        from app import alerts
        from app.main import S
        doc = next(d for d in S.db.load_subscribers() if d["id"] == s)
        back = alerts.Subscriber.from_doc(doc)
        assert back.home and back.work and back.depart_hhmm == "08:30" and back.token == store.subscribers[s].token

    def test_only_people_whose_route_uses_the_road_are_warned(self, client, officer_headers):
        a, ha = signup(client, name="on route")
        b, hb = signup(client, name="elsewhere")
        trip(client, a, ha, "n0_4")
        trip(client, b, hb, "n4_0")
        at(client, a, ha, node="n0_-4")           # drives along row 0, through the accident
        at(client, b, hb, node="n-4_-4")          # a different part of town, going elsewhere
        publish_accident(client, officer_headers, "n0_0", "n0_1")
        at(client, a, ha, node="n0_-3")
        at(client, b, hb, node="n-4_-3")
        inbox_a = client.get(f"/api/subscribers/{a}/alerts", headers=ha).json()["alerts"]
        inbox_b = client.get(f"/api/subscribers/{b}/alerts", headers=hb).json()["alerts"]
        assert [x["kind"] for x in inbox_a] == ["in_trip"]
        assert inbox_b == []

    def test_nobody_far_away_is_warned_and_there_is_no_fixed_radius(self, client, officer_headers):
        s, h = signup(client)
        trip(client, s, h, "n-4_-4")              # heading AWAY from the accident
        at(client, s, h, node="n-3_-3")
        publish_accident(client, officer_headers, "n0_0", "n0_1")
        at(client, s, h, node="n-3_-4")
        assert client.get(f"/api/subscribers/{s}/alerts", headers=h).json()["alerts"] == []

    def test_voice_wording_is_short_and_one_action(self, client, officer_headers):
        s, h = signup(client, voice=True)
        trip(client, s, h, "n0_4")
        at(client, s, h, node="n0_-4")
        publish_accident(client, officer_headers)      # the alert is delivered the moment it is verified
        a = client.get(f"/api/subscribers/{s}/alerts", headers=h).json()["alerts"][0]
        assert a["speak"] is True and len(a["voice"]) < 90
        assert a["voice"].startswith("Accident ahead on")
        assert any(w in a["voice"] for w in ("Turn left", "Turn right", "Continue straight"))
        assert a["extra_min"] >= 0 and a["route"]


# ================================================================ geofence: set off AFTER the incident
class TestStandingIncident:
    def test_someone_who_starts_after_verification_is_warned_when_it_becomes_relevant(self, client, officer_headers):
        publish_accident(client, officer_headers)            # confirmed FIRST
        s, h = signup(client)                                # then Ravi sets off
        trip(client, s, h, "n0_4")
        r = at(client, s, h, node="n0_-4")
        assert r["alert"] and r["alert"]["kind"] == "in_trip"
        assert r["alert"]["voice"].startswith("Accident ahead on")

    def test_an_incident_confirmed_while_someone_is_driving_warns_them_at_once(self, client, officer_headers):
        s, h = signup(client)
        trip(client, s, h, "n0_4")
        at(client, s, h, node="n0_-3")
        assert client.get(f"/api/subscribers/{s}/alerts", headers=h).json()["alerts"] == []
        publish_accident(client, officer_headers)            # confirmed under their wheels
        alerts = client.get(f"/api/subscribers/{s}/alerts", headers=h).json()["alerts"]
        assert [a["kind"] for a in alerts] == ["in_trip"]

    def test_each_person_is_warned_once_per_incident(self, client, officer_headers):
        s, h = signup(client)
        trip(client, s, h, "n0_4")
        at(client, s, h, node="n0_-4")
        publish_accident(client, officer_headers)
        for n in ("n0_-3", "n0_-2", "n0_-2"):
            assert at(client, s, h, node=n)["alert"] is None
        assert len(client.get(f"/api/subscribers/{s}/alerts", headers=h).json()["alerts"]) == 1


# ================================================================ the turn-off timing
class TestSeverityAndSilence:
    def test_a_slow_but_passable_road_that_is_still_the_best_way_sends_no_alert(self, client, officer_headers):
        s, h = signup(client)
        trip(client, s, h, "n0_4")
        at(client, s, h, node="n0_-4")
        iid = publish_accident(client, officer_headers, severity="Low")      # one lane blocked: +30% on one block
        assert at(client, s, h, node="n0_-3")["alert"] is None
        assert client.get(f"/api/subscribers/{s}/alerts", headers=h).json()["alerts"] == []
        # the officer then closes the road: they are checked again and told
        client.post(f"/api/incidents/{iid}/respond", json={"severity": "Closed"}, headers=officer_headers)
        kinds = [a["kind"] for a in client.get(f"/api/subscribers/{s}/alerts", headers=h).json()["alerts"]]
        assert kinds == ["in_trip"]

    def test_severity_chosen_by_the_officer_sticks(self, client, officer_headers):
        iid = publish_accident(client, officer_headers, severity="Closed")
        d = client.get(f"/api/incidents/{iid}", headers=officer_headers).json()
        assert d["severity"] == "Closed"
        lat, lon = mid("n0_0", "n0_1")
        client.post("/api/reports", json={"type": "accident", "lat": lat, "lon": lon, "reporter_id": "c2"})
        assert client.get(f"/api/incidents/{iid}", headers=officer_headers).json()["severity"] == "Closed"


class TestLatestTurnOff:
    def test_the_warning_is_for_the_last_reasonable_turn_off_not_the_first_possible_one(self, client, officer_headers,
                                                                                         monkeypatch):
        from app import config
        monkeypatch.setattr(config, "ALERT_WARN_HORIZON_S", 999)    # decide the turn-off, ignore timing here
        s, h = signup(client)
        trip(client, s, h, "n0_4")
        at(client, s, h, node="n0_-4")
        publish_accident(client, officer_headers, "n0_0", "n0_1")   # 600 m ahead: n0_0 is the last junction before it
        a = client.get(f"/api/subscribers/{s}/alerts", headers=h).json()["alerts"][0]
        assert 550 <= a["dist_m"] <= 650, a                         # at the junction beside the incident, not 150 m in

    def test_with_no_slack_the_earliest_best_way_round_is_used(self, client, officer_headers, monkeypatch):
        from app import config
        monkeypatch.setattr(config, "ALERT_WARN_HORIZON_S", 999)
        monkeypatch.setattr(config, "ALERT_TURNOFF_SLACK_S", 0)
        s, h = signup(client)
        trip(client, s, h, "n0_4")
        at(client, s, h, node="n0_-4")
        publish_accident(client, officer_headers, "n0_0", "n0_1")
        a = client.get(f"/api/subscribers/{s}/alerts", headers=h).json()["alerts"][0]
        assert a["dist_m"] <= 600


class TestDecisionPoint:
    def test_not_yet_when_the_turn_off_is_far_then_warned_inside_the_horizon(self, client, officer_headers, monkeypatch):
        from app import config
        monkeypatch.setattr(config, "ALERT_WARN_HORIZON_S", 30)   # 30 s at 9 m/s = 270 m
        monkeypatch.setattr(config, "ALERT_MIN_EVAL_GAP_S", 0)
        s, h = signup(client)
        trip(client, s, h, "n0_4")
        publish_accident(client, officer_headers)            # 600 m ahead of n0_-4: too far to bother them yet
        assert at(client, s, h, node="n0_-4")["alert"] is None
        assert at(client, s, h, node="n0_-3")["alert"] is None
        first = None
        for t in (0.0, 0.2, 0.4, 0.6, 0.8):                  # now creep along the next block
            lat, lon = between("n0_-2", "n0_-1", t)
            first = at(client, s, h, lat=lat, lon=lon)["alert"]
            if first:
                break
        assert first and first["kind"] == "in_trip" and first["lead_s"] <= 30

    def test_past_the_last_turn_off_the_person_is_told_to_turn_back(self, client, officer_headers):
        s, h = signup(client)
        trip(client, s, h, "n0_4")
        # a driver already on the blocked block, before the accident: nothing ahead can be turned into
        publish_accident(client, officer_headers, "n0_0", "n0_1")
        lat, lon = ll("n0_0")
        lat2, lon2 = ll("n0_1")
        r = at(client, s, h, lat=lat + (lat2 - lat) * 0.30, lon=lon + (lon2 - lon) * 0.30)
        assert r["alert"]["kind"] == "uturn", r["alert"]
        assert "Turn back" in r["alert"]["voice"] and r["alert"]["route"]

    def test_a_late_verification_is_flagged_as_a_late_warning(self, client, officer_headers):
        from app import config
        s, h = signup(client)
        trip(client, s, h, "n0_4")
        lat, lon = between("n0_-1", "n0_0", 0.5)             # 75 m from the junction: about 8 s
        at(client, s, h, lat=lat, lon=lon)
        publish_accident(client, officer_headers, "n0_0", "n0_1")
        a = client.get(f"/api/subscribers/{s}/alerts", headers=h).json()["alerts"][0]
        assert a["late"] is True and a["lead_s"] < config.ALERT_MIN_LEAD_S


# ================================================================ regular commuters: before they leave
class TestPreTrip:
    def test_pretrip_alert_goes_out_before_departure_and_names_the_way_round(self, client, officer_headers):
        hlat, hlon = ll("n0_-4")
        wlat, wlon = ll("n0_4")
        s, h = signup(client, home_lat=hlat, home_lon=hlon, work_lat=wlat, work_lon=wlon)
        client.post(f"/api/subscribers/{s}/trip", json={"depart_in_min": 10}, headers=h)   # leaves in 10 min
        publish_accident(client, officer_headers)
        a = client.get(f"/api/subscribers/{s}/alerts", headers=h).json()["alerts"]
        assert [x["kind"] for x in a] == ["pre_trip"]
        assert "usual route" in a[0]["text"] and a[0]["extra_min"] >= 0

    def test_no_pretrip_alert_when_departure_is_far_off(self, client, officer_headers):
        hlat, hlon = ll("n0_-4")
        wlat, wlon = ll("n0_4")
        s, h = signup(client, home_lat=hlat, home_lon=hlon, work_lat=wlat, work_lon=wlon)
        client.post(f"/api/subscribers/{s}/trip", json={"depart_in_min": 300}, headers=h)
        publish_accident(client, officer_headers)
        assert client.get(f"/api/subscribers/{s}/alerts", headers=h).json()["alerts"] == []

    def test_planned_construction_in_the_window_is_in_the_pretrip_alert(self, client, officer_headers):
        hlat, hlon = ll("n0_-4")
        wlat, wlon = ll("n0_4")
        s, h = signup(client, home_lat=hlat, home_lon=hlon, work_lat=wlat, work_lon=wlon)
        lat, lon = mid("n0_0", "n0_1")
        now = time.time()
        client.post("/api/planned-works", headers=officer_headers, json={
            "title": "Pipe laying", "lat": lat, "lon": lon, "starts": now + 600, "ends": now + 7200,
            "agency": "GVMC"})
        client.post(f"/api/subscribers/{s}/trip", json={"depart_in_min": 10}, headers=h)   # leaves as work starts
        a = client.get(f"/api/subscribers/{s}/alerts", headers=h).json()["alerts"]
        assert [x["kind"] for x in a] == ["pre_trip"] and a[0]["title"].startswith("Construction")


# ================================================================ the all-clear
class TestClear:
    def test_everyone_who_was_warned_hears_when_the_road_reopens(self, client, officer_headers):
        s, h = signup(client)
        trip(client, s, h, "n0_4")
        at(client, s, h, node="n0_-4")
        iid = publish_accident(client, officer_headers)
        at(client, s, h, node="n0_-3")
        assert client.post(f"/api/incidents/{iid}/clear", headers=officer_headers).status_code == 200
        kinds = [a["kind"] for a in client.get(f"/api/subscribers/{s}/alerts", headers=h).json()["alerts"]]
        assert kinds == ["in_trip", "clear"]

    def test_someone_who_was_never_warned_gets_no_all_clear(self, client, officer_headers):
        s, h = signup(client)
        iid = publish_accident(client, officer_headers)
        client.post(f"/api/incidents/{iid}/clear", headers=officer_headers)
        assert client.get(f"/api/subscribers/{s}/alerts", headers=h).json()["alerts"] == []


# ================================================================ boards: people with no app
class TestBoards:
    def test_boards_are_few_and_blank_when_there_is_no_incident(self, client):
        boards = client.get("/api/boards").json()["boards"]
        assert 1 <= len(boards) <= 4
        assert all(b["lines"] == [] and b["simulated"] for b in boards)

    def test_a_board_near_the_incident_warns_which_road_to_avoid(self, client, officer_headers):
        publish_accident(client, officer_headers, "n0_0", "n0_1")
        boards = client.get("/api/boards").json()["boards"]
        warned = [b for b in boards if b["lines"]]
        assert warned, "at least one board within the look-ahead distance must warn"
        b = warned[0]
        assert b["lines"][0]["title"] == "Accident" and b["lines"][0]["metres"] <= 600
        assert b["alternatives"], "and it must name roads that are still fine"

    def test_board_goes_blank_when_the_incident_clears(self, client, officer_headers):
        iid = publish_accident(client, officer_headers)
        client.post(f"/api/incidents/{iid}/clear", headers=officer_headers)
        assert all(b["lines"] == [] for b in client.get("/api/boards").json()["boards"])

    def test_unknown_board_is_404(self, client):
        assert client.get("/api/boards/nope").status_code == 404
