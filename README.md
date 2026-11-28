# RoadPulse

Low-cost, real-time road disruption detection and vehicle-aware rerouting.
A road disruption is reported → the incident is assessed → the affected road is identified →
an alternative route is calculated → the authority dashboard updates, live.

Hackathon prototype (AITAM virtual hackathon, Problem #3). Demo area: Dwaraka Nagar, Visakhapatnam
(17.728670 N, 83.308634 E). Plan and contracts: [`docs/PLAN.md`](docs/PLAN.md).

Road data and map tiles © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), available under the
Open Database Licence (ODbL). The attribution is shown on the map and must stay visible.

## Run it in VS Code (5 minutes the first time)

Needs **Python 3.12 or newer** and **VS Code** with the Python extension (VS Code offers it when you open the folder).
There is one server: it serves the API and all three web apps (plain HTML/JS, no build step, no Node.js).

1. **Open the folder:** VS Code > *File > Open Folder…* > choose the `RoadPulse` folder (the one with `README.md`).
2. **Open a terminal:** *Terminal > New Terminal* (PowerShell on Windows).
3. **Create the virtual environment and install** (first time only):

   Windows (PowerShell):
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope Process Bypass, then run it again
   pip install -r requirements-dev.txt
   Copy-Item .env.example .env
   ```
   macOS / Linux:
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements-dev.txt
   cp .env.example .env
   ```
4. **Pick the interpreter:** press `Ctrl+Shift+P` > *Python: Select Interpreter* > choose the one in `.venv`.
5. **Start the server:** *Terminal > Run Task… > RoadPulse: start server* (or press `Ctrl+Shift+B`), or type:
   ```
   uvicorn app.main:app --app-dir backend --env-file .env --no-access-log --timeout-graceful-shutdown 3
   ```
   Wait for `Uvicorn running on http://127.0.0.1:8000`. To debug instead, press `F5` (*RoadPulse server (debug)*).
6. **Open the apps** in your browser:

   | App | Address | Sign in (example `.env`) |
   |---|---|---|
   | Commuter map | http://localhost:8000 | none |
   | Control room | http://localhost:8000/control | `officer` / `change-me` |
   | Field police (phone layout) | http://localhost:8000/field | `field1` / `change-me-field` |

7. **Run the demo** in a second terminal (`+` in the terminal panel, activate `.venv` again):
   `python simulator/demo.py story` (flood story, press Enter between steps), or
   `python simulator/demo.py checks` (two checks on a citizen report). Also available under *Run Task…*.
8. **Run the tests:** *Run Task… > RoadPulse: run tests*, or `python -m pytest -q tests`, or the Testing panel.

Stop the server with `Ctrl+C` in its terminal. Change the passwords in `.env` before sharing the server. Names in
`OFFICER_USERS` that start with `field` are field police; all others are control room (`supervisor…` can do both).
If `OFFICER_USERS` or `DEVICE_KEYS` is missing, the server generates random ones for that run and prints them.
To open the field app on a phone on the same Wi-Fi, start the server with `--host 0.0.0.0` and open
`http://<your-laptop-ip>:8000/field`.

With Docker instead: `cp .env.example .env && docker compose up --build`

## The three apps

- **Commuter map (`/`).** Real OpenStreetMap roads of the pilot area with Dwaraka Nagar and the RTC Complex marked.
  Choose a vehicle, tap a start and a destination: the route follows real roads and avoids confirmed disruptions;
  when a disruption is confirmed, changes or clears, the route redraws by itself. Affected roads are coloured by
  severity; a closed road is drawn with black and yellow barrier striping; a road blocked for your vehicle gets a
  white dotted line. A **Map legend** explains every colour and symbol. **Report a problem** sends a report and
  tracks it through four steps (reported, control room, field police, on the map).
- **Control room (`/control`).** Queues: *Needs your check*, *With field police*, *Live on the public map*, flood
  watch and activity. For a citizen report: **Send to field police**, **Urgent: publish now** or **Reject**. For a
  published incident: **Approve diversion**, **Resolve**, record units sent, and decision support (compare
  diversions, signal timing advice, bulletin draft; advice only, RoadPulse controls no signal). Also planned works,
  the demo rain setting and the before/after evaluation.
- **Field police (`/field`).** Built for a phone. The checks the control room sent, urgent first, each with the
  citizen's words, the control room's note, a map and a Directions link. **Confirm: it is here** (with how bad it
  is) publishes it; **Not found at this spot** closes it, or sends an urgent one back to the control room.

