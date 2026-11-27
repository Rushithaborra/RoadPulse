# RoadPulse — Phase 0 plan (one page)

Full design rationale: "RoadPulse — System Architecture Design v0.1" (team doc). This page is the build contract.

## 1. MVP scope (must demo)
Report → assess → identify affected road → alternative route → dashboard updates.
1. Interactive road map (real OSM area or synthetic grid fallback)
2. Incident reporting: citizen report API/bot, water sensor API, speed probes
3. Verification + management: confidence fusion, lifecycle Reported → Verified → Responding → Cleared, auto-clear.
   **Verification rule:** auto-verify only when confidence ≥ 0.7 AND at least one trusted source (keyed sensor, keyed
   fleet probe, planned works, officer). Public reports, however many, count as one untrusted source and never verify
   on their own. Commuters only see Verified / Responding incidents.
4. Risk engine: depth → severity, vehicle-class passability, flood countdown
5. Dynamic alternatives: vehicle-aware, load-balanced routes
6. Authority dashboard (login required for actions)
7. Commuter view
8. Real-time updates (Server-Sent Events)

Optional after MVP works: what-if, signal advice, planned works UI, Telegram bot, weather API (code present; can be cut).

## 2. Architecture
One FastAPI process (modular monolith) + SQLite file + static frontend. No broker, no Kubernetes.

```
ESP32 / simulator ─HTTP─┐
Citizen (web/bot) ──────┼─> FastAPI ── fusion ── lifecycle loop (5 s)
Fleet probes ───────────┘      │          │
                               │       routing (NetworkX, per vehicle class)
                               ├─ SQLite (write-through, reload on restart)
                               └─ SSE /api/stream ──> commuter + authority UI
```

## 3. Database (SQLite, `data/roadpulse.db`)
| table | columns |
|---|---|
| incidents | id PK, type, status, severity, confidence, lat, lon, updated, doc (full JSON) |
| status_changes | incident_id, status, at, reason, actor |
| sensor_readings | sensor_id, depth_cm, at |
| reports | id, type, lat, lon, reporter_id, note, at |
| audit_log | at, actor, action, target, detail |

## 4. API contract (v1)
| method | path | auth | purpose |
|---|---|---|---|
| GET | /api/health | – | liveness + graph source |
| GET | /api/network | – | road segments for the map |
| GET | /api/incidents?scope=public\|all | all needs officer | incident list |
| POST | /api/reports | – (rate limited) | citizen report |
| POST | /api/sensors/readings | X-Device-Key | water depth |
| POST | /api/probes | X-Device-Key | speed sample |
| POST | /api/routes | – | vehicle-aware alternatives |
| GET | /api/hotspots | – | depth + countdown |
| POST | /api/auth/login | – | officer token |
| POST | /api/incidents/{id}/verify\|respond\|clear\|reject | officer | manage incident |
| POST | /api/planned-works | officer | construction window |
| POST | /api/weather/override | officer | demo rain input |
| GET | /api/incidents/{id}/whatif, /signals | officer | decision support |
| GET | /api/evaluation | – | before/after (simplified model) |
| GET | /api/feed | – | open incident feed |
| POST | /api/stream/ticket | officer | one-time 60 s ticket for the officer stream |
| GET | /api/stream[?ticket=] | – / ticket | SSE: public snapshot, or officer snapshot with a valid ticket (401 if spent) |
| POST | /api/subscribers | – (rate limited, consent required) | alert sign-up; returns id + token (see `docs/ALERTS.md`) |
| GET/DELETE | /api/subscribers/{id} | subscriber token | read / erase own data |
| POST | /api/subscribers/{id}/places, /trip, /position | subscriber token | saved places, start a trip, live location |
| GET | /api/subscribers/{id}/alerts | subscriber token | alert inbox |
| GET | /api/boards, /api/boards/{id} | – | simulated roadside boards |
| POST | /api/incidents/{id}/dispatch | officer | record police / ambulance / tow sent |

Incident JSON: see `docs/incident_schema.json`.
Errors: `422 {"detail": "<field>: <message>; ...", "errors": [{"field", "message"}]}` for invalid input, including
points more than `MAX_SNAP_M` (250 m) from the nearest mapped road segment; `401` login/device key/stream ticket;
`409` action not allowed in the current state; `429` rate limit. Inputs may carry `"simulated": true` (demo data);
incidents then report `simulated: true` and the UI labels them.

**MVP frozen 2026-11-27** (git tag `mvp-freeze`, after demo hotspot placement was optimised on the real map).
No optional features without explicit approval; after the freeze only bug fixes, data (surveyed hotspots) and demo prep.

## 5. Decisions (changes vs design doc flagged)
- Python 3.12+, FastAPI, NetworkX routing, Leaflet frontend **without a build step** (changed from React: no bundler to break in 20 h; teammates edit one HTML/JS app).
- SQLite via stdlib (changed from PostgreSQL: zero setup; repository module `db.py` is the only place SQL lives).
- Sensors post over HTTPS with a per-device key (changed from MQTT: no broker to run; MQTT can be added in front later).
- Auth: officer password → signed bearer token (HMAC, 8 h expiry). Rate limit: token bucket per IP on write endpoints.

## 6. Tasks and dependencies
| # | task | depends on | workstream |
|---|---|---|---|
| T1 | contracts (this page, schema) | – | coordinator |
| T2 | backend core + DB + API | T1 | B |
| T3 | routing + evaluation | T1 | C |
| T4 | frontend views | T1 (mock via API) | D |
| T5 | ESP32 sketch → /api/sensors/readings | T2 | A |
| T6 | simulator demo scenario | T2, T3 | C |
| T7 | Telegram bot → /api/reports | T2 | D |
| T8 | tests, CI, Docker, deploy | T2–T4 | B |
| T9 | evidence, deck, rehearsal | T6 | E |

## 7. Primary failure risks
| risk | mitigation |
|---|---|
| Integration drift | contracts above are frozen; change only via coordinator |
| Venue network blocks APIs | rain override; synthetic map; backup video |
| Sensor hardware fails live | simulator uses the same endpoint |
| Real OSM graph too slow | keep area ≈ 2 km × 2 km |
| Demo state lost on restart | SQLite reload + simulator replays in < 1 min |
