"""Browser end-to-end tests: a real server (real OpenStreetMap graph, temporary database) and a real browser.

These tests need Playwright and an installed Chrome or Edge:  pip install -r requirements-e2e.txt
They are skipped, with a clear reason, only when Playwright or a browser is missing. Run only these with:
  python -m pytest -q tests/e2e
External map tiles are blocked by default so the tests are fast and offline-safe (set E2E_TILES=1 to allow them).
"""
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

sync_api = pytest.importorskip("playwright.sync_api", reason="Playwright is not installed (pip install -r requirements-e2e.txt)")

ROOT = Path(__file__).resolve().parents[2]
OFFICER, POLICE, DEVICE = ("officer", "e2e-pass"), ("unit1", "e2e-pass"), "e2e-key"
CENTER = (17.7286712, 83.3086321)          # the pilot area centre (Dwaraka Nagar)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Live:
    """A running RoadPulse server plus small helpers to play the roles the browser does not (devices, officers)."""

    def __init__(self, url: str, log: Path):
        self.url, self.log = url, log
        self.http = httpx.Client(base_url=url, timeout=30)
        self.proc = None
        self.cmd = self.env = None

    def start(self, cmd, env):
        self.cmd, self.env = cmd, env
        self._spawn()

    def _spawn(self):
        with open(self.log, "a", encoding="utf-8") as fh:
            self.proc = subprocess.Popen(self.cmd, cwd=ROOT, env=self.env, stdout=fh, stderr=subprocess.STDOUT)
        for _ in range(80):
            try:
                if self.http.get("/api/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if self.proc.poll() is not None:
                pytest.fail("server exited at start-up:\n" + self.log.read_text(encoding="utf-8", errors="replace")[-1500:])
            time.sleep(0.5)
        pytest.fail("server did not start in 40 s")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def restart(self, pause: float = 0.0):
        """Stop and start the server again on the same port and database (for reconnection tests)."""
        self.stop()
        time.sleep(pause)
        self._spawn()

    def get(self, path, **kw):
        return self.http.get(path, **kw)

    def post(self, path, json=None, headers=None):
        return self.http.post(path, json=json if json is not None else {}, headers=headers)

    def token(self, user_pass):
        r = self.post("/api/auth/login", {"username": user_pass[0], "password": user_pass[1]})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}

    @property
    def officer(self):
        return self.token(OFFICER)

    def hotspot(self, sensor="sensor-01"):
        return next(h for h in self.get("/api/hotspots").json()["hotspots"] if h["sensor_id"] == sensor)

    def sensor(self, depths, sensor="sensor-01"):
        """Simulated water-depth readings through the same endpoint a real device would use."""
        last = None
        for d in depths:
            r = self.post("/api/sensors/readings", {"sensor_id": sensor, "depth_cm": d, "simulated": True}, headers={"X-Device-Key": DEVICE})
            assert r.status_code == 200, r.text
            last = r.json()
        return last

    def incidents(self, scope="all"):
        h = self.officer if scope == "all" else {}
        return self.get(f"/api/incidents?scope={scope}", headers=h).json()["incidents"]

    def clear_everything(self):
        """Leave a shared server clean between tests."""
        h = self.officer
        for i in self.incidents():
            self.post(f"/api/incidents/{i['id']}/clear", {}, headers=h)

    def server_errors(self) -> list[str]:
        text = self.log.read_text(encoding="utf-8", errors="replace") if self.log.exists() else ""
        return [ln for ln in text.splitlines() if "Traceback" in ln or " ERROR " in ln]


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e")
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLEAR_WINDOW", "TELEGRAM", "HOTSPOTS", "BOARDS", "REPORT_", "ROUTE_", "DEVICE_", "EVAL_"))}
    env.update(DATABASE_PATH=str(tmp / "e2e.db"), GRAPHML_PATH=str(ROOT / "data" / "area.graphml"), ROADPULSE_SECRET="e2e-secret",
               OFFICER_USERS=f"{OFFICER[0]}:{OFFICER[1]}", POLICE_USERS=f"{POLICE[0]}:{POLICE[1]}", DEVICE_KEYS=DEVICE, CLEAR_WINDOW_S="600",
               REPORT_RATE_PER_MIN="6000", ROUTE_RATE_PER_MIN="60000", DEVICE_RATE_PER_MIN="60000", SUBSCRIBE_RATE_PER_MIN="6000",
               EVAL_RATE_PER_MIN="600", PYTHONUNBUFFERED="1", TRUST_PROXY="0",
               REQUIRE_FIELD_CONFIRMATION="1")          # the production default: the unit-test settings turn it off, and must not leak in
    log = tmp / "server.log"
    lv = Live(f"http://127.0.0.1:{port}", log)
    cmd = [sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", str(port),
           "--no-access-log", "--timeout-graceful-shutdown", "2"]
    try:
        lv.start(cmd, env)
        health = lv.get("/api/health").json()
        assert health["map_mode"] == "osm" and health["map_warning"] is None, "the e2e run must use the real OSM graph"
        yield lv
        assert not lv.server_errors(), "server log has errors:\n" + "\n".join(lv.server_errors()[:8])
    finally:
        lv.stop()


