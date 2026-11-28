"""The UI has no build step, so nothing checks it before a browser does. These tests catch the cheap failures that
break the whole page: app.js looking up an element that index.html does not have (a null error stops the script and
leaves the map blank), and a stale UI being served from the browser cache after a redeploy."""
import os
import re

STATIC = os.path.join(os.path.dirname(__file__), "..", "backend", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


# ids a script creates itself (inside its own templates) rather than finding in the page
PAGES = [("classic.html", "app.js", {"btn-retry"}),
         ("index.html", "commuter.js", {"send-report", "q-from", "q-to", "results", "report-note"}),
         ("commute.html", "commute.js", {"name", "depart", "consent", "results", "q-home", "q-work"}),
         ("control.html", "control.js", {"user", "pass", "btn-login", "live"}),
         ("police.html", "police.js", {"user", "pass", "btn-login", "note"})]


def test_every_element_the_scripts_look_up_exists_in_their_page():
    for page, script, created_in_js in PAGES:
        check_ids(page, script, created_in_js)


def check_ids(page, script, created_in_js):
    html_ids = set(re.findall(r'\bid="([^"]+)"', read(page)))
    js = read(script)
    used = set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', js)) | set(re.findall(r'getElementById\("([A-Za-z0-9_-]+)"\)', js))
    missing = used - html_ids - created_in_js
    assert not missing, f"{script} uses ids that {page} does not define: {sorted(missing)}"


def test_every_stylesheet_and_script_the_pages_load_exists():
    for page in ("index.html", "control.html", "police.html", "classic.html", "commute.html", "board.html"):
        for ref in re.findall(r'(?:href|src)="/static/([^"]+)"', read(page)):
            assert os.path.exists(os.path.join(STATIC, ref)), f"{page}: {ref}"


def test_page_and_static_files_are_revalidated_not_cached_stale(client):
    for path in ("/", "/control", "/police", "/classic", "/commute", "/board", "/static/app.js", "/static/style.css", "/static/rp.js", "/static/rp.css"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", path


def test_page_shows_the_map_legend_and_simulation_honesty_markers():
    js, html = read("app.js"), read("classic.html")
    assert "Map legend" in js and "Awaiting verification" in js and "Blocked for the vehicle you picked" in js
    assert "SIMULATED" in js and "assumed demo values" in html


def test_apps_explain_every_severity_in_plain_words_and_offer_satellite():
    shared = read("rp.js")
    for sev in ("Low", "Medium", "Severe", "Closed"):
        assert re.search(rf"{sev}: \{{ label: \"[^\"]+\".*advice: \"[^\"]+\"", shared), f"no plain advice for {sev}"
    assert "You cannot get through. Take another road." in shared          # blocked: a clear instruction
    assert "only if you are in a hurry" in shared                          # slow: optional
    assert "World_Imagery" in shared and "Esri" in shared                  # satellite map with attribution
    for page in ("index.html", "control.html", "police.html", "commute.html"):
        assert 'id="btn-style"' in read(page), f"{page} has no map / satellite switch"


def test_three_separate_apps_each_speak_to_their_own_user():
    assert "Where to?" in read("index.html") and "See a way round" in read("commuter.js")                   # drivers
    c = read("control.js")
    assert "Why we think so" in c and "Send the nearest police unit to check" in c and "Warn drivers now" in c  # control room
    p = read("police.js")
    assert "I've arrived" in p and "What do you see?" in p and "Send to control room" in p                  # police on the scene


def test_no_jargon_is_shown_to_anyone():
    for name in ("rp.js", "commuter.js", "control.js", "police.js"):
        for text in re.findall(r"`([^`]*)`", read(name)):
            assert "confidence" not in text.lower() and "\u00d7" not in text and "probe\u00d7" not in text, f"jargon in {name}: {text[:60]}"


def test_phone_first_features_are_in_place():
    for page in ("index.html", "control.html", "police.html"):
        h = read(page)
        assert "viewport-fit=cover" in h and 'name="theme-color"' in h and "apple-mobile-web-app-capable" in h, page
    c, ctl, pol = read("commuter.js"), read("control.js"), read("police.js")
    assert "/api/places" in c and "Share this warning" in c and "data-save" in c and "visibilitychange" in c      # search, share, saved places, saves battery
    assert "What happened" in ctl and "/api/units" in ctl and "beep" in ctl                                       # live units, timeline, sound
    assert "waiting to send" in pol and "wakeLock" in pol and "watchPosition" in pol and "sheet-foot" in pol      # no-signal queue, screen on, live distance, thumb actions
    css = read("rp.css")
    assert "min-height: 44px" in css and "safe-area-inset-bottom" in css and "font-size: 16px" in css            # thumb targets, notches, no iOS zoom
