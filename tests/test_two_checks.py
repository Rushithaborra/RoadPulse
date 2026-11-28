"""The agreed review flow for citizen reports: control room check, then field police check; urgent fast-track."""
from conftest import DEV


def report(client, kind="accident", dlat=0.0015):
    lat, lon = client.get("/api/health").json()["center"]
    r = client.post("/api/reports", json={"type": kind, "lat": lat + dlat, "lon": lon, "reporter_id": "citizen-1",
                                          "note": "seen from the bus stop"})
    assert r.status_code == 201, r.text
    return r.json()["incident_id"]


def public_ids(client):
    return [i["id"] for i in client.get("/api/incidents").json()["incidents"]]


def test_both_checks_are_needed_and_the_field_team_sees_only_forwarded_reports(client, officer_headers, field_headers):
    iid = report(client)
    inc = client.get(f"/api/incidents/{iid}", headers=officer_headers).json()
    assert inc["review"]["stage"] == "control_review" and inc["review"]["two_check"] is True
    assert client.get("/api/field/tasks", headers=field_headers).json()["tasks"] == [], "not forwarded yet"

    c = client.post(f"/api/incidents/{iid}/verify", json={"severity": "Severe"}, headers=officer_headers).json()
    assert c["review"]["stage"] == "with_field" and c["review"]["checks"]["control"]["by"] == "officer"
    assert iid not in public_ids(client), "one check is not enough"
    tasks = client.get("/api/field/tasks", headers=field_headers).json()["tasks"]
    assert [t["id"] for t in tasks] == [iid] and tasks[0]["signals"], "the field officer sees the report and evidence"

    f = client.post(f"/api/field/incidents/{iid}/confirm", json={"note": "lorry across both lanes", "severity": "Closed"},
                    headers=field_headers).json()
    assert f["status"] == "Verified" and f["published"] and f["severity"] == "Closed"
    assert f["review"]["stage"] == "confirmed" and f["review"]["checks"]["field"]["by"] == "field1"
    assert iid in public_ids(client)
    assert client.get("/api/field/tasks", headers=field_headers).json()["tasks"] == []
    # the public view says it was confirmed but does not name the officers
    pub = next(i for i in client.get("/api/incidents").json()["incidents"] if i["id"] == iid)
    assert pub["review"]["stage"] == "confirmed" and "checks" not in pub["review"]


def test_field_cannot_confirm_before_the_control_room(client, field_headers):
    iid = report(client)
    r = client.post(f"/api/field/incidents/{iid}/confirm", headers=field_headers)
    assert r.status_code == 409 and "not been sent to the field" in r.json()["detail"]
    assert iid not in public_ids(client)


def test_not_found_on_the_ground_rejects_the_report(client, officer_headers, field_headers):
    iid = report(client, "blockage")
    client.post(f"/api/incidents/{iid}/verify", headers=officer_headers)
    r = client.post(f"/api/field/incidents/{iid}/not-found", json={"note": "road clear"}, headers=field_headers).json()
    assert r["status"] == "Cleared" and r["review"]["stage"] == "rejected"
    st = client.get(f"/api/reports/{iid}/status").json()
    assert st["outcome"] == "rejected" and st["published"] is False


def test_urgent_publishes_now_and_still_asks_the_field_team(client, officer_headers, field_headers):
    iid = report(client)
    u = client.post(f"/api/incidents/{iid}/verify", json={"urgent": True, "severity": "Closed",
                                                         "reason": "caller reports injured rider"},
                    headers=officer_headers).json()
    assert u["published"] and u["review"]["urgent"] and u["review"]["stage"] == "urgent_awaiting_field"
    assert iid in public_ids(client)
    normal = report(client, "blockage", dlat=-0.0015)
    client.post(f"/api/incidents/{normal}/verify", headers=officer_headers)
    tasks = [t["id"] for t in client.get("/api/field/tasks", headers=field_headers).json()["tasks"]]
    assert tasks == [iid, normal], "urgent first"

    # a field "not found" does not silently withdraw an urgent incident: it goes back to the control room
    d = client.post(f"/api/field/incidents/{iid}/not-found", headers=field_headers).json()
    assert d["published"] and d["review"]["stage"] == "field_disputed"
    assert client.post(f"/api/incidents/{iid}/clear", json={"reason": "checked by phone"},
                       headers=officer_headers).json()["status"] == "Cleared"


def test_urgent_action_name_works_too(client, officer_headers):
    iid = report(client)
    r = client.post(f"/api/incidents/{iid}/urgent", headers=officer_headers).json()
    assert r["review"]["urgent"] and r["published"]


def test_no_diversion_on_an_unconfirmed_citizen_report(client, officer_headers):
    iid = report(client)
    r = client.post(f"/api/incidents/{iid}/respond", headers=officer_headers)
    assert r.status_code == 409 and "field police" in r.json()["detail"]
    client.post(f"/api/incidents/{iid}/verify", headers=officer_headers)
    assert client.post(f"/api/incidents/{iid}/respond", headers=officer_headers).status_code == 409


def test_desks_are_separate(client, officer_headers, field_headers):
    iid = report(client)
    r = client.post(f"/api/incidents/{iid}/verify", headers=field_headers)
    assert r.status_code == 403 and "control room" in r.json()["detail"]
    client.post(f"/api/incidents/{iid}/verify", headers=officer_headers)
    r = client.post(f"/api/field/incidents/{iid}/confirm", headers=officer_headers)
    assert r.status_code == 403 and "field police" in r.json()["detail"]
    assert client.get("/api/field/tasks").status_code == 401
    # both desks may open the live stream
    assert client.post("/api/stream/ticket", headers=field_headers).status_code == 200
    assert client.post("/api/stream/ticket", headers=officer_headers).status_code == 200


def test_sensor_evidence_keeps_the_single_decision_rule(client, officer_headers):
    for cm in (32, 34):
        res = client.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": cm}, headers=DEV).json()
    inc = client.get(f"/api/incidents/{res['incident']}", headers=officer_headers).json()
    assert inc["published"] and inc["review"]["stage"] == "trusted_evidence" and inc["review"]["two_check"] is False
    assert client.post(f"/api/incidents/{inc['id']}/respond", headers=officer_headers).json()["status"] == "Responding"


def test_review_survives_a_restart(client, officer_headers):
    iid = report(client)
    client.post(f"/api/incidents/{iid}/verify", headers=officer_headers)
    from app.main import S
    reloaded = {i.id: i for i in S.db.load_open_incidents()}
    assert reloaded[iid].checks["control"]["decision"] == "forwarded"
