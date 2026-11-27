"""Two-step validation: a citizen report is checked by traffic police on the ground, then confirmed by the control room.
(tests/conftest.py switches the rule off for the older tests; these switch it back on.)"""
import time

import pytest

from conftest import DEV


@pytest.fixture(autouse=True)
def field_rule_on(monkeypatch):
    monkeypatch.setenv("REQUIRE_FIELD_CONFIRMATION", "1")
    from app import security
    for lim in (security.REPORT_LIMIT, security.DEVICE_LIMIT, security.SUBSCRIBE_LIMIT):
        monkeypatch.setattr(lim, "burst", 100000)
        monkeypatch.setattr(lim, "rate", 100000.0)
        lim.state.clear()


def spot(node="n0_0"):
    from app.main import S
    return S.rg.node_latlon(node)


def report(client, node="n0_0", kind="accident"):
    lat, lon = spot(node)
    return client.post("/api/reports", json={"type": kind, "lat": lat, "lon": lon, "reporter_id": "citizen"}).json()["incident_id"]


def police(client, name="unit12", pw="police-pass"):
    r = client.post("/api/auth/login", json={"username": name, "password": pw})
    assert r.status_code == 200 and r.json()["role"] == "police"
    return {"Authorization": f"Bearer {r.json()['token']}"}


def view(client, officer_headers, iid):
    return client.get(f"/api/incidents/{iid}", headers=officer_headers).json()


def public(client):
    return client.get("/api/incidents").json()["incidents"]


class TestRuleOfTwo:
    def test_the_control_room_cannot_publish_a_citizen_report_before_police_check_it(self, client, officer_headers):
        iid = report(client)
        r = client.post(f"/api/incidents/{iid}/verify", json={"severity": "Closed"}, headers=officer_headers)
        assert r.status_code == 409 and "Send a unit first" in r.json()["detail"]
        assert public(client) == []
        assert view(client, officer_headers, iid)["needs_field"] is True

    def test_an_emergency_can_be_fast_tracked_and_is_recorded(self, client, officer_headers):
        iid = report(client)
        r = client.post(f"/api/incidents/{iid}/verify", json={"severity": "Closed", "urgent": True, "reason": "112 call"},
                        headers=officer_headers)
        assert r.status_code == 200 and r.json()["urgent"] is True
        assert len(public(client)) == 1
        audit = client.get("/api/audit", headers=officer_headers).json()["entries"]
        assert any(e["action"] == "urgent_publish" for e in audit)

    def test_full_flow_report_assign_check_confirm(self, client, officer_headers):
        iid = report(client)
        assert client.post(f"/api/incidents/{iid}/assign", json={"unit": "unit12"}, headers=officer_headers).status_code == 201
        p = police(client)
        calls = client.get("/api/police/calls", headers=p).json()["calls"]
        assert [c["id"] for c in calls] == [iid] and calls[0]["my_status"] == "assigned"
        for st in ("accepted", "on_way", "arrived"):
            assert client.post(f"/api/police/calls/{iid}/status", json={"status": st}, headers=p).status_code == 200
        assert view(client, officer_headers, iid)["field_state"] == "arrived"
        # police see it is blocked: a strong signal, but drivers are NOT warned yet
        r = client.post(f"/api/police/calls/{iid}/report", json={"finding": "blocked", "need": ["ambulance"]}, headers=p)
        assert r.status_code == 200
        d = view(client, officer_headers, iid)
        assert d["status"] == "Reported" and d["field_state"] == "confirmed" and d["field_severity"] == "Closed"
        assert d["needs_field"] is False and public(client) == []
        # the control room confirms with one tap and the police finding becomes the severity
        assert client.post(f"/api/incidents/{iid}/verify", json={}, headers=officer_headers).status_code == 200
        pub = public(client)
        assert len(pub) == 1 and pub[0]["severity"] == "Closed"

    def test_one_lane_means_slow_not_blocked(self, client, officer_headers):
        iid = report(client)
        client.post(f"/api/incidents/{iid}/assign", json={"unit": "unit12"}, headers=officer_headers)
        p = police(client)
        client.post(f"/api/police/calls/{iid}/report", json={"finding": "one_lane"}, headers=p)
        client.post(f"/api/incidents/{iid}/verify", json={}, headers=officer_headers)
        assert public(client)[0]["severity"] == "Medium"

    def test_police_find_nothing_and_the_control_room_dismisses(self, client, officer_headers):
        iid = report(client)
        client.post(f"/api/incidents/{iid}/assign", json={"unit": "unit12"}, headers=officer_headers)
        p = police(client)
        client.post(f"/api/police/calls/{iid}/report", json={"finding": "not_real", "note": "nothing here"}, headers=p)
        d = view(client, officer_headers, iid)
        assert d["field_state"] == "not_real" and d["status"] == "Reported" and public(client) == []
        assert d["needs_field"] is True                                     # police did not confirm it
        assert client.post(f"/api/incidents/{iid}/reject", json={}, headers=officer_headers).status_code == 200

    def test_trusted_sources_do_not_wait_for_police(self, client, officer_headers):
        import time as t
        lat, lon = spot("n0_1")
        now = t.time()
        w = client.post("/api/planned-works", headers=officer_headers, json={
            "title": "Pipe", "lat": lat, "lon": lon, "starts": now - 60, "ends": now + 3600}).json()
        assert w["needs_field"] is False


