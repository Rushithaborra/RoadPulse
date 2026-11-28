# Architecture

RoadPulse is one Python process plus one SQLite file and a plain HTML/JavaScript front end (no build step, no Node.js).
There is no message broker, no Kubernetes and no microservices. Everything runs on a laptop.

```
 Simulated water sensor ─┐
 (same endpoint a real    │  HTTP + X-Device-Key
 device would use)        ├──────────────►  FastAPI process
 Simulated speed probes ──┤                  ├─ fusion        confidence from independent sources
 Citizens (web, Telegram)─┤  HTTP, no login  ├─ lifecycle     5-second loop: auto-clear, expiry
 Police units (/police) ──┤  police token    ├─ field         police-check workflow
 Control room (/control) ─┤  officer token   ├─ routing       NetworkX on the OSM graph, per vehicle class
 Commuter app (/)  ───────┘  HTTP, no login  ├─ alerts        route-based warnings, boards, Telegram
                                             ├─ SQLite        write-through, reloaded on restart
                                             └─ SSE stream    /api/stream (the /classic dashboard)
```

Real versus simulated: the road graph, routing, verification rules and alert logic are real. Water depth, rain, speed probes and the roadside boards are **simulated** in the demo, and everything the simulator sends is flagged `simulated` and labelled SIMULATED in every app.

## Folder structure

```
backend/app/          the server (FastAPI)
  main.py             HTTP API, live stream (SSE), page routes, security headers
  models.py           request and incident models (pydantic)
  fusion.py           confidence fusion, merging of signals into incidents
  field.py            the police-check workflow (assign, accept, travel, arrive, report)
  services.py         citizen reports, sensor readings, lifecycle loop (auto-clear, expiry)
  severity.py         depth to severity, which vehicles are blocked
  probe_rules.py      suspected-slowdown detection from many vehicles (never publishes)
  roadgraph.py        loads the OSM graph, snapping, spatial index; synthetic fallback WITH a warning
  routing.py          vehicle-aware alternatives, load sharing, incident effects
  alerts.py           subscribers, pre-trip and in-trip alerts, all-clear, boards
  channels.py         Telegram delivery (optional, background thread)
  evaluate.py         simplified before/after traffic model (not SUMO)
  forecast.py         rain forecast (Open-Meteo, optional) and flood countdown
  security.py         tokens, device keys, rate limits, one-time stream tickets
  db.py               the only place that contains SQL
  store.py, geo.py, config.py
backend/static/       the front end: index.html (commuter), control.html, police.html, commute.html, board.html,
                      classic.html (detailed dashboard), rp.js / rp.css (shared), Leaflet (vendored)
data/area.graphml     the real OpenStreetMap road graph for Dwaraka Nagar (committed)
simulator/            demo.py (flood story, accident, two-stage police), alerts_demo.py
tests/                158 unit and API tests;  tests/e2e/  browser tests in real Chrome
tools/                fetch_map.py (export the graph), loadtest.py
bot/telegram_bot.py   optional Telegram front end
firmware/             ESP32 sensor sketch (written, NOT tested on hardware; not part of this software-only delivery)
docs/                 this documentation
```

## The apps

| Path | Who | Notes |
|---|---|---|
| `/` | commuters | map-first; plans a trip by search or by tapping; polls `/api/incidents` every 2.5 s; "Your reports" follows each report |
| `/commute` | subscribers | sign up for alerts (consent first), saved home and work, simulated drive, spoken alerts, delete my data |
| `/board?id=` | people with no app | a **simulated** roadside display; reads `/api/boards` |
| `/control` | control room | queue, evidence, send police, confirm, approve diversion, clear, warning history |
| `/police` | traffic police | phone-first calls with an offline queue |
| `/classic` | officers | the detailed dashboard (planned works, what-if, signal advice, bulletin, evaluation); uses the SSE stream |
| `/docs` | developers | interactive API reference (hide with `API_DOCS=0`) |

## Database (SQLite, `DATABASE_PATH`)

| Table | Holds | Retention |
|---|---|---|
| `incidents` | one row per incident, full JSON in `doc` | cleared incidents 30 days |
| `status_changes` | every status change with reason and actor | follows its incident |
| `sensor_readings` | raw water-depth readings | 7 days |
| `reports` | raw citizen reports | 30 days |
| `subscribers` | alert sign-ups: saved places, departure, token | deleted on request, and after 30 days of inactivity |
| `audit_log` | every officer action | kept |

The in-memory store is the working set; every change is written through, and open incidents and subscribers are reloaded on restart. Run **one** server instance only.