All three share one live connection type (Server-Sent Events with a heartbeat): the header shows **Live**, and a
banner says when updates stop; pages reconnect by themselves. If `data/area.graphml` cannot be loaded the server
falls back to a synthetic grid and every app says so (*Synthetic grid, not real roads* and a red map warning).
Per-vehicle water-depth limits are **assumed demo values** in `backend/app/config.py`, not safety guidance.
Map tiles are greyscale so that only RoadPulse information carries colour. Fonts (Overpass, OFL licence) and Leaflet
are bundled, so the apps work without a CDN; only the background map tiles need internet.

## Alert delivery (commuters, boards, Telegram)

See [`docs/ALERTS.md`](docs/ALERTS.md): how people are warned **by route (not radius)**, **before the last turn-off**,
before they leave (pre-trip) and again when the road clears, plus the honest limits. Pages: `/commute` (phone page
with spoken alerts and a simulated drive) and `/board` (simulated roadside display). Story:
`python simulator/alerts_demo.py --device-key KEY --password PASS`.

## Demo scenario

The full 10-step hackathon walkthrough, with what to say and what to click, is in [`docs/DEMO.md`](docs/DEMO.md).
In a second terminal (the simulator reads `DEVICE_KEYS` and `OFFICER_USERS` from `.env`):
```bash
python simulator/demo.py story               # the 10-step demo, press Enter between steps
python simulator/demo.py story --auto --auto-officer   # unattended: the script also plays the officer
python simulator/demo.py all                 # the original scenario with an accident and planned works
python simulator/demo.py all --auto          # no pauses
python simulator/demo.py all --no-sensor     # when the real ESP32 is connected
```
Also: `python simulator/demo.py checks` (a citizen report through both checks) and `checks --urgent` (the control
room's fast-track). With `--auto --auto-officer` the script plays the control room and field police itself.

**Honesty rules for the demo:** the simulator marks everything it sends as `simulated`, and the UI shows a dashed
**Simulated** badge on those incidents, hotspot readings and planned works; the rain override is labelled
"manual override set by an officer, not a forecast". Say this out loud during the demo. The evaluation uses a simplified
traffic model (static assignment with a BPR delay curve), not SUMO, and its numbers are model outputs, not field data.
Telugu text is template-based and should be checked by a native speaker.

## How verification works

| Evidence | Effect |
|---|---|
| Public reports (web, Telegram) | Never publish on their own. Any number of reports counts as **one untrusted source** (reporter ids are client-supplied). They need **two checks**: the control room, then field police at the spot. |
| Control room check | Sends the report to field police (not published), rejects it, or marks it **urgent**: published at once, field check still requested. |
| Field police check | Confirms it (published, routes change) or *not found* (closed; an urgent one goes back to the control room). |
| Keyed water sensor (`X-Device-Key`) | Trusted. Two consecutive readings ≥ 10 cm open an incident; confidence 0.8 verifies it. One control-room decision is enough for sensor-backed incidents. |
| Keyed fleet speed probes | **Not trusted**, weight 0.4. Judged per road link from 8+ vehicles against this road's normal speed for this time, ignoring junction zones, and the dip must last 3 minutes (`probe_rules.py`). It raises a *Suspected slowdown* and never publishes on its own; without trusted evidence it follows the two-check route. |
| Planned works (control room) | Trusted; active during its time window. |

Commuters and routing only use **Verified** or **Responding** incidents. The review stage is in every incident
(`review.stage`: `control_review`, `with_field`, `urgent_awaiting_field`, `field_disputed`, `confirmed`,
`trusted_evidence`, `rejected`, `closed`); the logic is in `backend/app/review.py`. Field endpoints:
`GET /api/field/tasks`, `POST /api/field/incidents/{id}/confirm` and `/not-found` (role *field*). Control-room
endpoints refuse field accounts with HTTP 403 and the other way round. A citizen can follow a report with
`GET /api/reports/{id}/status`, which returns only the outcome and stage, never evidence or names.

## Map: real Visakhapatnam roads or synthetic grid

The header shows which one is running; `/api/health` reports `map_mode` (`osm` or `synthetic`) and
`/api/network/stats` gives the loaded graph's real statistics.
- **Real OpenStreetMap roads (intended for the demo):** `data/area.graphml`, exported by `tools/fetch_map.py` for
  Dwaraka Nagar with a 1500 m radius. Loaded with these rules: directed edges exactly as OSM gives them (one-way
  roads stay one-way, no reverse edges invented); curved roads follow their real shape; of parallel edges between
  two junctions the one with the lowest estimated travel time is kept; speed from OSM `maxspeed` where it is a number,
  otherwise a road-class estimate (`speed_source: "estimate"`); the largest weakly connected part is kept.
- **Synthetic (fallback, no internet needed):** a 9×9 grid around the centre with deliberately artificial names.

**Demo hotspots are simulated.** Without a surveyed `data/hotspots.json`, the two demo hotspots are placed at the
midpoint of a real road segment near the centre and named "Demo hotspot A (simulated) · <road>". They are
demonstration points, **not known flood locations**; the UI marks them SIMULATED. The segment is chosen by rule, not
by a hard-coded coordinate (`Store.place_demo_hotspots`), in this order: a **named** road; **two-way** (one-way roads
are never used); at least 60 m long; severe waterlogging there leaves a **diversion for a two-wheeler** of at most
800 m in both directions; both ends join real junctions; then the closest to the target spot. If nothing meets a rule
the rule is dropped in that order, and the last resort is an unnamed two-way road, shown as "unnamed road".

**Routing is road-segment exact.** The start and destination are projected onto their nearest road segment
(limit `MAX_SNAP_M`, 250 m) and temporary split points are inserted into a per-request copy of the graph, so a route
can begin in the middle of a long road and respects one-way direction. Points further than 250 m from any road get
HTTP 422. Because one-way streets are kept, some pairs of points have no legal route; the API says so (422).

## What's where

| Path | What | Workstream |
|---|---|---|
| `backend/app/main.py` | HTTP API, live stream (SSE), lifecycle loop | B |
| `backend/app/fusion.py`, `services.py`, `severity.py` | confidence fusion, lifecycle, risk rules | B |
| `backend/app/probe_rules.py` | slowdown detection from many probes (link level, time-of-week baseline, persistence) | B |
| `backend/app/alerts.py`, `channels.py` | who is warned and when: pre-trip, in-trip, U-turn, all-clear, boards, Telegram | B |
| `backend/app/db.py`, `security.py` | SQLite persistence, auth, device keys, rate limits | B |
| `backend/app/forecast.py`, `config.py` | rain forecast, flood countdown, hotspots | B / A |
| `backend/app/roadgraph.py`, `routing.py`, `evaluate.py` | road graph, vehicle-aware routing, what-if, signals | C |
| `backend/app/review.py` | two-check review of citizen reports (control room, field police, urgent) | B |
| `backend/static/` | commuter map (`index.html`, `app.js`), control room (`control.html`), field app (`field.html`); shared `rp/core.js` and `rp/theme.css` (Leaflet, no build step) | D |
| `.vscode/` | VS Code tasks (start server, tests, demos) and debug launch | |
| `bot/telegram_bot.py` | citizen reports via Telegram | D |
| `firmware/esp32_water_sensor/` | ESP32 + JSN-SR04T depth sensor | A |
| `simulator/demo.py` | demo scenarios through the real API (`story`, `checks`, `all`) | C |
| `tools/fetch_map.py` | export a real OpenStreetMap area | C |
| `tools/loadtest.py` | load test of critical workflows | B |
| `docs/` | plan, API contract, incident schema | coordinator |

## Use the real Visakhapatnam map
```bash
pip install osmnx
python tools/fetch_map.py --lat 17.72867 --lon 83.308634 --dist 1500   # writes data/area.graphml, prints stats
```
Restart the server; the header should read "Real OpenStreetMap roads". Commit `data/area.graphml` so teammates and
the Docker image use the same map. For real (surveyed) flood spots, copy `docs/hotspots.example.json` to
`data/hotspots.json` and enter coordinates you have verified (site visit, news report, residents); do not guess.
Delete `data/area.graphml` to go back to the synthetic grid.

## Sensor (ESP32)
Open `firmware/esp32_water_sensor/esp32_water_sensor.ino`, set Wi-Fi, `API_URL` (your laptop's LAN IP),
`DEVICE_KEY` (one of `DEVICE_KEYS`), `SENSOR_ID` (matching a hotspot) and `DRY_DISTANCE_CM`, flash, and point it down at a container of water.

## Telegram bot
Create a bot with @BotFather, put the token in `.env`, then `python bot/telegram_bot.py`.
Users share a location, pick what they see, and can send `/alerts`.

## Tests and quality
```bash
python -m pytest -q tests          # 87 tests: domain rules, full report → reroute → dashboard → auto-clear chain,
                                   # OSM graph loading, road-segment routing, demo hotspot placement on the real
                                   # map, and regression tests for every issue found in the engineering reviews
```
CI (`.github/workflows/ci.yml`) runs the tests and a Docker smoke test on every push and pull request.

### Load test
```bash
REPORT_RATE_PER_MIN=1000000 ROUTE_RATE_PER_MIN=1000000 DEVICE_RATE_PER_MIN=1000000 \
  uvicorn app.main:app --app-dir backend --env-file .env --no-access-log --timeout-graceful-shutdown 3
python tools/loadtest.py --device-key <your DEVICE_KEYS value>
```
Phases: normal (10 clients, 15 s) → burst (50 clients, 7.5 s) → recovery (10 clients, 15 s), then 50 live-stream clients.

Measured 2026-11-27 on a 2-core cloud sandbox, **synthetic grid** (288 road segments), one active flood, before
road-segment routing was added. Re-run on the real Visakhapatnam graph before quoting numbers. Latency in ms:

| phase | workflow | req/s | p50 | p95 | p99 | errors |
|---|---|---|---|---|---|---|
| normal | route | 223 | 17 | 29 | 36 | 0% |
| normal | sensor reading | 96 | 29 | 55 | 75 | 0% |
| normal | report | 40 | 23 | 44 | 55 | 0% |
| normal | incident list | 95 | 20 | 34 | 42 | 0% |
| burst | route | 122 | 107 | 480 | 778 | 0% |
| burst | sensor reading | 84 | 140 | 469 | 625 | 0% |
| recovery | route | 206 | 18 | 32 | 53 | 0% |
| recovery | sensor reading | 93 | 28 | 63 | 91 | 0% |

Live stream: 50 / 50 clients connected and received an update; delivery p95 1.0 s (the server checks once a second).
Under the 5× burst, p95 rises to about 0.5 s and recovers fully afterwards. A real OpenStreetMap graph is larger than
the grid, so re-run this on your demo map before quoting any number; no other targets are claimed.

## Deploy
Any Docker host works. Free option: Render.com → New Web Service → connect this repo → Docker → add the `.env`
values as environment variables. Free instances sleep and have no persistent disk, so the SQLite data resets on
restart; that is acceptable for the demo (the simulator rebuilds the scenario in under a minute).

## Security notes
Officer actions need a signed bearer token (8 h); devices need `X-Device-Key`; reports, routes and device posts
are rate-limited per client IP (`X-Forwarded-For` is ignored unless `TRUST_PROXY=1`, so it cannot be faked to
dodge limits); every officer action is written to `audit_log`. Never commit `.env`; rotate the bot token after the
hackathon.

Officer live stream: the browser sends its login token in a header to `POST /api/stream/ticket` and gets a random
ticket that is valid for 60 seconds and works once; only that ticket appears in the stream URL. If the connection
drops, the spent ticket is refused (401) and the page fetches a new one automatically. Keep `--no-access-log` anyway
so short-lived tickets don't pile up in logs, and `--timeout-graceful-shutdown 3` so a restart is not blocked by open
live streams (both are in the Dockerfile).

Retention: raw sensor readings 7 days, public reports 30 days, cleared incidents 30 days; the audit log is kept.
Each incident keeps at most 120 evidence signals (latest plus strongest per source).

## Known limitations
- Single process (in-memory working state + SQLite); run one server instance only.
- Report-only incidents never auto-verify, by design: they need the control room and field police (or an urgent
  fast-track by the control room).
- Forecast needs internet. After a failure the API is not retried for 2 minutes; the last good forecast is used for
  up to 3 hours, then the countdown shows "no forecast available". Use the rain override offline.
- Officer accounts are configured in `.env`; there is no user-management screen.
- The ESP32 sketch has not been tested on hardware yet.
- No real traffic feed: congestion comes only from the speed probes the simulator (or a keyed device) posts, and the
  evaluation is a simplified model. There is no camera/YOLO detection, no trained prediction model and no control of
  traffic signals; signal output is advice for an officer.
- The demo hotspots are simulated points on real roads, and the demo water depths and rain are simulated. Real
  flood spots need surveyed coordinates in `data/hotspots.json`.
- A citizen flood report within 200 m of a hotspot takes that hotspot's name in its title even when it is a separate
  incident; the officer's Evidence panel shows the real road and location.
- The officer session token is signed with `ROADPULSE_SECRET`; if that is not set, a random one is generated per run and
  officers must log in again after a restart.
- Browser checks were automated in headless Chromium (Playwright): desktop 1440 px and phone 390 px, plus an
  end-to-end run with three separate browsers (citizen, control room, field police). Not yet checked on a real phone
  or in Firefox/Safari. Map tiles need internet; the road graph and routing do not.
- The field app does not assign checks to a particular officer: every field account sees every forwarded report.
- MVP is frozen: no new features without the coordinator's approval. The phase 6 changes (UI, legend, report tracking,
  heartbeat, map warning, `story` scenario) were explicitly approved.