class TestWhoMayDoWhat:
    def test_the_two_roles_cannot_use_each_others_endpoints(self, client, officer_headers):
        iid = report(client)
        client.post(f"/api/incidents/{iid}/assign", json={"unit": "unit12"}, headers=officer_headers)
        p = police(client)
        assert client.get("/api/police/calls", headers=officer_headers).status_code == 401
        assert client.post(f"/api/incidents/{iid}/verify", json={"urgent": True}, headers=p).status_code == 401
        assert client.post(f"/api/incidents/{iid}/assign", json={"unit": "unit7"}, headers=p).status_code == 401
        assert client.get("/api/incidents?scope=all", headers=p).status_code == 401      # no control-room view for police
        assert client.get("/api/police/calls").status_code == 401

    def test_a_unit_can_only_report_on_its_own_calls(self, client, officer_headers):
        iid = report(client)
        client.post(f"/api/incidents/{iid}/assign", json={"unit": "unit12"}, headers=officer_headers)
        other = police(client, "unit7", "police-pass-2")
        assert client.get("/api/police/calls", headers=other).json()["calls"] == []
        r = client.post(f"/api/police/calls/{iid}/report", json={"finding": "blocked"}, headers=other)
        assert r.status_code == 403
        assert client.post(f"/api/incidents/{iid}/assign", json={"unit": "nobody"}, headers=officer_headers).status_code == 404

    def test_a_declined_call_leaves_the_unit_and_the_control_room_can_reassign(self, client, officer_headers):
        iid = report(client)
        client.post(f"/api/incidents/{iid}/assign", json={"unit": "unit12"}, headers=officer_headers)
        p = police(client)
        client.post(f"/api/police/calls/{iid}/status", json={"status": "declined"}, headers=p)
        assert client.get("/api/police/calls", headers=p).json()["calls"] == []
        assert view(client, officer_headers, iid)["field_state"] == "none"
        client.post(f"/api/incidents/{iid}/assign", json={"unit": "unit7"}, headers=officer_headers)
        assert view(client, officer_headers, iid)["field_state"] == "assigned"