@pytest.fixture(scope="session")
def browser():
    with sync_api.sync_playwright() as p:
        b = None
        for channel in ("chrome", "msedge"):
            try:
                b = p.chromium.launch(channel=channel, headless=True)
                break
            except Exception:                                   # that browser is not installed
                continue
        if b is None:
            try:
                b = p.chromium.launch(headless=True)             # Playwright's own download, if the user installed it
            except Exception:
                pytest.skip("no Chrome, Edge or Playwright Chromium found (install Chrome, or run: python -m playwright install chromium)")
        yield b
        b.close()


class Pages:
    """Opens pages in fresh browser contexts and records everything a user would never want to see."""

    def __init__(self, browser, live):
        self.browser, self.live, self.items = browser, live, []

    def open(self, path="/", *, width=1280, height=800, mobile=False, geo=None, wait=True, label=None):
        kw = dict(viewport={"width": width, "height": height}, locale="en-IN", service_workers="block")
        if mobile:
            kw.update(is_mobile=True, has_touch=True, device_scale_factor=2)
        if geo:
            kw.update(geolocation={"latitude": geo[0], "longitude": geo[1]}, permissions=["geolocation"])
        ctx = self.browser.new_context(**kw)
        if not os.getenv("E2E_TILES"):
            base = self.live.url
            ctx.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base) or route.request.url.startswith("data:") else route.abort())
        page = ctx.new_page()
        page.problems = []
        page.on("pageerror", lambda e: page.problems.append(f"page error: {e}"))
        def on_console(m):
            if m.type != "error":
                return
            url = (m.location or {}).get("url") or ""
            if url and not url.startswith(self.live.url):
                return                                            # an external map tile we blocked on purpose is not our bug
            page.problems.append(f"console error: {m.text}")
        page.on("console", on_console)
        page.on("response", lambda r: r.url.startswith(self.live.url) and r.status >= 400 and page.problems.append(f"HTTP {r.status} {r.request.method} {r.url.replace(self.live.url, '')}"))
        # ERR_ABORTED is what the browser reports when the page itself ends a request (reload, or switching stream): not a failure
        page.on("requestfailed", lambda r: r.url.startswith(self.live.url) and "ERR_ABORTED" not in str(r.failure) and page.problems.append(f"request failed {r.url.replace(self.live.url, '')}: {r.failure}"))
        page.set_default_timeout(15000)
        self.items.append((label or path, ctx, page))
        if wait:
            page.goto(self.live.url + path)
        return page

    def problems(self, ignore=()):
        out = []
        for label, _, page in self.items:
            out += [f"[{label}] {p}" for p in page.problems if not any(i in p for i in ignore)]
        return out

    def close(self):
        for _, ctx, _ in self.items:
            ctx.close()


@pytest.fixture
def pages(browser, live):
    pg = Pages(browser, live)
    yield pg
    pg.close()


def px(page, lat, lon):
    """Pixel position (page coordinates) of a map point, so tests click the map like a person does."""
    box = page.locator("#map").bounding_box()
    x, y = page.evaluate("([la, lo]) => { const p = map.latLngToContainerPoint([la, lo]); return [p.x, p.y]; }", [lat, lon])
    return box["x"] + x, box["y"] + y


def click_map(page, lat, lon):
    x, y = px(page, lat, lon)
    page.mouse.click(x, y)


def zoom_to(page, lat, lon, zoom=18):
    page.evaluate("([la, lo, z]) => map.setView([la, lo], z, { animate: false })", [lat, lon, zoom])
    page.wait_for_timeout(250)


def offset(lat, lon, east_m, north_m):
    import math
    return lat + north_m / 111320.0, lon + east_m / (111320.0 * math.cos(math.radians(lat)))


def control_login(page, password=OFFICER[1], user=None):
    from playwright.sync_api import expect
    expect(page.locator("#btn-login")).to_be_visible()
    if user:
        page.locator("#user").fill(user)
    page.locator("#pass").fill(password)
    page.locator("#btn-login").click()


def police_login(page, user=POLICE[0], password=POLICE[1]):
    from playwright.sync_api import expect
    expect(page.locator("#btn-login")).to_be_visible()
    page.locator("#user").fill(user)
    page.locator("#pass").fill(password)
    page.locator("#btn-login").click()
