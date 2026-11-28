"""The UI has no build step, so nothing checks it before a browser does. These tests catch the cheap failures that
break a whole page: a script looking up an element its page does not have (a null error stops the script and leaves
the map blank), a missing asset, and a stale UI being served from the browser cache after a redeploy.
Three apps share rp/core.js: the commuter map (/), the control room (/control) and the field police app (/field)."""
import os
import re

STATIC = os.path.join(os.path.dirname(__file__), "..", "backend", "static")
PAGES = {"index.html": "app.js", "control.html": "rp/control.js", "field.html": "rp/field.js"}


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def ids_used(js):
    return set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', js)) | set(re.findall(r'getElementById\("([A-Za-z0-9_-]+)"\)', js))


def test_every_element_each_app_looks_up_exists_in_its_page():
    core_optional = {"toast", "banner", "conn", "mapmode"}   # core.js creates or skips these when a page lacks them
    created_in_js = {"act-note", "act-sev", "tool-out", "f-note", "btn-confirm", "btn-notfound", "minimap"}
    for page, script in PAGES.items():
        html_ids = set(re.findall(r'\bid="([^"]+)"', read(page)))
        missing = ids_used(read(script)) - html_ids - created_in_js
        assert not missing, f"{script} uses ids that {page} does not define: {sorted(missing)}"
        assert not (ids_used(read("rp/core.js")) - html_ids - core_optional), page


def test_every_stylesheet_and_script_each_page_loads_exists():
    for page in PAGES:
        html = read(page)
        assert "/static/rp/core.js" in html and "/static/rp/theme.css" in html
        for ref in re.findall(r'(?:href|src)="/static/([^"]+)"', html):
            assert os.path.exists(os.path.join(STATIC, ref)), f"{page}: {ref}"
    css = read("rp/theme.css")
    for font in re.findall(r'url\("/static/([^"]+)"\)', css):
        assert os.path.exists(os.path.join(STATIC, font)), font


def test_pages_and_static_files_are_revalidated_not_cached_stale(client):
    for path in ("/", "/control", "/field", "/static/app.js", "/static/rp/core.js", "/static/rp/theme.css"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", path


def test_maps_carry_a_legend_and_simulation_honesty_markers():
    core, html = read("rp/core.js"), read("index.html")
    assert "Map legend" in core and "Awaiting verification" in core and "Blocked for the vehicle you picked" in core
    assert "Simulated" in core and "assumed demo values" in html
    assert "manual override set by an officer, not a forecast" in read("control.html")
    assert "Advice only: RoadPulse controls no signal" in read("rp/control.js")


def test_citizen_page_explains_the_two_checks():
    html = read("index.html")
    assert "traffic control room first, then to police on the ground" in html
    assert "only after both have checked it" in html