class TestHelpAndPlaces:
    def test_police_ask_for_help_and_the_control_room_answers(self, client, officer_headers):
        iid = report(client)
        client.post(f"/api/incidents/{iid}/assign", json={"unit": "unit12"}, headers=officer_headers)
        p = police(client)
        client.post(f"/api/police/calls/{iid}/report", json={"finding": "blocked", "need": ["ambulance", "tow"]}, headers=p)
        assert {r["kind"] for r in view(client, officer_headers, iid)["requests"]} == {"ambulance", "tow"}
        client.post(f"/api/incidents/{iid}/dispatch", json={"unit": "ambulance"}, headers=officer_headers)
        assert {r["kind"] for r in view(client, officer_headers, iid)["requests"]} == {"tow"}

    def test_nearest_unit_is_suggested_from_shared_positions(self, client, officer_headers):
        iid = report(client, "n0_0")
        near, far = spot("n0_1"), spot("n4_4")
        p12, p7 = police(client), police(client, "unit7", "police-pass-2")
        client.post("/api/police/position", json={"lat": near[0], "lon": near[1]}, headers=p12)
        client.post("/api/police/position", json={"lat": far[0], "lon": far[1]}, headers=p7)
        units = client.get(f"/api/incidents/{iid}/units", headers=officer_headers).json()["units"]
        assert [u["unit"] for u in units] == ["unit12", "unit7"] and units[0]["metres"] < units[1]["metres"]
        assert units[0]["eta_min"] >= 1

    def test_police_say_the_road_is_open_and_the_control_room_clears_it(self, client, officer_headers):
        iid = report(client)
        client.post(f"/api/incidents/{iid}/assign", json={"unit": "unit12"}, headers=officer_headers)
        p = police(client)
        client.post(f"/api/police/calls/{iid}/report", json={"finding": "blocked"}, headers=p)
        client.post(f"/api/incidents/{iid}/verify", json={}, headers=officer_headers)
        client.post(f"/api/police/calls/{iid}/report", json={"finding": "open"}, headers=p)
        assert view(client, officer_headers, iid)["field_state"] == "open"
        assert len(public(client)) == 1                                  # still shown until the control room clears it
        client.post(f"/api/incidents/{iid}/clear", json={}, headers=officer_headers)
        assert public(client) == []


class TestDriversAreWarnedOnlyAfterBoth:
    def test_a_commuter_is_alerted_after_the_control_room_confirms_not_when_police_report(self, client, officer_headers):
        from app.main import S
        a, b = S.rg.node_latlon("n0_-4"), S.rg.node_latlon("n0_4")
        sub = client.post("/api/subscribers", json={"name": "R", "vehicle": "car", "consent_location": True}).json()
        h = {"X-Subscriber-Token": sub["token"]}
        client.post(f"/api/subscribers/{sub['id']}/trip", json={"lat": b[0], "lon": b[1]}, headers=h)
        client.post(f"/api/subscribers/{sub['id']}/position", json={"lat": a[0], "lon": a[1]}, headers=h)
        iid = report(client, "n0_0")
        client.post(f"/api/incidents/{iid}/assign", json={"unit": "unit12"}, headers=officer_headers)
        client.post(f"/api/police/calls/{iid}/report", json={"finding": "blocked"}, headers=police(client))
        assert client.get(f"/api/subscribers/{sub['id']}/alerts", headers=h).json()["alerts"] == []   # not yet
        client.post(f"/api/incidents/{iid}/verify", json={}, headers=officer_headers)
        kinds = [x["kind"] for x in client.get(f"/api/subscribers/{sub['id']}/alerts", headers=h).json()["alerts"]]
        assert kinds == ["in_trip"]


class TestUnitsOnTheMap:
    def test_control_room_sees_where_units_are_and_which_incidents_they_are_sent_to(self, client, officer_headers):
        iid = report(client)
        client.post(f"/api/incidents/{iid}/assign", json={"unit": "unit12"}, headers=officer_headers)
        lat, lon = spot("n0_1")
        client.post("/api/police/position", json={"lat": lat, "lon": lon}, headers=police(client))
        units = {u["unit"]: u for u in client.get("/api/units", headers=officer_headers).json()["units"]}
        assert units["unit12"]["lat"] == lat and units["unit12"]["incidents"] == [iid] and units["unit12"]["age_s"] <= 2
        assert units["unit7"]["lat"] is None and units["unit7"]["incidents"] == []     # has not shared a location
        assert client.get("/api/units").status_code == 401


class TestPlaceSearch:
    def test_people_can_search_roads_and_landmarks_by_name(self, client):
        all_ = client.get("/api/places").json()["places"]
        assert all_ and all_[0]["name"].startswith("Dwaraka Nagar")
        hits = client.get("/api/places?q=main").json()["places"]
        assert hits and all("main" in p["name"].lower() for p in hits)
        from app.main import S
        assert all(S.rg.distance_to_network(p["lat"], p["lon"]) < 5 for p in hits)    # each point is on the road
        assert client.get("/api/places?q=zzzzzz").json()["places"] == []

    def test_names_that_start_with_the_search_come_first(self, client):
        names = [p["name"] for p in client.get("/api/places?q=synthetic s").json()["places"]]
        assert names and all(n.lower().startswith("synthetic s") for n in names[:3])
