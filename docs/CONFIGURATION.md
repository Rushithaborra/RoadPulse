# Configuration reference

Every setting is an environment variable. Put them in `.env` (copy `.env.example`) or set them on the host.
`.env` is never committed. Values below are the defaults in `backend/app/config.py` and `backend/app/security.py`.

## Secrets and logins

| Variable | Default | What it does |
|---|---|---|
| `ROADPULSE_SECRET` | random per run | Signs login tokens (HMAC-SHA256, 8 hours). Set it so tokens survive a restart. |
| `OFFICER_USERS` | one random login, printed in the log | Control-room accounts `name:password,name:password`. A name starting with `supervisor` gets the supervisor role. |
| `POLICE_USERS` | one random login, printed in the log | Traffic-police accounts, same format. A name already in `OFFICER_USERS` is never downgraded to police. |
| `DEVICE_KEYS` | one random key, printed in the log | Keys for sensors and the simulator, sent as `X-Device-Key`. Comma separated. |

Passwords are compared in constant time. They live in configuration, not in the database (there is no user table), so use long random values and keep `.env` private.

## Map and data

| Variable | Default | What it does |
|---|---|---|
| `GRAPHML_PATH` | `data/area.graphml` | The OpenStreetMap road graph. If it is missing or cannot be loaded the server falls back to a synthetic grid, logs a warning, and every app shows a red warning (`map_warning` in `/api/health`). |
| `CENTER_LAT`, `CENTER_LON` | 17.728670, 83.308634 | Pilot area centre (Dwaraka Nagar). With a real graph the centre comes from the graph itself. |
| `HOTSPOTS_PATH` | `data/hotspots.json` | Surveyed flood spots. Without the file, two **simulated** points on real roads are used (see `docs/hotspots.example.json`). |
| `BOARDS_PATH` | `data/boards.json` | Simulated roadside boards. Without the file the busiest junctions are used (`BOARD_COUNT`, default 4). |
| `DATABASE_PATH` | `data/roadpulse.db` | SQLite file. |
| `MAX_SNAP_M` | 250 | A point further than this from a mapped road is rejected with HTTP 422. |
| `INCIDENT_EDGE_RADIUS_M` | 50 | How far an incident affects nearby road segments. |

## Incident rules

| Variable | Default | What it does |
|---|---|---|
| `REQUIRE_FIELD_CONFIRMATION` | 1 | Citizen reports need police on the scene before the control room can publish them (`0` turns it off). Sensor and planned-works incidents never wait for police. |
| `PUBLISH_CONFIDENCE` | 0.7 | Fused confidence needed, **together with at least one trusted source** (sensor, planned works or an officer), to publish automatically. |
| `CLUSTER_RADIUS_M`, `CLUSTER_WINDOW_S` | 300, 900 | Reports of the same kind within this distance and time are one incident. |
| `CLEAR_WINDOW_S` | 120 | Seconds of normal readings before an incident clears by itself (use 600 in a real pilot). |
| `REPORT_EXPIRY_S` | 3600 | Seconds with no new evidence before an incident expires. |
| `TICK_S` | 5 | How often the lifecycle loop runs. |

Fixed in code (`config.py`), tune only with pilot data: water-depth bands (Low under 10 cm, Medium under 30, Severe under 45, Closed from 45) and the **assumed** safe depth per vehicle: two-wheeler 10 cm, auto 20, car 30, ambulance 30, bus 45. These are starting values, **not safety guidance**.

## Speed probes (suspected slowdowns only, never published on their own)

`PROBE_MIN_VEHICLES` 8, `PROBE_SLOW_RATIO` 0.4, `PROBE_SLOW_FRAC` 0.6, `PROBE_SUSTAIN_S` 180, `PROBE_JUNCTION_ZONE_M` 60, `PROBE_WINDOW_S` 300, `PROBE_FALLBACK_FRACTION` 0.6, `PROBE_BASELINE_MIN_UPDATES` 5. See `backend/app/probe_rules.py`.

## Alerts and boards

`ALERT_WARN_HORIZON_S` 120, `ALERT_MIN_LEAD_S` 15, `ALERT_TURNOFF_SLACK_S` 60, `PRETRIP_LEAD_S` 900, `POSITION_FRESH_S` 120, `ALERT_MIN_EVAL_GAP_S` 2, `BOARD_LOOKAHEAD_M` 600, `BOARD_COUNT` 4, `UTC_OFFSET_S` 19800 (India Standard Time). Details in `docs/ALERTS.md`.

## Rate limits (per client IP, token bucket)

| Variable | Default per minute | Protects |
|---|---|---|
| `REPORT_RATE_PER_MIN` | 6 (burst 5) | citizen reports and sign-in attempts |
| `ROUTE_RATE_PER_MIN` | 120 (burst 30) | route calculations |
| `DEVICE_RATE_PER_MIN` | 600 (burst 100) | sensor and probe posts |
| `SUBSCRIBE_RATE_PER_MIN` | 10 (burst 5) | alert sign-ups |
| `EVAL_RATE_PER_MIN` | 6 (burst 3) | the public before/after evaluation (CPU heavy) |

## Server

| Variable | Default | What it does |
|---|---|---|
| `TRUST_PROXY` | 0 | Set `1` only behind your own reverse proxy so `X-Forwarded-For` is trusted for rate limiting. |
| `API_DOCS` | 1 | `0` hides `/docs` and `/openapi.json`. |
| `LOG_LEVEL` | INFO | Logging level. |
| `PORT` | 8000 | Used by the Docker image (Render sets it). |

## Optional: Telegram

`TELEGRAM_BOT_TOKEN` (empty by default) lets the server send alerts to subscribers who gave a Telegram chat id, and lets `bot/telegram_bot.py` take reports. `ROADPULSE_API` is the server address the bot talks to. **The app does not depend on Telegram**, and this integration has not been tested against a live bot in this project.
