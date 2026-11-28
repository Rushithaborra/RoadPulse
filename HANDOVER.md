# RoadPulse handover (28 November 2026)

Repo: https://github.com/Rushithaborra/RoadPulse, branch `main`, latest commit `48aba6a`.
CI on that commit: **green** (147 tests, Docker image built, container smoke test passed).

This file says what changed since the earlier deployment handover (`RoadPulse_Handover.pdf`), how to run and deploy it,
and what is still to do. Everything in the old handover about Render, the demo script and the deck still applies unless
this file says otherwise.

## 1. What is new

| Area | What it does |
|---|---|
| **Three separate apps** | `/` commuters, `/control` control room, `/police` traffic police in the field. All phone-first, all with a Map / Satellite switch. `/classic` is the old detailed dashboard (what-if, signal advice, bulletins, planned works). |
| **Two-step validation** | A citizen report never publishes alone. Control room sends the nearest police unit, police confirm on the scene, then the control room confirms (one tap) and drivers are warned. The control room can fast-track an emergency as *urgent* (recorded in the audit log). Sensors, cameras and planned works skip the police step. |
| **Alerts to people** | Warned **by route, not by radius**, before the last turn-off, before they leave (pre-trip), and again when the road clears. Details: `docs/ALERTS.md`. |
| **Stricter speed rule** | Slow traffic needs 8+ vehicles, a slow majority, this road's normal speed for this time, junction zones ignored, and 3 minutes of persistence. Speed alone only gives officers a "suspected slowdown" and never publishes. |
| **Police field app** | Accept / on the way / arrived, report what they see (blocked, one lane, not real, open), ask for ambulance or tow. Works on a weak signal: updates queue on the phone and send by themselves. Keeps the screen on, shows live distance. |
| **Control room** | Queue, step tracker, nearest-unit list, live police markers on the map, plain "what happened" timeline, optional sound alert. |
| **Commuters** | Place search by name, saved Home/Work, share a warning, "where to?" trip check with a way round, report a problem, spoken alerts page at `/commute`, simulated roadside boards at `/board`. |
| **Telegram bot** | `/start /agree /home /work /depart /go /forget` plus live location. **Not tested with a real bot.** |

## 2. Run it locally (2 minutes)

```bash
git clone https://github.com/Rushithaborra/RoadPulse.git && cd RoadPulse
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env     # then edit it (see section 3)
uvicorn app.main:app --app-dir backend --env-file .env --no-access-log
```

Open http://localhost:8000 (commuters), `/control`, `/police`. Load demo data in a second terminal:

```bash
python simulator/demo.py all --auto          # flood, accident (waiting for police), planned works
python simulator/alerts_demo.py --device-key YOUR_DEVICE_KEY --user officer --password YOUR_PASSWORD \
  --police-user unit12 --police-password YOUR_POLICE_PASSWORD --auto     # the full alerts story
python -m pytest -q tests                    # 147 tests
```

**Walk the whole flow by hand:** `/control` sign in, open "Possible accident", tap **Send** on a unit. In another tab or on a
phone open `/police`, sign in as that unit, tap Accept, "I'm on my way", "I've arrived", choose **Blocked**, **Send to control
room**. Back in `/control` tap **Confirm: Blocked and warn drivers**. Open `/` to see it as a commuter.

## 3. Settings (`.env` locally, Environment on Render)

| Variable | Meaning |
|---|---|
| `ROADPULSE_SECRET` | long random value, signs login tokens (required) |
| `OFFICER_USERS` | control room logins, `name:password,name2:password2` |
| `POLICE_USERS` | **new**: traffic police logins, `unit12:password,unit7:password` |
| `DEVICE_KEYS` | key sent by sensors and simulators as `X-Device-Key` |
| `TRUST_PROXY=1` | needed on Render |
| `REQUIRE_FIELD_CONFIRMATION` | `1` (default) needs a police check before the control room can publish a citizen report; `0` turns it off |
| `TELEGRAM_BOT_TOKEN` | only for Telegram alerts; the **server** needs it too, not just the bot |

