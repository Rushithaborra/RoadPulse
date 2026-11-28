# API reference

Interactive version: `/docs` on a running server (hide it with `API_DOCS=0`). This table is generated from the
application's own routes, so it matches the code. `?` after a field means optional.

## Who can call what

| Caller | How it authenticates |
|---|---|
| Anyone (commuters, citizens) | nothing; rate limited per IP |
| Control room | `POST /api/auth/login` returns `{token, role, expires_in}`; send `Authorization: Bearer <token>` (valid 8 hours) |
| Traffic police | same login with a `POLICE_USERS` account (`role: police`); a police token cannot use control-room endpoints and vice versa |
| Devices (water sensor, speed probes, the simulator) | `X-Device-Key: <one of DEVICE_KEYS>` |
| Alert subscribers | `X-Subscriber-Token` returned once by `POST /api/subscribers` |
| Officer live stream | `POST /api/stream/ticket` (officer token) gives a one-time ticket valid 60 s; open `/api/stream?ticket=...`. Without a ticket `/api/stream` is the public stream |

Public responses only ever contain **published** incidents (`Verified` or `Responding`); the one exception is `GET /api/reports/{id}/status`, which tells the person who filed a report only a coarse outcome (`pending`, `published`, `rejected`, `resolved`). Reporter ids, notes, raw evidence (`signals`), police findings, assignments and the warning history are **staff-only**: they appear only for a control-room token on `GET /api/incidents/{id}` and `?scope=all`.

## Errors

JSON with a plain sentence in `detail`. `401` not signed in, wrong password, bad key or token, or the wrong role for that endpoint. `404` unknown id. `409` the action is not allowed now (for example publishing a citizen report before police have checked it). `422` invalid input, including a point more than `MAX_SNAP_M` from a road, an impossible number, or a non-web `photo_url`. `429` too many requests. `500` returns `{"error": "internal error", "request_id": ...}` and never a stack trace.

Inputs from the simulator carry `"simulated": true`; incidents then report `simulated: true` and every app labels them.

## Endpoints

