# RoadPulse

Road disruption detection and vehicle-aware rerouting for Visakhapatnam. A road disruption is reported, assessed,
verified, mapped and used to reroute commuters, while the traffic authority and police manage it, live.

Virtual hackathon project (AITAM, Problem #3). Pilot area: **Dwaraka Nagar and the RTC Complex (Dwaraka Bus Station)**
on real OpenStreetMap roads. This is a **software-only** delivery: no hardware is needed or claimed.

## What is real, what is simulated

| Real | Simulated (and labelled SIMULATED in every app) |
|---|---|
| The OpenStreetMap road graph (5,310 nodes, 10,345 directed edges, one-way streets kept) and the routing on it | Water depth, rain and the water sensor in the demo |
| The verification rules, the incident lifecycle, the two-stage police check | Speed probes (vehicles) in the demo |
| Route-based alerts, pre-trip and in-trip warnings, the all-clear | The two flood-watch points (simulated points on real roads, not known flood spots) |
| The commuter, control-room and police apps, and the live updates | The roadside boards (a web page that shows what a board would display) |
| 179 automated tests, 21 of them in a real browser | The assumed safe water depth per vehicle (two-wheeler 10 cm, auto 20, car 30, ambulance 30, bus 45): demo values, **not safety guidance** |

**Not built:** camera or YOLO detection, a trained prediction model, control of traffic signals (the signal output is advice
for an officer), real traffic feeds, Telegram tested against a live bot, the ESP32 sensor hardware (a sketch exists in
`firmware/` but is not part of this delivery and was never run on hardware).

## Run it (5 minutes, fresh machine)

Python 3.12 or newer. No Node.js. One server serves the API and every app.

**Windows (PowerShell)**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1         # the leading .\ is required; if blocked: Set-ExecutionPolicy -Scope Process Bypass
pip install -r requirements-dev.txt
Copy-Item .env.example .env          # then edit the logins, keys and secret in .env
uvicorn app.main:app --app-dir backend --env-file .env --no-access-log --timeout-graceful-shutdown 3
```

**macOS / Linux**
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                 # then edit the logins, keys and secret
uvicorn app.main:app --app-dir backend --env-file .env --no-access-log --timeout-graceful-shutdown 3
```

Open **http://localhost:8000**. Check `http://localhost:8000/api/health`: it must say `"map_mode":"osm"` and
`"map_warning":null`. If `data/area.graphml` is missing or cannot be loaded, the server falls back to a synthetic grid and
**every app shows a red warning**: it is never silent. Docker: `docker compose up --build` (see `docs/DEPLOY.md`).

## The apps

| Address | Who | What it does |
|---|---|---|
| `/` | Commuters | Map-first. Search or tap a start and a destination, get a route on real roads, a clear card when a road is affected (usual way dashed red, better way green), a "See a way round" button, a report form, and **Your reports** which follows each report to its outcome. Updates by itself. |
| `/commute` | Subscribers | Sign up for alerts (consent first), saved home and work, a planned departure, a simulated drive, spoken alerts, an inbox, **Delete my data**. |
| `/board?id=` | People with no app | A **simulated** roadside board: what to avoid and which way to use, or ROAD CLEAR. |
| `/control` | Control room | Queue of reports, the evidence and how sure the system is, send the nearest police unit, confirm, **Approve diversion**, recovery monitoring, clear, anonymous history of warnings sent. |
| `/police` | Traffic police | Phone-first: get a call, accept, travel, arrive, report what you see, ask for an ambulance or tow, say the road is open. Works through a weak signal. |
| `/classic` | Officers | The detailed dashboard: planned works, what-if, signal-timing advice, bulletin draft, before/after evaluation. Uses the live SSE stream. |
| `/docs` | Developers | Interactive API reference (hide on a public server with `API_DOCS=0`). |

## The rules that matter

- A citizen report **alone never publishes**, however many arrive. Speed probes alone never publish either: they only raise a "suspected slowdown" for officers.
- Publishing needs confidence of 0.7 **and** a trusted source (water sensor, planned works or an officer). Citizen reports of accidents and blockages also need **police on the scene, then the control room** (the control room is refused with HTTP 409 until police have checked); the only shortcut is **urgent**, which is recorded in the audit log.
- Commuters and routing only ever see published incidents. A cleared or expired incident stops affecting routes, the public feed, boards and alerts immediately, and everyone who was warned gets an all-clear.

Details: `docs/ARCHITECTURE.md` (lifecycle, routing, notification design), `docs/ALERTS.md`.

## Demo

`docs/DEMO.md` is the 8-minute script with every click, command, expected result and recovery step: a flood from a commuter's
first route to the all-clear, then an accident through the two-stage police check, with alerts and the simulated board.

```powershell
python simulator/demo.py story --auto --auto-officer --device-key $K --user officer --password $P   # the flood story, no browser
python simulator/demo.py accident-field --auto --device-key $K --user officer --password $P --police-user unit1 --police-password $U
python simulator/alerts_demo.py --device-key $K --password $P --police-user unit1 --police-password $U --auto
```

## Tests

```powershell
python -m pytest -q tests --ignore=tests/e2e      # 158 unit and API tests, about 20 seconds
pip install -r requirements-e2e.txt               # once: the Playwright package (uses your installed Chrome or Edge)
python -m pytest -q tests/e2e                     # 21 tests in a real browser, about 2 minutes
```

The browser tests start their own server on the real map with a temporary database and play the whole story in six
browser windows at once (commuter, citizen, control room, two alert subscribers, the board), the accident workflow with a police
phone, wrong and non-ASCII passwords, forged sessions, script-injection attempts, security headers, phone layouts,
live SSE updates, and recovery after a server restart. They block external map tiles so they run offline. They skip, with a
reason, only if Playwright or a Chrome/Edge browser is not installed.

Measured once on 28 Nov 2026 with `tools/loadtest.py` on a Windows 11 laptop, real map, the server and the load generator on
the same machine, one server process: every request succeeded (0 errors). With 10 clients the median request took
0.3 to 0.4 s; with 50 clients 1.5 to 1.8 s (p95 up to 3.3 s); it recovered fully afterwards; the live stream delivered an
update to all 50 clients (p95 0.7 s). This is not a capacity claim: run it on your own host before relying on any number.

## Documentation

| File | Contents |
|---|---|
| `docs/DEMO.md` | the demo script |
| `docs/ARCHITECTURE.md` | components, folder structure, database, incident lifecycle, routing, notification design, security |
| `docs/API.md` | every endpoint, who may call it, request fields, error codes |
| `docs/CONFIGURATION.md` | every environment variable with its default |
| `docs/DEPLOY.md` | local, Docker and Render, with a checklist before going public |
| `docs/ALERTS.md` | how alerts are chosen and delivered, with the honest limits |
| `docs/PLAN.md` | the original plan and API contract (the scope was frozen on 27 Nov 2026) |

## Security notes

Signed tokens (8 hours, role inside), device keys, per-IP rate limits, one-time stream tickets, parameterised SQL, escaped
output, safe response headers, `no-store` on private responses, no CORS, secrets only in `.env`, an audit log of officer actions.
Staff accounts are set in configuration (no user table, no password reset); there is no token revocation other than changing
`ROADPULSE_SECRET`. Subscriber data is deleted on request and after 30 days of inactivity.

## Known limitations

- One server process; in-memory working state plus SQLite. Do not run two instances.
- The free hosting plan sleeps and loses data on restart (see `docs/DEPLOY.md`).
- Incidents expire an hour after their last evidence (`REPORT_EXPIRY_S`); a silent sensor therefore keeps a flood alive for up to that hour unless an officer clears it.
- The rain forecast needs internet; use the officer rain setting offline. Map tiles need internet; roads and routing do not.
- The "before/after evaluation" is a simplified traffic model (not SUMO): its numbers are model output, not field data.
- Alerts reach only people with the page open and location on (a phone browser pauses a page in a pocket) or a linked Telegram chat.
- Tested in Chrome (desktop and a 390 px phone size). Not tested on real phones, other browsers, or against a live Telegram bot, and not deployed on Render or built with Docker in this environment.
- Telugu bulletin text is template-based and should be checked by a native speaker.

## Licences and data

Road data and map tiles (c) [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), ODbL: the attribution stays visible
on every map. Satellite imagery (c) Esri, Maxar, Earthstar Geographics (internet needed; follows Esri's terms). Leaflet is
vendored (BSD 2-clause). No external scripts or fonts are loaded; only the map tiles come from the internet.
