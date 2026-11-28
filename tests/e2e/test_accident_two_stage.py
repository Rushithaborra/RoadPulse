"""An accident and the two-stage check, in real browsers: a citizen report is never published until the control room
has sent a police unit, the unit has confirmed on the scene, and the control room has confirmed in turn."""
import re

from playwright.sync_api import expect

from .conftest import CENTER, control_login, offset, police_login


def test_accident_report_needs_police_on_the_scene_then_the_control_room(live, pages):
    live.clear_everything()
    lat, lon = offset(*CENTER, -150, -75)                    # a side street west of the centre, the same spot the simulator uses

    # a citizen reports an accident (over the real API: the report form itself is covered by the flood story)
    rep = live.post("/api/reports", {"type": "accident", "lat": lat, "lon": lon, "reporter_id": "e2e-citizen", "note": "two bikes down", "simulated": True})
    assert rep.status_code == 201 and rep.json()["status"] == "Reported"
    iid = rep.json()["incident_id"]

    police = pages.open("/police", geo=(lat + 0.0006, lon + 0.0004), label="police")        # a unit a little way off, sharing its location
    police_login(police)
    expect(police.get_by_role("heading", name="No calls right now")).to_be_visible()

    control = pages.open("/control", label="control room")
    control_login(control)
    card = control.locator(".prob.pending", has_text="Possible accident")
    expect(card).to_be_visible(timeout=15000)
    card.click()

    # stage 0: nothing is published and the control room has no confirm button, only "send police" (and a recorded urgent route)
    assert live.incidents("public") == []
    expect(control.get_by_text("Send the nearest police unit to check")).to_be_visible()
    assert control.locator('[data-act="confirm"]').count() == 0
    expect(control.locator("[data-evidence]")).to_contain_text("driver report")

    # stage 1: the control room sends the unit
    send = control.locator('[data-act="assign"][data-unit="unit1"]')
    expect(send).to_be_visible(timeout=15000)
    send.click()
    expect(control.locator("#toast")).to_contain_text("unit1 is being sent")
    assert live.incidents("public") == []

    # the unit gets the call and walks through the steps on its phone
    call = police.locator(".prob", has_text="Accident")
    expect(call).to_be_visible(timeout=15000)
    call.click()
    expect(police.locator(".hero")).to_contain_text("New call")
    police.locator('[data-st="accepted"]').click()
    police.locator('[data-st="on_way"]').click()
    police.locator('[data-st="arrived"]').click()
    police.locator('[data-find="blocked"]').click()
    police.locator("#note").fill("two bikes down, lane blocked")
    police.locator('[data-act="send"]').click()
    expect(police.locator(".hero")).to_contain_text("Sent to the control room")
    assert live.incidents("public") == [], "police alone do not publish it"

    # stage 2: the control room sees the finding and confirms; only now are drivers warned
    expect(control.locator(".hero", has_text="Police confirmed")).to_be_visible(timeout=15000)
    confirm = control.locator('[data-act="confirm"]').first
    expect(confirm).to_be_visible()
    confirm.click()
    expect(control.locator("#toast")).to_contain_text("Confirmed")
    pub = live.incidents("public")
    assert len(pub) == 1 and pub[0]["id"] == iid and pub[0]["type"] == "accident" and pub[0]["severity"] == "Closed"

    commuter = pages.open("/", label="commuter")
    expect(commuter.locator(".prob")).to_contain_text("Accident", timeout=15000)

    # the unit says the road is open; the control room clears it (two taps) and the commuter sees all-clear
    call.click() if police.locator(".prob", has_text="Accident").count() else None
    police.locator('[data-act="open"]').click()
    expect(police.locator("#toast")).to_contain_text("road is open")
    control.locator(".prob", has_text="Accident").click()
    expect(control.locator(".hero", has_text="Police say the road is open")).to_be_visible(timeout=15000)
    clear = control.locator('[data-act="clear"]').first
    clear.click()
    expect(clear).to_contain_text("Tap again to confirm")
    clear.click()
    expect(control.locator("#toast")).to_contain_text("Road marked clear")
    expect(commuter.locator(".hero.ok")).to_contain_text("All clear", timeout=15000)
    assert not pages.problems(), pages.problems()


def test_the_urgent_route_is_recorded_and_warns_immediately(live, pages):
    live.clear_everything()
    lat, lon = offset(*CENTER, 120, 60)
    iid = live.post("/api/reports", {"type": "blockage", "lat": lat, "lon": lon, "reporter_id": "e2e-urgent"}).json()["incident_id"]
    control = pages.open("/control", label="control room")
    control_login(control)
    control.locator(".prob.pending", has_text="Possible road blocked").click()
    control.get_by_text("Emergency? Warn drivers now").click()
    tile = control.locator('[data-act="urgent"][data-sev="Closed"]')
    tile.click()
    expect(tile).to_contain_text("Tap again: warn drivers now")                 # two taps: it cannot be undone
    tile.click()
    expect(control.locator("#toast")).to_contain_text("Recorded as urgent")
    inc = live.get(f"/api/incidents/{iid}", headers=live.officer).json()
    assert inc["status"] == "Verified" and inc["urgent"] is True
    audit = live.get("/api/audit", headers=live.officer).text
    assert iid in audit and "urgent" in audit.lower(), "the fast-track is in the audit log"
    live.clear_everything()
    assert not pages.problems(), pages.problems()