| Method | Path | Auth | Body fields | What it does |
|---|---|---|---|---|
| `GET` | `/api/audit` | control-room token |  |  |
| `POST` | `/api/auth/login` | none | `username`, `password` |  |
| `GET` | `/api/boards` | none |  |  |
| `GET` | `/api/boards/{board_id}` | none |  |  |
| `GET` | `/api/evaluation` | none |  |  |
| `GET` | `/api/feed` | none |  | Open incident feed, modelled on the Waze for Cities incident-feed fields. |
| `GET` | `/api/health` | none |  |  |
| `GET` | `/api/hotspots` | none |  |  |
| `GET` | `/api/incidents` | optional control-room token (staff see more) |  |  |
| `GET` | `/api/incidents/{incident_id}` | optional control-room token (staff see more) |  |  |
| `POST` | `/api/incidents/{incident_id}/assign` | control-room token | `unit` | Send a traffic-police unit to check this incident on the ground. |
| `GET` | `/api/incidents/{incident_id}/bulletin` | control-room token |  | Template bulletin in English and Telugu. Telugu wording should be checked by a native speaker. |
| `POST` | `/api/incidents/{incident_id}/dispatch` | control-room token | `unit`, `note`? | Record that police, an ambulance, a tow vehicle, drainage or a marshal was sent. RoadPulse does not call them: |
| `GET` | `/api/incidents/{incident_id}/signals` | control-room token |  |  |
| `GET` | `/api/incidents/{incident_id}/units` | control-room token |  |  |
| `GET` | `/api/incidents/{incident_id}/whatif` | control-room token |  |  |
| `POST` | `/api/incidents/{incident_id}/{action}` | control-room token |  |  |
| `GET` | `/api/network` | none |  |  |
| `GET` | `/api/network/stats` | none |  | Actual statistics of the loaded road graph (after loading rules: curves split, weak component kept). |
| `GET` | `/api/places` | none |  | Search roads and landmarks by name so people can pick a start and a destination without tapping the map. |
| `POST` | `/api/planned-works` | control-room token | `title`, `lat`, `lon`, `starts`, `ends`, `full_closure`?, `agency`?, `simulated`? |  |
| `GET` | `/api/police/calls` | police token |  |  |
| `POST` | `/api/police/calls/{incident_id}/report` | police token | `finding`, `need`?, `note`? | What the officer SEES on the scene. A strong signal; the control room still confirms before drivers are warned. |
| `POST` | `/api/police/calls/{incident_id}/status` | police token | `status` |  |
| `GET` | `/api/police/me` | police token |  |  |
| `POST` | `/api/police/position` | police token | `lat`, `lon` |  |
| `POST` | `/api/probes` | device key (`X-Device-Key`) | `vehicle_id`, `lat`, `lon`, `speed_kph`, `ts`?, `simulated`? |  |
| `POST` | `/api/reports` | none | `type`, `lat`, `lon`, `reporter_id`?, `note`?, `photo_url`?, `simulated`? |  |
| `GET` | `/api/reports/{incident_id}/status` | none |  | What happened to a report: the reporter keeps the incident id from POST /api/reports. Only a coarse outcome |
| `POST` | `/api/routes` | none | `from_lat`, `from_lon`, `to_lat`, `to_lon`, `vehicle`?, `alternatives`? |  |
| `POST` | `/api/sensors/readings` | device key (`X-Device-Key`) | `sensor_id`, `depth_cm`, `ts`?, `simulated`? |  |
| `GET` | `/api/stream` | none |  | Server-Sent Events: pushes a fresh snapshot whenever anything changes (checked every second). |
| `POST` | `/api/stream/ticket` | control-room token |  | Exchange the officer's bearer token (sent in a header) for a one-time, 60-second stream ticket. |
| `POST` | `/api/subscribers` | none | `name`, `vehicle`?, `voice`?, `telegram_chat`?, `home_lat`?, `home_lon`?, `work_lat`?, `work_lon`?, `depart_hhmm`?, `consent_location`? | Sign up for alerts. Consent to location use is required. The returned token is shown once and must be sent as |
| `DELETE` | `/api/subscribers/{sub_id}` | subscriber token (`X-Subscriber-Token`) |  | Erase the commuter's saved places and alert history. |
| `GET` | `/api/subscribers/{sub_id}` | subscriber token (`X-Subscriber-Token`) |  |  |
| `GET` | `/api/subscribers/{sub_id}/alerts` | subscriber token (`X-Subscriber-Token`) |  |  |
| `POST` | `/api/subscribers/{sub_id}/places` | subscriber token (`X-Subscriber-Token`) | `home_lat`?, `home_lon`?, `work_lat`?, `work_lon`?, `depart_hhmm`? |  |
| `POST` | `/api/subscribers/{sub_id}/position` | subscriber token (`X-Subscriber-Token`) | `lat`, `lon`, `speed_kph`?, `ts`?, `simulated`? | Live location while travelling. The route is checked against every published incident; if the turn-off is near |
| `POST` | `/api/subscribers/{sub_id}/trip` | subscriber token (`X-Subscriber-Token`) | `dest`?, `lat`?, `lon`?, `depart_in_min`? |  |
| `GET` | `/api/units` | control-room token |  | Police units for the control-room map: where each is (if shared), how fresh, and which incidents it is sent to. |
| `POST` | `/api/weather/override` | control-room token | `rain_mm_per_hr`? |  |

Notes

- `POST /api/incidents/{incident_id}/{action}`: `action` is `verify`, `respond` (approve a diversion), `clear` or `reject`. Optional body `{reason, severity, urgent}`. `verify` on a citizen report is refused with 409 until a police unit has confirmed, unless `urgent: true` (recorded in the audit log).
- `POST /api/police/calls/{id}/status` takes `{status}` of `accepted`, `on_way`, `arrived` or `declined`. `POST .../report` takes `{finding, need, note}` where `finding` is `blocked`, `one_lane`, `not_real` or `open`.
- `POST /api/routes` returns up to 3 routes with a traffic share each, the usual route, which verified incidents it crosses (`usual_route_hits`) and `no_route`.
- `GET /api/stream` sends a full snapshot as the default event whenever something changes and a `heartbeat` event about every 10 seconds.

Example (a simulated sensor reading, as the demo sends it):

```bash
curl -X POST http://localhost:8000/api/sensors/readings   -H "X-Device-Key: $DEVICE_KEY" -H "Content-Type: application/json"   -d '{"sensor_id": "sensor-01", "depth_cm": 35, "simulated": true}'
```
