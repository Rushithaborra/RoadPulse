"""Commuter app in a real browser: map, trip planning, route failures, vehicle categories, live offline behaviour,
and the phone layout. Every test also fails on any console error, page error or failed request of our own."""
import re

from playwright.sync_api import expect

from .conftest import CENTER, click_map, offset, zoom_to


def test_map_loads_and_the_page_is_clean(live, pages):
    live.clear_everything()
    page = pages.open("/")
    expect(page.locator(".leaflet-container")).to_be_visible()
    expect(page.locator("#pill-t")).to_have_text("Where to?")
    expect(page.locator(".hero.ok")).to_contain_text("All clear")          # data loaded: the skeleton is replaced
    expect(page.locator(".legend")).to_contain_text("Blocked")
    expect(page.locator("#btn-report")).to_be_visible()
    assert page.title().startswith("RoadPulse")
    assert page.evaluate("map.getCenter().lat") - CENTER[0] < 0.01, "the map opens on the pilot area"
    assert not pages.problems(), pages.problems()


def test_plan_a_trip_by_searching_two_places_and_see_a_route_on_the_real_roads(live, pages):
    live.clear_everything()
    page = pages.open("/")
    page.locator("#pill").click()
    expect(page.locator("#q-to")).to_be_visible()
    page.locator("#q-from").click()
    page.locator("#q-from").fill("4 th")
    first = page.locator(".result").first
    expect(first).to_contain_text("4 th Lane")
    first.click()
    page.locator("#q-to").fill("RTC")
    expect(page.locator(".result").first).to_contain_text("RTC")
    page.locator(".result").first.click()
    expect(page.locator(".hero.ok")).to_contain_text("Your way is clear")
    expect(page.locator(".hero")).to_contain_text(re.compile(r"About \d+ min, [\d.]+ km"))
    expect(page.locator(".ab", has_text="A")).to_be_visible()
    expect(page.locator(".ab", has_text="B")).to_be_visible()
    assert page.locator(".leaflet-overlay-pane path").count() >= 2, "the route is drawn on the map"
    assert not pages.problems(), pages.problems()


def test_planning_by_tapping_two_points_and_changing_vehicle(live, pages):
    live.clear_everything()
    page = pages.open("/")
    expect(page.locator(".hero.ok")).to_be_visible()
    a, b = offset(*CENTER, -150, 40), offset(*CENTER, 220, 90)
    zoom_to(page, *CENTER, 16)
    click_map(page, *a)
    click_map(page, *b)
    expect(page.locator(".hero")).to_contain_text(re.compile(r"Your way is clear|A problem on your way"))
    expect(page.locator("#pill-t")).to_have_text("Your trip")        # not stuck on "Checking your trip" once the result is shown
    for veh in ("two_wheeler", "auto", "bus"):
        page.locator(f'[data-veh="{veh}"]').click()
        expect(page.locator(f'[data-veh="{veh}"]')).to_have_class(re.compile(r"\bon\b"))
        expect(page.locator(".hero")).to_contain_text(re.compile(r"Your way is clear|A problem on your way|No way to get there"))
    assert not pages.problems(), pages.problems()


def test_an_impossible_trip_gives_a_clear_explanation_not_a_blank_screen(live, pages):
    live.clear_everything()
    page = pages.open("/")
    expect(page.locator(".hero.ok")).to_be_visible()
    far_a, far_b = offset(*CENTER, 9000, 9000), offset(*CENTER, 9500, 9200)       # far outside the mapped pilot area
    page.evaluate("([a, b]) => { state.A = a; state.B = b; drawTripPins(); setView('trip'); runTrip(); }", [list(far_a), list(far_b)])
    expect(page.locator(".hero.bad")).to_contain_text("Could not check this trip")
    expect(page.locator(".hero.bad")).to_contain_text("nearest mapped road")
    assert not pages.problems(ignore=("422", "Unprocessable")), pages.problems()


def test_the_trip_card_updates_by_itself_when_an_incident_appears_and_when_it_is_resolved(live, pages):
    live.clear_everything()
    hs = live.hotspot()
    page = pages.open("/")
    expect(page.locator(".hero.ok")).to_be_visible()
    zoom_to(page, hs["lat"], hs["lon"], 18)
    click_map(page, *offset(hs["lat"], hs["lon"], -110, 0))
    click_map(page, *offset(hs["lat"], hs["lon"], 110, 0))
    page.locator('[data-veh="two_wheeler"]').click()
    expect(page.locator(".hero")).to_contain_text("Your way is clear")

    live.sensor([18, 25, 31, 35])                                              # simulated sensor evidence arrives
    expect(page.locator(".hero.bad, .hero.warn")).to_contain_text(re.compile(r"usual way|problem on your way"), timeout=15000)
    expect(page.locator("#toast")).to_contain_text("trip was updated", timeout=15000)

    for i in live.incidents():                                                  # an officer resolves it
        live.post(f"/api/incidents/{i['id']}/clear", {}, headers=live.officer)
    expect(page.locator(".hero.ok")).to_contain_text("Your way is clear", timeout=15000)
    assert not pages.problems(), pages.problems()


def test_going_offline_is_shown_and_recovery_is_automatic(live, pages):
    live.clear_everything()
    page = pages.open("/")
    expect(page.locator(".hero.ok")).to_be_visible()
    page.context.set_offline(True)
    expect(page.locator("#offline")).to_be_visible(timeout=15000)
    page.context.set_offline(False)
    expect(page.locator("#offline")).to_be_hidden(timeout=15000)


def test_phone_layout_is_usable(live, pages):
    live.clear_everything()
    page = pages.open("/", width=390, height=844, mobile=True)
    expect(page.locator(".hero.ok")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= 391, "no sideways scrolling on a phone"
    expect(page.locator("#sheet")).to_be_visible()
    expect(page.locator("#btn-report")).to_be_visible()
    box = page.locator("#btn-report").bounding_box()
    assert box["width"] >= 44 and box["height"] >= 44, "touch targets are big enough"
    before = page.locator("#sheet").bounding_box()["height"]
    page.locator("#grab").click()                                                # the bottom sheet changes height
    page.wait_for_timeout(500)
    assert page.locator("#sheet").bounding_box()["height"] != before
    page.locator("#pill").click()
    expect(page.locator("#q-to")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= 391
    assert not pages.problems(), pages.problems()


def test_see_a_way_round_really_shows_a_way_round_the_problem(live, pages):
    """The button on a problem card must build a trip that crosses the problem, for every vehicle that is blocked."""
    live.clear_everything()
    live.sensor([18, 25, 31, 35])                                              # severe flood: cars and two-wheelers are blocked
    page = pages.open("/")
    page.locator(".prob").first.click()
    expect(page.locator("#body")).to_contain_text("Water about 35 cm deep")
    page.locator('[data-act="detour"]').click()
    expect(page.locator(".hero")).to_contain_text(re.compile(r"usual way|problem on your way"), timeout=15000)
    expect(page.locator(".hero")).to_contain_text("green route")
    assert page.locator(".leaflet-overlay-pane path").count() >= 4, "the blocked road and the detour are both drawn"
    page.locator('[data-veh="bus"]').click()                                   # a bus may still pass 35 cm of water
    expect(page.locator(".hero")).to_contain_text(re.compile(r"problem on your way|Your way is clear|usual way"))
    live.clear_everything()
    assert not pages.problems(), pages.problems()
