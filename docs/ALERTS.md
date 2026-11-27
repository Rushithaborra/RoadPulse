# Alert delivery: who is warned, when, and how

This layer sits on top of RoadPulse's detect → verify → reroute pipeline and answers one question: **how does the
right person find out in time?** It was designed by working through the doubts below. Each row says what the code
does about it and where the test is (`tests/test_alerts.py`, `tests/test_bot.py`).

| Doubt | What the code does |
|---|---|
| **Accidents and construction, not only floods. Who updates them?** | Accidents come from citizen reports, camera/sensor signals and fleet probes, and an **officer decides** (`Verify`, and chooses the severity: Low / Medium / Severe / **Closed**). Construction is **never detected**: the agency enters it as planned works with a time window, and it is included in pre-trip alerts if it overlaps the departure. |
| **Speed alone cannot decide (a senior citizen drives slowly)** | Probe rule v2 in `probe_rules.py`: a vehicle gets **one vote**; at least **8 distinct vehicles**; a **clear majority (60%) and the median** must be under 40% of **this road's normal speed for this time of week** (learned; a fraction of the speed limit until learned); samples within 60 m of a **junction are ignored** (signal queues); the dip must **last 3 minutes**; if neighbouring links are slow too it is **congestion**, not an incident. A probe is **not a trusted source**: it only creates a *Suspected slowdown* for officers. |
| **An accident between two signals** | Probes are judged per road **link** (junction to junction, curve points merged), and the incident is placed **where the slow vehicles are**, so it needs no sensor at a junction. Citizen reports and calls work anywhere on the map. |
| **Who sends the alert, how, and to whom? Not everyone needs it.** | A human publishes the incident; the system then warns **only people whose own route uses the affected road** (`alerts.plan_trip`). There is **no fixed radius**. Channels: the phone page (optionally spoken), Telegram, and simulated roadside boards. |
| **Someone already travelling reaches the area later** | The incident is a **standing** one. The route is re-checked on **every position update** and whenever an incident is published, so a person who sets off after verification is warned when it becomes relevant. Each person is warned once per incident. |
| **They may not see the message, and may pass the last turn-off** | The planner finds where their usual route and the best way round part, then prefers the **last turn-off that costs at most 60 s extra** (more time to react) and warns when it is **within 120 s**. A warning under 15 s is flagged **late**. If they are already past it they get **"Turn back"** with a new route. |
| **Voice alerts only exist inside navigation** | Right: so the alert is not limited to navigation. Order of reach: **pre-trip** alert (read at home, safest), **commute mode** page (spoken, but only while open), **Telegram live location** (keeps updating in the background), and **boards** (need nothing from the driver). |
| **What happens when an accident occurs?** | Detect → verify (human) → set severity → **dispatch** police / ambulance / tow (recorded, with audit) → affected people warned with a diversion → road reopens → **everyone who was warned hears the all-clear**. |

## Validation: police on the ground, then the control room

A citizen report is untrusted and never publishes on its own. The flow:

1. **Report** arrives (untrusted, one source however many people send it).
2. **Control room sends the nearest police unit** (`POST /api/incidents/{id}/assign`; units ranked by distance).
3. **Police** accept, travel, arrive (`/api/police/calls/{id}/status`) and report what they see (`/report`): blocked,
   one lane, not real, or open, plus a request for an ambulance or tow. This is strong evidence but **does not publish**.
4. **Control room confirms** with one tap; the police finding becomes the default severity. Drivers are warned now.
5. **Police say the road is open** and the control room clears it (or traffic recovery does).

Fast track: the control room can confirm as `urgent` with no police check; this is recorded in the audit log.
Trusted sources (water sensor, camera, planned works) skip the police step. Police tokens cannot use control-room
endpoints and the control room cannot use police ones. Turn the step off with `REQUIRE_FIELD_CONFIRMATION=0`.
Tests: `tests/test_field.py`.

## The flow

