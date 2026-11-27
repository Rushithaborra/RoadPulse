"""The UI has no build step, so nothing checks it before a browser does. These tests catch the cheap failures that
break the whole page: app.js looking up an element that index.html does not have (a null error stops the script and
leaves the map blank), and a stale UI being served from the browser cache after a redeploy."""
import os
import re

STATIC = os.path.join(os.path.dirname(__file__), "..", "backend", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def test_every_element_app_js_looks_up_exists_in_the_page():
    html_ids = set(re.findall(r'\bid="([^"]+)"', read("index.html")))
    js = read("app.js")
    used = set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', js)) | set(re.findall(r'getElementById\("([A-Za-z0-9_-]+)"\)', js))
    created_in_js = {"btn-retry"}  # injected by setMapOverlay() when the network fails to load
    missing = used - html_ids - created_in_js
    assert not missing, f"app.js uses ids that index.html does not define: {sorted(missing)}"


def test_every_stylesheet_and_script_the_page_loads_exists():
    html = read("index.html")
    for ref in re.findall(r'(?:href|src)="/static/([^"]+)"', html):
        assert os.path.exists(os.path.join(STATIC, ref)), ref


def test_page_and_static_files_are_revalidated_not_cached_stale(client):
    for path in ("/", "/static/app.js", "/static/style.css"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", path


def test_page_shows_the_map_legend_and_simulation_honesty_markers():
    js, html = read("app.js"), read("index.html")
    assert "Map legend" in js and "Awaiting verification" in js and "Blocked for the vehicle you picked" in js
    assert "SIMULATED" in js and "assumed demo values" in html
