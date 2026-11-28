"""Staff apps in a real browser: sign-in behaviour, role separation, forged sessions, script injection through user
text, response headers, and the phone layout. Expected 401 responses are the only network errors allowed here."""
import re

from playwright.sync_api import expect

from .conftest import CENTER, OFFICER, POLICE, control_login, offset, police_login

EXPECTED_401 = ("401", "Unauthorized")
PAYLOAD = '<img src=x onerror="window.__xss=1">'


def test_wrong_and_non_ascii_passwords_get_a_plain_message_on_both_apps(live, pages):
    for path, msg in (("/control", "That name or password is not right."), ("/police", "That unit or password is not right.")):
        page = pages.open(path, label=path)
        for bad in ("definitely-wrong", "pässwörd✓", ""):
            control_login(page, bad, user="officer") if path == "/control" else police_login(page, password=bad)
            expect(page.locator("body")).to_contain_text(msg)
            assert page.locator("#btn-login").is_visible(), "still on the sign-in form"
    assert not pages.problems(ignore=EXPECTED_401), pages.problems(ignore=EXPECTED_401)
    assert not live.server_errors(), live.server_errors()          # a non-ASCII password used to be a server error


def test_each_role_is_sent_to_its_own_app(live, pages):
    ctrl = pages.open("/control", label="control")
    control_login(ctrl, POLICE[1], user=POLICE[0])
    expect(ctrl.locator("body")).to_contain_text("That is a police login")
    pol = pages.open("/police", label="police")
    police_login(pol, OFFICER[0], OFFICER[1])
    expect(pol.locator("body")).to_contain_text("That is a control-room login")
    assert not pages.problems(), pages.problems()                   # a role mix-up is a 200 with a message, not an error
    # and the API agrees: a police token cannot read the control room's view or act on incidents
    pt = live.token(POLICE)
    assert live.get("/api/incidents?scope=all", headers=pt).status_code == 401
    assert live.post("/api/incidents/inc-none/clear", {}, headers=pt).status_code == 401


def test_a_forged_or_expired_session_falls_back_to_sign_in(live, pages):
    page = pages.open("/control", label="control")
    control_login(page)
    expect(page.get_by_role("heading", name="Queue")).to_be_visible()
    page.evaluate("sessionStorage.setItem('rp_ctrl_token', 'forged.token')")
    page.reload()
    page.wait_for_timeout(3500)                                      # the next poll is refused and the app drops the session
    expect(page.locator("#btn-login")).to_be_visible()
    assert not pages.problems(ignore=EXPECTED_401), pages.problems(ignore=EXPECTED_401)


def test_text_typed_by_citizens_police_and_subscribers_is_never_run_as_script(live, pages):
    live.clear_everything()
    lat, lon = offset(*CENTER, -150, -75)
    iid = live.post("/api/reports", {"type": "accident", "lat": lat, "lon": lon, "reporter_id": "xss", "note": PAYLOAD}).json()["incident_id"]
    off, pol = live.officer, live.token(POLICE)
    assert live.post(f"/api/incidents/{iid}/assign", {"unit": POLICE[0]}, headers=off).status_code == 201
    for st in ("accepted", "on_way", "arrived"):
        assert live.post(f"/api/police/calls/{iid}/status", {"status": st}, headers=pol).status_code == 200
    assert live.post(f"/api/police/calls/{iid}/report", {"finding": "blocked", "need": [], "note": PAYLOAD}, headers=pol).status_code in (200, 201)
    sub = live.post("/api/subscribers", {"name": PAYLOAD, "vehicle": "car", "consent_location": True}).json()

    control = pages.open("/control", label="control")
    control_login(control)
    control.locator(".prob.pending").first.click()
    expect(control.locator(".hero", has_text="Police confirmed")).to_contain_text("<img src=x")            # shown as text
    assert control.evaluate("window.__xss") is None
    assert control.locator("img[src='x']").count() == 0

    classic = pages.open("/classic", label="classic")
    classic.locator('.tab[data-view="authority"]').click()
    classic.locator("#pass").fill(OFFICER[1])
    classic.locator("#btn-login").click()
    classic.locator('#officer-pending [data-act="evidence"]').first.click()
    expect(classic.locator(".evidence")).to_contain_text("<img src=x")
    assert classic.evaluate("window.__xss") is None and classic.locator("img[src='x']").count() == 0

    commute = pages.open("/commute", label="commute")
    commute.evaluate("([id, tok, name]) => localStorage.setItem('rp_sub', JSON.stringify({ id, token: tok, name }))", [sub["id"], sub["token"], PAYLOAD])
    commute.reload()
    expect(commute.locator(".hero.ok")).to_contain_text(PAYLOAD)
    assert commute.evaluate("window.__xss") is None and commute.locator("img[src='x']").count() == 0
    live.post(f"/api/subscribers/inc-x", {}, headers={})                                                   # (harmless) unknown id: 404/405, never a crash
    live.http.delete(f"/api/subscribers/{sub['id']}", headers={"X-Subscriber-Token": sub["token"]})
    live.clear_everything()
    assert not pages.problems(), pages.problems()


def test_security_headers_reach_the_browser(live, pages):
    page = pages.open("/", wait=False)
    resp = page.goto(live.url + "/control")
    h = {k.lower(): v for k, v in resp.headers.items()}
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in h["content-security-policy"] and h["cache-control"] == "no-cache"
    login = live.post("/api/auth/login", {"username": OFFICER[0], "password": OFFICER[1]})
    assert login.headers["cache-control"] == "no-store"
    # the app cannot be put in someone else's frame
    page.set_content(f'<iframe id="f" src="{live.url}/control" width="300" height="200"></iframe>')
    page.wait_for_timeout(1500)
    frame = [f for f in page.frames if f.url.startswith(live.url)]
    assert not frame or "RoadPulse" not in (frame[0].title() if frame else ""), "the page must refuse to load inside an iframe"


def test_staff_apps_fit_a_phone(live, pages):
    live.clear_everything()
    for path, login in (("/control", control_login), ("/police", police_login)):
        page = pages.open(path, width=390, height=844, mobile=True, label=path)
        expect(page.locator("#btn-login")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth") <= 391, f"{path} scrolls sideways on a phone"
        box = page.locator("#btn-login").bounding_box()
        assert box["height"] >= 44 and box["width"] >= 120, "the sign-in button is a comfortable touch target"
        login(page)
        expect(page.locator("#sheet")).to_be_visible()
        page.wait_for_timeout(1500)
        assert page.evaluate("document.documentElement.scrollWidth") <= 391
        assert re.search(r"Queue|No calls right now|Your calls", page.locator("#body").inner_text())
    assert not pages.problems(), pages.problems()