## Incident lifecycle

Statuses: `Reported` -> `Verified` -> `Responding` -> `Cleared`.

1. **Evidence arrives** as signals: citizen report (weight 0.35), water sensor (0.8), speed probes (0.4), police on the scene (0.9), planned works (1.0), officer (1.0). Signals of the same kind close together merge into one incident (`CLUSTER_RADIUS_M`, `CLUSTER_WINDOW_S`).
2. **Confidence** is `1 - product(1 - weight)` over distinct sources. All public reports count as **one** untrusted source however many arrive, because the reporter id is client-supplied.
3. **Auto-verification** needs confidence at least 0.7 **and** a trusted source (sensor, planned works or an officer). A citizen report alone, or speed probes alone, never publishes. A speed drop only raises a "suspected slowdown" for officers.
4. **Two-stage check for citizen reports** (`REQUIRE_FIELD_CONFIRMATION=1`, the default): the control room sends the nearest police unit; the unit accepts, travels, arrives and reports what it sees (blocked, one lane, not real, open); only then can the control room confirm. Before that, the control room is refused (HTTP 409). The only shortcut is **urgent**, which warns drivers at once and is recorded in the audit log.
5. **Published** means `Verified` or `Responding`: only these appear for commuters and in routing. The officer can approve a diversion (`Responding`) and change the severity of accidents and blockages.
6. **Recovery:** when the evidence says the road is back to normal (for a flood, water under 10 cm) a countdown of `CLEAR_WINDOW_S` starts and the incident clears by itself, or an officer clears it sooner. Police can also report "road is open".
7. **Staleness:** an incident with no new signal for `REPORT_EXPIRY_S` (one hour) expires. A cleared or expired incident stops affecting routes, the public feed, the boards and the alerts at once, and everyone who was warned gets an all-clear.
8. **Conflicting evidence** is handled safely by the rules above: nothing is published without trusted evidence or an officer, and a "not real" finding from police is shown to the control room, which dismisses the report (nothing was ever shown to drivers). A water sensor that reads dry does not erase a police confirmation: the incident clears only through the recovery rule or an officer.

## Routing

- The OSM graph is directed. One-way streets stay one-way, curved roads keep their real shape, parallel edges keep the fastest, the largest weakly connected part is kept (5,310 nodes, 10,345 edges for the shipped map).
- A start or destination is projected onto its nearest road **segment** (within `MAX_SNAP_M`, else HTTP 422) and a temporary node is inserted in a per-request copy of the graph, so a trip can start mid-block and respects one-way directions. The permanent graph is never modified.
- Each active published incident affects the road segments within `INCIDENT_EDGE_RADIUS_M` of it. For each vehicle class the effect is either a travel-time multiplier or a closed road. Water depth is compared with the **assumed** safe depth per class (two-wheeler 10 cm, auto 20, car 30, ambulance 30, bus 45): these are demo values, not safety guidance.
- Alternatives use a penalty method (up to 3, at most 1.4 times the best time) with a traffic share for each so a detour does not jam. "No route" is reported with a reason; one-way streets can make some pairs unreachable.

## Notification architecture

- **Recipients are chosen by route**, not radius: only a subscriber whose own route uses the affected road is warned. The check runs on every position update and whenever an incident changes.
- **Pre-trip** (a saved departure within `PRETRIP_LEAD_S`), **in-trip** (before the last reasonable turn-off, or "turn back" if it is behind them), and **all-clear** when the incident clears. Each person is warned **once per incident**, and only people who were warned get the all-clear.
- **Delivery** is the phone page's inbox (polled), spoken alerts, an optional Telegram message sent by the server in a background thread, and simulated roadside boards. Without a Telegram token everything else still works.
- **Privacy:** consent is required before location is used; the token is shown once and sent in a header; the alert log used by the control room is anonymous (time, kind, whether Telegram carried it); subscribers can delete everything (`DELETE /api/subscribers/{id}`).
- Limits: only people with the page open and location on can be reached; a phone browser pauses a page in a pocket.

## Security summary

Signed bearer tokens (HMAC-SHA256, 8 hours, role in the token), per-device keys, per-IP rate limits, a one-time 60-second ticket for the officer live stream (the long token never appears in a URL), SQL parameters everywhere (the only formatted SQL is a table-name count against a fixed whitelist), HTML escaping of all user text in the front end (checked in a browser with an injection test), no CORS (same origin only), safe response headers, `no-store` on private responses, secrets only in `.env`. The audit log records officer actions.