Never commit `.env`. Use strong passwords on any public deployment. If a login variable is missing, the server generates a
random one at start-up and prints it in the log.

## 4. Deploy on Render

Same steps as the earlier handover (Docker, Singapore, Free, health check `/api/health`), plus add `POLICE_USERS`.
Render free **sleeps after ~15 minutes idle and forgets data on restart**: open the site 5 to 10 minutes before presenting
and re-run `simulator/demo.py all --auto --url https://YOUR-URL --device-key ... --user ... --password ...`.
A public-repo deploy does not redeploy on push: use **Manual Deploy**. Render gives you HTTPS, which phone GPS needs (below).

## 5. Still to do (nobody has done these)

- [ ] **Real phones.** Everything was tested in a resized desktop browser only. On the Render URL (HTTPS) check, on one
  Android and one iPhone: the three apps load and the bottom sheet drags; "Use my location" works; `/police` shares location,
  shows the distance, buzzes on a new call, keeps the screen on; turn on airplane mode in `/police`, tap a status, turn it
  off and confirm it sent by itself; spoken alerts on `/commute` after tapping "Spoken alerts"; satellite view loads.
  Phone GPS needs **HTTPS or localhost**, so testing over `http://192.168...` will not get a location.
- [ ] **Telegram bot** with a real token (`python bot/telegram_bot.py`; set the same token on the server). Try live location.
- [ ] **Real Docker build locally** (optional; GitHub CI already built and smoke-tested the image on this commit):
  `docker build -t roadpulse:local .`
- [ ] **Real passwords** on any public deployment, and rotate the Telegram token after the event.
- [ ] Deck placeholders, backup video and two rehearsals (see the earlier handover, section 5). Update the deck to mention
  the police step; the demo script `docs/DEMO.md` still uses `/classic`.
- [ ] Optional: survey real flood spots into `data/hotspots.json`; set real board sites in `data/boards.json`
  (example in `docs/boards.example.json`); have a native Telugu speaker check bulletin wording.

## 6. Say this honestly when presenting

- Fleet speeds, water depth, the sensor and the roadside boards are **simulated** (labelled "demo" in the apps). The road map
  and routing are real OpenStreetMap data for Dwaraka Nagar.
- There is no camera or YOLO detection, no trained prediction model and no traffic-signal control (signal output is advice).
- "Normal speed for this time" is learned only after enough traffic; until then it is 60% of the speed limit (an assumption).
- Only people who use the apps, the bot or a board can be warned. A phone page only knows your location while it is open.
- Per-vehicle water-depth limits are assumed demo values, not safety guidance.
- Satellite imagery comes from Esri (needs internet; follows Esri's terms of use).

## 7. Where things are

| What | Where |
|---|---|
| Shared look and map code | `backend/static/rp.css`, `rp.js` |
| The apps | `index.html` + `commuter.js`, `control.html` + `control.js`, `police.html` + `police.js`, `commute.html` + `commute.js`, `board.html`, `classic.html` + `app.js` |
| Who is warned and when | `backend/app/alerts.py` |
| Police validation | `backend/app/field.py`, endpoints in `main.py` (`/api/incidents/{id}/assign`, `/api/police/*`, `/api/units`) |
| Speed rule | `backend/app/probe_rules.py` |
| Design notes and limits | `docs/ALERTS.md`, `docs/PLAN.md` |
| Tests | `tests/` (new: `test_alerts.py`, `test_field.py`, `test_bot.py`; `test_frontend_contract.py` guards the pages) |

## 8. Gotchas

- The public incident feed hides police and warning state on purpose (tests guard it). Keep it that way.
- Probes are **not** a trusted source. Do not add them to `TRUSTED_SOURCES`.
- `tests/conftest.py` switches the police rule off for older tests; `tests/test_field.py` switches it back on.
- The Dockerfile copies only `backend` and `data`; anything new the server needs must live there.
- The old UI moved to `/classic`; the `simulator/demo.py story` script and `docs/DEMO.md` refer to it.
