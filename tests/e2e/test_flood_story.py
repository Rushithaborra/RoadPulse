"""The whole virtual-hackathon flood story, played by real browsers against a real server on the real OSM map.

Commuter plans a trip -> a citizen reports waterlogging -> simulated sensor readings arrive -> the system verifies ->
the control room reviews the evidence -> the officer approves a diversion -> the commuter's route changes and a
subscriber is warned -> the simulated roadside board warns -> recovery readings arrive -> the officer resolves ->
everyone gets the all-clear and the original route is back."""
import re

from playwright.sync_api import expect

from .conftest import OFFICER, click_map, offset, zoom_to


def control_login(page):
    expect(page.locator("#btn-login")).to_be_visible()
    page.locator("#pass").fill(OFFICER[1])
    page.locator("#btn-login").click()
    expect(page.get_by_role("heading", name="Queue")).to_be_visible()


def test_the_flood_story_end_to_end(live, pages):
    live.clear_everything()
    hs = live.hotspot()
    lat, lon = hs["lat"], hs["lon"]
    assert hs["simulated_location"] is True

    # -- 1. A commuter opens RoadPulse and chooses a route across the road that will flood (a two-wheeler)
    commuter = pages.open("/", label="commuter")
    expect(commuter.locator(".hero.ok")).to_contain_text("All clear")
    zoom_to(commuter, lat, lon, 18)
    click_map(commuter, *offset(lat, lon, -110, 0))
    click_map(commuter, *offset(lat, lon, 110, 0))
    commuter.locator('[data-veh="two_wheeler"]').click()
    expect(commuter.locator(".hero")).to_contain_text("Your way is clear")
    usual_card = commuter.locator("#body").inner_text()

    # a commuter who has signed up for alerts, with home and work on either side of the same road
    sub = pages.open("/commute", label="subscriber")
    zoom_to(sub, lat, lon, 17)
    sub.locator("#name").fill("Ravi")
    sub.locator('[data-veh="two_wheeler"]').click()
    sub.locator("#q-home").click()                                   # focus the Home box, then tap the map
    click_map(sub, *offset(lat, lon, -105, 0))
    sub.locator("#q-work").click()
    click_map(sub, *offset(lat, lon, 105, 0))
    expect(sub.locator("#q-home")).to_have_attribute("placeholder", "A spot you tapped")
    sub.locator("#consent").check()
    sub.locator('[data-act="signup"]').click()
    expect(sub.locator(".hero.ok")).to_contain_text("Alerts are on")
    sub.locator('[data-leave="5"]').click()
    expect(sub.locator("#toast")).to_contain_text("before you leave in 5 minutes")

    # a second subscriber who simply drives (no planned departure): this one will get the in-trip instruction.
    # (Each person is warned once per incident, so the pre-trip subscriber above is not warned again on the road.)
    driver = pages.open("/commute", label="driver")
    zoom_to(driver, lat, lon, 17)
    driver.locator("#name").fill("Asha")
    driver.locator('[data-veh="two_wheeler"]').click()
    driver.locator("#q-home").click()
    click_map(driver, *offset(lat, lon, -105, 0))
    driver.locator("#q-work").click()
    click_map(driver, *offset(lat, lon, 105, 0))
    driver.locator("#consent").check()
    driver.locator('[data-act="signup"]').click()
    expect(driver.locator(".hero.ok")).to_contain_text("Alerts are on")

    # -- 2. A citizen reports waterlogging (in a second browser)
    citizen = pages.open("/", label="citizen")
    expect(citizen.locator(".hero.ok")).to_be_visible()
    zoom_to(citizen, lat, lon, 18)
    citizen.locator("#btn-report").click()
    citizen.locator('[data-rtype="flood"]').click()
    click_map(citizen, lat, lon)
    expect(citizen.locator("#send-report")).to_be_enabled()
    citizen.locator("#report-note").fill("water above the knee near the school")
    citizen.locator("#send-report").click()
    expect(citizen.locator("#toast")).to_contain_text("Traffic police will check it")
    expect(citizen.locator('[data-report-status="pending"]')).to_contain_text("Waiting for a check")
    # a report on its own is never published: the other commuter still sees an all-clear route
    commuter.wait_for_timeout(3500)
    assert live.incidents("public") == []
    assert commuter.locator("#body").inner_text() == usual_card

    # -- 3. Simulated sensor observations arrive, and 4. the system assesses and verifies
    result = live.sensor([18, 25, 31, 35])
    assert result["incident"]
    inc = next(i for i in live.incidents() if i["id"] == result["incident"])
    assert inc["status"] == "Verified" and inc["severity"] == "Severe" and inc["simulated"] is True

    # -- 7a. the commuter is told and the trip card changes by itself (no tap)
    expect(commuter.locator("#toast")).to_contain_text(re.compile(r"trip was updated|New road problem"), timeout=15000)
    expect(commuter.locator(".hero")).to_contain_text(re.compile(r"usual way|problem on your way"), timeout=15000)
    assert commuter.locator("#body").inner_text() != usual_card
    assert commuter.title().startswith("(1)")
    # the citizen's own report follows the incident: confirmed
    expect(citizen.locator('[data-report-status="published"]')).to_contain_text("Confirmed", timeout=20000)

    # -- 5. The traffic authority reviews it: evidence, confidence, location
    control = pages.open("/control", label="control room")
    control_login(control)
    card = control.locator(".prob", has_text="Water on the road")
    expect(card).to_be_visible(timeout=15000)
    card.click()
    expect(control.locator("[data-evidence]")).to_contain_text("Water sensor reads 35 cm")
    expect(control.locator("[data-evidence]")).to_contain_text("driver report")
    expect(control.locator("[data-sure]")).to_contain_text(re.compile(r"\d+%"))
    expect(control.locator("[data-sure]")).to_contain_text("demo data")                 # simulated evidence is labelled

    # -- 6. The officer approves a diversion (a real backend call, not a UI-only change)
    control.get_by_role("button", name=re.compile("Approve diversion")).click()
    expect(control.locator("#toast")).to_contain_text("Diversion approved")
    expect(control.locator(".hero", has_text="Diversion approved")).to_be_visible()
    assert next(i for i in live.incidents() if i["id"] == inc["id"])["status"] == "Responding"

    # -- 7b. the commuter has a way round, in plain words
    expect(commuter.locator(".hero")).to_contain_text(re.compile(r"green route|no faster way"))

    # subscriber: a pre-trip alert, then an in-trip instruction while driving (simulated drive)
    expect(sub.locator(".card.alert.pre_trip")).to_be_visible(timeout=25000)
    expect(sub.locator(".card.alert.pre_trip")).to_contain_text("usual route")
    expect(sub.locator(".card.alert.pre_trip")).to_contain_text("demo")                  # simulated data is labelled
    assert driver.locator(".card.alert.in_trip, .card.alert.uturn, .card.alert.no_alternative").count() == 0
    driver.locator('[data-act="sim"]').click()
    expect(driver.locator(".card.alert.in_trip, .card.alert.uturn, .card.alert.no_alternative").first).to_be_visible(timeout=60000)

    # -- 8. The simulated roadside board shows the warning
    boards = live.get("/api/boards").json()["boards"]
    warning = [b for b in boards if b["lines"]]
    assert warning, f"no simulated board is within reach of the incident: {boards}"
    board = pages.open(f"/board?id={warning[0]['id']}", label="board")
    expect(board.locator(".line .big")).to_contain_text("AVOID")
    expect(board.locator("header")).to_contain_text("SIMULATED DISPLAY")

    # the control room sees that people were warned, anonymously
    expect(control.locator("[data-alerts]")).to_contain_text(re.compile(r"Warned (before the trip|on the road)|Told to turn back|no way round"), timeout=20000)

    # -- 9. Recovery observations arrive; the control room sees that the road is recovering
    live.sensor([28, 20, 12, 6, 3])
    expect(control.locator("[data-recovery]")).to_be_visible(timeout=30000)

    # -- 10. The officer resolves it (two taps, because it cannot be undone)
    clear = control.locator('[data-act="clear"]').last
    clear.click()
    expect(clear).to_contain_text("Tap again to confirm")
    clear.click()
    expect(control.locator("#toast")).to_contain_text("Road marked clear")
    assert [i for i in live.incidents() if i["status"] != "Cleared"] == []

    # -- 11. everyone gets the all-clear, 12. and the original route is back
    expect(sub.locator(".card.alert.clear")).to_be_visible(timeout=20000)
    expect(driver.locator(".card.alert.clear")).to_be_visible(timeout=20000)
    expect(commuter.locator(".hero.ok")).to_contain_text("Your way is clear", timeout=20000)
    expect(citizen.locator('[data-report-status="resolved"]')).to_contain_text("Resolved", timeout=25000)
    expect(board.locator(".clear")).to_contain_text("ROAD CLEAR", timeout=15000)
    assert live.incidents("public") == []

    assert not pages.problems(), pages.problems()
