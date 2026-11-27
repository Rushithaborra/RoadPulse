# RoadPulse

Low-cost, real-time road disruption detection and vehicle-aware rerouting.
A road disruption is reported → the incident is assessed → the affected road is identified →
an alternative route is calculated → the authority dashboard updates, live.

Hackathon prototype (AITAM virtual hackathon, Problem #3). Plan and contracts: [`docs/PLAN.md`](docs/PLAN.md).

## Run it (2 minutes)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                   # then edit the passwords and keys
uvicorn app.main:app --app-dir backend --reload --env-file .env
```
Open http://localhost:8000. Officer login uses `OFFICER_USERS` from `.env`
(without a `.env`, the demo default is `officer` / `roadpulse-demo`: change it before anyone else can reach the server).

With Docker: `cp .env.example .env && docker compose up --build`

## Demo scenario

In a second terminal (same `.env` values for `--device-key` and `--password`):
```bash
python simulator/demo.py all                 # step by step, press Enter between steps
python simulator/demo.py all --auto          # no pauses
python simulator/demo.py all --no-sensor     # when the real ESP32 is connected
```
1. Commuter view: pick **Two-wheeler**, click a start south of the railway underpass and a destination north of it.
2. Run the scenario: rain is set, water rises, the incident is verified, the bike route changes live.
   Switch the vehicle to **Bus**: the bus is still allowed through at 35 cm.
3. Authority view: log in, verify the accident, approve the flood diversion, run **What-if** / **Signals** / **Bulletin**,
   then **Before / after evaluation**.
4. Drain step: the incident auto-clears after `CLEAR_WINDOW_S` (120 s by default).

**Honesty rules for the demo:** the simulator sends *simulated* readings; say so. The evaluation uses a simplified
traffic model (static assignment with a BPR delay curve), not SUMO, and its numbers are model outputs, not field data.
Telugu text is template-based and should be checked by a native speaker.

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

## Use a real map
```bash
pip install osmnx
python tools/fetch_map.py --lat <lat> --lon <lon> --dist 1000   # writes data/area.graphml
```
Set `CENTER_LAT` / `CENTER_LON` in `.env` and move `HOTSPOTS` in `backend/app/config.py` onto real flood-prone roads.
Without `data/area.graphml` the app uses a synthetic 9×9 street grid around the centre.

## Sensor (ESP32)
Open `firmware/esp32_water_sensor/esp32_water_sensor.ino`, set Wi-Fi, `API_URL` (your laptop's LAN IP),
`DEVICE_KEY` (one of `DEVICE_KEYS`) and `DRY_DISTANCE_CM`, flash, and point it down at a container of water.

## Telegram bot
Create a bot with @BotFather, put the token in `.env`, then `python bot/telegram_bot.py`.
Users share a location, pick what they see, and can send `/alerts`.

## Tests and quality
```bash
python -m pytest -q tests          # 17 tests: domain rules + the full report → reroute → dashboard → auto-clear chain
python tools/loadtest.py           # start the server with high REPORT/ROUTE/DEVICE_RATE_PER_MIN first
```
CI (`.github/workflows/ci.yml`) runs the tests and a Docker smoke test on every push and pull request.

Measured on a 2-core cloud sandbox, 20 concurrent clients, 20 s, synthetic grid, one active flood, 0 errors:

| workflow | req/s | p50 ms | p95 ms |
|---|---|---|---|
| route | 102 | 78 | 124 |
| incident list | 44 | 91 | 147 |
| sensor reading | 22 | 210 | 645 |
| citizen report | 8 | 179 | 636 |

Sensor writes alone take 7 ms (p50); under mixed load they wait behind CPU-bound routing in the same Python process.
Next step if needed: move route computation to a process pool.

## Deploy
Any Docker host works. Free option: Render.com → New Web Service → connect this repo → Docker → add the `.env`
values as environment variables. Free instances sleep and have no persistent disk, so the SQLite data resets on
restart; that is acceptable for the demo (the simulator rebuilds the scenario in under a minute).

## Security notes
Officer actions need a signed bearer token (8 h); devices need `X-Device-Key`; reports, routes and device posts
are rate-limited per IP; every officer action is written to `audit_log`. Change all defaults in `.env`, never commit
`.env`, and rotate the bot token after the hackathon.

## Known limitations
Single process (in-memory working state + SQLite); one server instance only. Report-only incidents never
auto-verify (needs a second source or an officer), by design. Forecast needs internet; use the rain override offline.