```
fleet probes ──► probe_rules (many vehicles, link level, normal for this hour, must last) ──► Suspected (officers only)
citizen / camera / 112 / planned works ──► fusion ──► Reported
                                                         │  officer verifies + sets severity (+ dispatches units)
                                                         ▼
                                                    Verified  ──► alerts.on_status ──► for each subscriber:
                                                                     usual route vs best route round the incident
                                                                     ├─ pre-trip (before departure, incl. planned works)
                                                                     ├─ in-trip (turn-off within 120 s, else keep checking)
                                                                     └─ U-turn if already past the turn-off
                                                    boards: /api/boards  (roads that lead to the incident, within 600 m)
Cleared ──► "road clear" to everyone who was warned
```

## API (all new)

| Method and path | Who | Purpose |
|---|---|---|
| `POST /api/subscribers` | public (rate limited) | Sign up. `consent_location: true` is **required**. Returns `id` and a `token` (shown once). |
| `GET/DELETE /api/subscribers/{id}` | `X-Subscriber-Token` | Read or **erase** a commuter's data. |
| `POST /api/subscribers/{id}/places` | token | Home, work, usual departure `HH:MM` (India time). |
| `POST /api/subscribers/{id}/trip` | token | Start a trip (`dest: work/home` or coordinates) and/or set `depart_in_min`. |
| `POST /api/subscribers/{id}/position` | token | Live location. Returns the alert if one is due. |
| `GET /api/subscribers/{id}/alerts?since=` | token | The inbox. |
| `GET /api/boards`, `GET /api/boards/{id}` | public | What each simulated board shows. Pages: `/board`, `/board?id=b1`. |
| `POST /api/incidents/{id}/dispatch` | officer | Record `police`, `ambulance`, `tow`, `drainage`, `marshal`. |
| `POST /api/incidents/{id}/verify` or `respond` | officer | Body `{"severity": "Closed"}` is now accepted. |

Pages: `/commute` (sign up, map, spoken alerts, GPS or **simulated drive**), `/board` (roadside display).
Telegram: `bot/telegram_bot.py` (`/start /agree /home /work /depart /go /forget`; share **live location**).

## Run the story

```bash
python simulator/alerts_demo.py --url http://localhost:8000 --device-key $DEVICE_KEY --password $OFFICER_PASS
```
It signs up two commuters, shows speed alone giving only a suspicion, a citizen report not being enough, the officer
closing the road and sending units, the pre-trip and in-trip alerts (with lead time), the boards, the all-clear and
data deletion. Add `--auto` to skip the pauses. Everything it sends is marked simulated.

## Settings (environment variables, defaults in `config.py`)

`PROBE_MIN_VEHICLES=8`, `PROBE_SLOW_RATIO=0.4`, `PROBE_SLOW_FRAC=0.6`, `PROBE_SUSTAIN_S=180`,
`PROBE_JUNCTION_ZONE_M=60`, `ALERT_WARN_HORIZON_S=120`, `ALERT_MIN_LEAD_S=15`, `ALERT_TURNOFF_SLACK_S=60`,
`PRETRIP_LEAD_S=900`, `BOARD_LOOKAHEAD_M=600`, `BOARD_COUNT=4` (or `data/boards.json`), `TELEGRAM_BOT_TOKEN` (server).
All are starting values to tune on real data.

## What is NOT true yet (say this honestly)

- **Only people with the page, the bot or a board can be reached.** Everyone else gets nothing.
- **The phone page only gets location while it is open with the screen on.** Browsers pause it in a pocket. Telegram
  live location is the background option, but it is **not tested with a live bot**. Server-side Telegram sending is
  also untested against the real API (it is skipped without a token).
- **Spoken alerts** use the browser's speech engine and need one tap to enable; they may not play on a locked phone.
- **Boards are web pages.** A real board needs hardware, power and a data link.
- **"Normal speed for this time of week" is learned only after 5 normal observations per link and hour**; until then it
  is 60% of the speed limit, an assumption. It is kept in memory and is lost on restart.
- **Mid-block accuracy depends on probe density.** With fewer than 8 vehicles in 5 minutes, nothing is raised.
- **Fleet probes are simulated here.** There is no real vehicle fleet feeding the service.
- Passenger-side heading is not known from a single position: at the first junction the system assumes the person
  faces along their usual route.
- Saved places are personal data: they need consent, can be deleted by the owner, and expire after 30 days of
  inactivity. A real deployment needs a privacy notice and a retention decision.
