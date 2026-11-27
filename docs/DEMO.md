# RoadPulse demo script (about 6 minutes)

Pilot area: Dwaraka Nagar and the RTC Complex (Dwaraka Bus Station), Visakhapatnam, on real OpenStreetMap roads.
Everything the simulator sends is marked **SIMULATED** in the interface. Say so out loud.

## Before you start (once)

First time only, in the project folder (a fresh checkout or unzip has no virtual environment and no `.env`):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # needs the leading .\ ; if blocked: Set-ExecutionPolicy -Scope Process Bypass
pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

Every time, to start the server:

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --app-dir backend --env-file .env --no-access-log --timeout-graceful-shutdown 3
```

- Open http://localhost:8000 in **two** browser windows side by side: the left one stays on **Commuter**, the right
  one goes to **Traffic authority** and logs in (user and password from `OFFICER_USERS` in `.env`).
- Check the header of the left window: green "Real OpenStreetMap roads" and `live`. A red MAP WARNING means the real
  map did not load: stop and fix that first (`python tools/fetch_map.py`).
- In a second terminal, in the same folder and virtual environment: `python simulator/demo.py story`. The script
  stops before every step and waits for Enter, so you decide the pace. Fresh start for a repeat run: restart the
  server (the demo database is `data/roadpulse.db`; delete it for a clean slate).
- In the left window press **Jump to: Dwaraka Nagar**, choose **Two-wheeler**, and zoom in on the green circle
  *Demo hotspot A (simulated)* on 4 th Lane Dwaraka nagar. Open the **Map legend** once so the audience can read it.

## The ten steps

| # | Say / do | What the audience sees | Press Enter in the terminal |
|---|---|---|---|
| 1 | "A commuter opens RoadPulse." Click **A** west and **B** east of the circle on 4 th Lane. | Route follows the real road: **106 m, 15 s**. Live alerts: none. | Step 1 prints the same numbers from the API. |
| 2 | "A citizen reports waterlogging." (Script sends a simulated report.) | The left window shows **nothing new**: a public report is never published on its own. The right window shows it under **Pending review**. | Step 2 |
| 3 | "A water-depth sensor reports rising water." (Simulated sensor, 4 to 35 cm, about 10 seconds.) | Right window: confidence rises. Left window, **live while the depth climbs**: within a few seconds the sensor evidence (a keyed sensor is a trusted source) verifies the incident, a toast says *New verified alert*, and the alert and **W** marker appear, at first as a minor slowdown with the route unchanged. As the water passes the assumed two-wheeler limit the severity rises, the road gets a black dotted overlay ("blocked for the vehicle you picked") and the route **redraws around it by itself**. | Step 3 |
| 4 | "The system assesses it." In the right window open **Evidence**. | Status **Verified**, severity **Severe**, confidence **0.87**; the table lists the sensor and report signals, each with a SIMULATED badge. Explain: with the report, the sensor evidence passes the 0.70 confidence bar. | Step 4 |
| 5 | "The authority approves the diversion." Click **Approve diversion** (or let the script do it with `--auto-officer`). | Right window: status **Responding**. | Step 5 |
| 6 | "The affected road is marked." Point at the map. | Left window: the affected road segment in red (severity colour), the **W** marker, the legend entry for each. | Step 6-8 |
| 7 | "The commuter has an alternative route." | Left window: **A** west to **B** east now takes **299 m / 40 s** instead of 106 m (the opposite direction is 362 m); the panel says *usual route is affected, diversion 299 m versus 106 m*. Switch to **Bus**: it still goes straight through (106 m), because the assumed limit for a bus is higher. | |
| 8 | "The authority dashboard updates." | Right window: KPIs, incident list and Activity log have all moved. Optionally press **What-if**, **Signals**, **Bulletin**. | |
| 9 | "Recovery evidence arrives." (Rain stops, depth falls to 3 cm, probe speeds normal.) | Severity falls to **Low**; the two-wheeler route goes straight again with the note *still the best option, expect slower traffic there*. | Step 9 |
| 10 | "The incident is resolved." Click **Resolve** (or wait for the auto-clear, `CLEAR_WINDOW_S`). | Left window: toast *Resolved ... the road is available again*, the red road disappears, the route returns to **106 m**. | Step 10 |

## Optional extras (one minute each)

- **Citizen report on screen:** in the left window open **Report a problem**, press **Choose on map**, click a road, type a
  note, **Send report**. It shows *waiting for officer review*. In the right window **Verify** it: the left window
  changes the report's status to *verified*.
- **Accident that needs an officer:** `python simulator/demo.py accident`: a speed drop plus one report scores 0.675,
  below the bar, so it waits in **Pending review** until someone presses **Verify**.
- **Connection loss:** stop the server for 10 seconds: both windows show a red banner (*live updates lost*) and the
  badge `offline · reconnecting…`; they recover on their own when it is back.
- **Evaluation:** right window, **Before / after evaluation**. Say it is a simplified traffic model, not SUMO, with
  the assumptions printed under the table.

## What to say about honesty

- The road map is real OpenStreetMap data; the water depth, rain and sensor in the demo are **simulated**.
- The hotspot is a **simulated point on a real road**, not a known flood location.
- The depth limits per vehicle are **assumed demo values**, not safety guidance.
- RoadPulse gives signal advice only; it controls no signal. There is no camera detection and no trained
  prediction model.
- The ESP32 sensor sketch is written but has not been tested on hardware.

## If something goes wrong

| Symptom | Fix |
|---|---|
| Red MAP WARNING banner | `data/area.graphml` is missing or broken: `python tools/fetch_map.py`, restart. |
| Map tiles are grey | No internet for the tiles: roads and routing still work; use a phone hotspot. |
| `live` turns to `stale` | The server stopped answering; restart it, the page reconnects by itself. |
| Officer login fails | Check `OFFICER_USERS` in `.env`; the server log prints generated ones if it was empty. |
| Route says no legal route | One-way streets: pick two points on the same two-way road, as in step 1. |
| Repeat the demo | Restart the server (delete `data/roadpulse.db` for a clean slate) and run `story` again. |
| Backup | Record the run once beforehand as a video. |
