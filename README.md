# RoadPulse

Low-cost, real-time road disruption detection and vehicle-aware rerouting.
A road disruption is reported → the incident is assessed → the affected road is identified →
an alternative route is calculated → the authority dashboard updates, live.

Hackathon prototype (AITAM virtual hackathon, Problem #3). Demo area: Dwaraka Nagar, Visakhapatnam
(17.728670 N, 83.308634 E). Plan and contracts: [`docs/PLAN.md`](docs/PLAN.md).

Road data and map tiles © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), available under the
Open Database Licence (ODbL). The attribution is shown on the map and must stay visible.

## Run it (2 minutes)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                   # then edit the passwords and keys
uvicorn app.main:app --app-dir backend --reload --env-file .env --no-access-log --timeout-graceful-shutdown 3
```
Open http://localhost:8000. Officer login uses `OFFICER_USERS` from `.env`. There are no built-in passwords:
if `OFFICER_USERS` or `DEVICE_KEYS` is missing, the server generates random ones for that run and prints them in its log.

With Docker: `cp .env.example .env && docker compose up --build`

## Demo scenario

In a second terminal (the simulator reads `DEVICE_KEYS` and `OFFICER_USERS` from `.env`):
```bash
python simulator/demo.py all                 # step by step, press Enter between steps
python simulator/demo.py all --auto          # no pauses
python simulator/demo.py all --no-sensor     # when the real ESP32 is connected
```
1. Commuter view: pick **Two-wheeler**, click a start on the road a little south of *Demo hotspot A* (the circle on
   the map) and a destination north of it, on the same road.
2. Run the scenario: rain is set, water rises, the sensor incident is verified, the bike route changes live.
   Switch the vehicle to **Bus**: the bus is still allowed through at 35 cm.
3. Authority view: log in. The accident (speed drop + one public report) is **pending**: verify it, then approve the
   flood diversion, run **What-if** / **Signals** / **Bulletin**, then **Before / after evaluation**.
4. Drain step: the incident auto-clears after `CLEAR_WINDOW_S` (120 s by default).

**Honesty rules for the demo:** the simulator marks everything it sends as `simulated`, and the UI shows a dashed
**SIMULATED** badge on those incidents, hotspot readings and planned works; the rain override is labelled
"manual override set by an officer, not a forecast". Say this out loud during the demo. The evaluation uses a simplified
traffic model (static assignment with a BPR delay curve), not SUMO, and its numbers are model outputs, not field data.
Telugu text is template-based and should be checked by a native speaker.

## How verification works

| Evidence | Effect |
|---|---|
| Public reports (web, Telegram) | Shown to officers as **pending**. Any number of reports counts as **one untrusted source** (reporter ids are client-supplied, so they prove nothing). Never auto-verified. |
| Keyed water sensor (`X-Device-Key`) | Trusted. Two consecutive readings ≥ 10 cm open an incident; confidence 0.8 verifies it. |
| Keyed fleet speed probes | Trusted, weight 0.5: verifies only together with other evidence (e.g. 0.5 + report 0.35 = 0.675 stays pending). |
| Planned works (officer) | Trusted; active during its time window. |
| Officer **Verify** | Always verifies and publishes. |

Commuters and routing only use **Verified** or **Responding** incidents. Confidence is still computed and shown to
officers for every incident.

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
midpoint of a real two-way road segment near the centre and named "Demo hotspot A (simulated) · <road>". They are
demonstration points, **not known flood locations**; the UI marks them SIMULATED.

**Routing is road-segment exact.** The start and destination are projected onto their nearest road segment
(limit `MAX_SNAP_M`, 250 m) and temporary split points are inserted into a per-request copy of the graph, so a route
can begin in the middle of a long road and respects one-way direction. Points further than 250 m from any road get
HTTP 422. Because one-way streets are kept, some pairs of points have no legal route; the API says so (422).

## What's where

| Path | What | Workstream |
|---|---|---|
| `backend/app/main.py` | HTTP API, live stream (SSE), lifecycle loop | B |
| `backend/app/fusion.py`, `services.py`, `severity.py` | confidence fusion, lifecycle, risk rules | B |
| `backend/app/db.py`, `security.py` | SQLite persistence, auth, device keys, rate limits | B |
| `backend/app/forecast.py`, `config.py` | rain forecast, flood countdown, hotspots | B / A |
| `backend/app/roadgraph.py`, `routing.py`, `evaluate.py` | road graph, vehicle-aware routing, what-if, signals | C |
| `backend/static/` | commuter + authority UI (Leaflet, no build step) | D |
| `bot/telegram_bot.py` | citizen reports via Telegram | D |
| `firmware/esp32_water_sensor/` | ESP32 + JSN-SR04T depth sensor | A |
| `simulator/demo.py` | demo scenario through the real API | C |
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
python -m pytest -q tests          # 64 tests: domain rules, full report → reroute → dashboard → auto-clear chain,
                                   # OSM graph loading, road-segment routing, and regression tests for every
                                   # issue found in the engineering reviews
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
- Report-only incidents never auto-verify, by design; an officer must verify them.
- Forecast needs internet. After a failure the API is not retried for 2 minutes; the last good forecast is used for
  up to 3 hours, then the countdown shows "no forecast available". Use the rain override offline.
- Officer accounts are configured in `.env`; there is no user-management screen.
- The ESP32 sketch has not been tested on hardware yet.
- MVP is frozen: no new features without the coordinator's approval.
