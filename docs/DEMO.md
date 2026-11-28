# Demo script (about 8 minutes)

Pilot area: Dwaraka Nagar and the RTC Complex (Dwaraka Bus Station), Visakhapatnam, on **real OpenStreetMap roads**.
Everything the simulator sends is flagged `simulated` and labelled **SIMULATED / demo** in every app. Say so out loud.

## Before you start (once, 10 minutes)

```powershell
cd <project folder>
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # the leading .\ is required; if blocked: Set-ExecutionPolicy -Scope Process Bypass
pip install -r requirements-dev.txt
Copy-Item .env.example .env           # then put real values for the passwords, keys and secret in .env
```

For a demo, in `.env` set `CLEAR_WINDOW_S=300` (so an incident does not clear itself before the officer does) and note the
logins: `OFFICER_USERS` (control room), `POLICE_USERS` (police) and `DEVICE_KEYS` (simulator).

Start the server (stop it with Ctrl+C; for a clean slate delete `data\roadpulse.db*` first):

```powershell
uvicorn app.main:app --app-dir backend --env-file .env --no-access-log --timeout-graceful-shutdown 3
```

Open six browser windows and arrange them side by side:

| Window | Address | Do first |
|---|---|---|
| 1 Commuter | `http://localhost:8000/` | nothing; green "All clear" card |
| 2 Control room | `/control` | sign in as an officer |
| 3 Police (a phone, or a second window) | `/police` | sign in as `unit1`; **Allow** location when asked |
| 4 Alerts | `/commute` | name "Ravi", vehicle two-wheeler, tap **Set home** then tap the road about 100 m **west** of the flood-watch spot, **Set work** about 100 m **east** of it (the spot is on *4 th Lane Dwaraka nagar*, 17.72798 N, 83.30584 E), tick consent, **Start getting alerts**, then **Set my departure** (10 min) |
| 5 Alerts B | `/commute` in a **private window** (it is a different person) | name "Asha", two-wheeler, home and work the same way, consent, **Start getting alerts**; do **not** set a departure |
| 6 Board | `/board` | open the board list; leave it for step 8 |

Second terminal, same folder, virtual environment active. Put the logins in variables once:

```powershell
$K = "<DEVICE_KEYS value>"; $P = "<OFFICER password>"; $U = "<POLICE password>"
```

Health check: `http://localhost:8000/api/health` must show `"map_mode":"osm"` and `"map_warning":null`, and window 1 shows the **Live** chip. A red map warning means the real map did not load: stop and fix that first.

## Part 1: the flood (about 5 minutes)

| # | Say and do | What the audience sees |
|---|---|---|
| 1 | "A commuter opens RoadPulse." In window 1 tap the top bar, type `4 th` in **From**, pick the first result, type `RTC` in **To**, pick it. | A route on the map and **Your way is clear**, about 2 min. |
| 2 | "A citizen reports waterlogging." Window 1: **Report**, **Water**, tap the road at the flood-watch spot, **Send report**. | A thank-you, and under **Your reports**: **Waiting for a check**. Nobody else is warned: a report alone never publishes. |
| 3 | "Simulated sensor readings arrive." Terminal: `python simulator/demo.py flood --auto --device-key $K --user officer --password $P` | About 10 seconds of lines such as `simulated sensor-01 depth 35 cm -> incident inc-...` and `report -> Verified`. |
| 4 | "The system assesses it: a trusted sensor plus the report is enough to publish." | Window 1: a toast (new road problem) and the card **Water on the road on 4 th Lane Dwaraka nagar, Very slow, demo**. **Your reports** turns to **Confirmed**. |
| 5 | "The control room reviews it." Window 2: open the card. | **Why we think so**: *Water sensor reads 35 cm*, *1 driver report says there is a problem here*, **How sure the system is: 87% . demo data**. |
| 6 | "The officer approves a diversion." Tap **Approve diversion**. | A toast and the green **Diversion approved** box. |
| 7 | "The commuter gets a new route." Window 1: **Start again**, tap the problem card, **See a way round**. | **A problem on your usual way ... Take the green route if you can: 1 min longer**, the usual way dashed red, the better way green. Switch to **Bus**: no faster way round, expect delays (a bus can still pass). |
| 8 | "People are warned, and a roadside board shows it." Window 5 (Asha): **Demo: simulate my drive**. Window 6: open the board that says WARNING. | Window 4 (Ravi, who planned a trip): a **pre trip** alert with the way round. Window 5 (Asha, on the road): an **in trip** or **turn back** alert, marked SIMULATED (each person is warned once per incident). Window 6: **AVOID ... Water on road ... m ahead**, `SIMULATED DISPLAY`. Window 2 shows **Warnings sent** (anonymous). |
| 9 | "Recovery observations arrive." Terminal: `python simulator/demo.py drain --auto --device-key $K --user officer --password $P` | Depths 28 down to 3 cm. Window 2: **Looks like it is recovering**. |
| 10 | "The officer resolves it." Window 2: **The road is clear again**, tap again to confirm. | Toast: *Road marked clear. Everyone who was warned is told.* |
| 11 | "Everyone gets the all-clear." | Windows 4 and 5: a green **clear** alert. Window 1: **All clear**, your original route again, **Your reports: Resolved**. Window 6: **ROAD CLEAR**. |

## Part 2: an accident and the two-stage police check (about 2 minutes)

| # | Say and do | What the audience sees |
|---|---|---|
| 12 | "A report is not enough: police must check it on the scene." Window 1: **Report**, **Accident**, tap a road about 500 m away, **Send report**. | **Waiting for a check**. Window 2: **Possible accident on ...**, no confirm button. |
| 13 | Window 2: open the card, tap **Send** next to `unit1`. | *unit1 is being sent to check.* Still nothing public. |
| 14 | Window 3: the call arrives. **Accept**, **I'm on my way**, **I've arrived**, **Blocked**, **Send to control room**. | Window 3: *Sent to the control room.* Window 2: **Police confirmed: road blocked**. |
| 15 | Window 2: **Confirm: Blocked and warn drivers**. | Only now: window 1 shows **Accident on ...**. |
| 16 | Window 3: **The road is open**. Window 2: **Clear road**, tap again. | Window 1: **All clear**. |

Backup for Part 2 with no clicking: `python simulator/demo.py accident-field --auto --device-key $K --user officer --password $P --police-user unit1 --police-password $U` prints each step, including the control room being refused with HTTP 409 until police have checked.

## Close (30 seconds)

"The map and the rules are real. The water depth, rain, sensors and boards are simulated and labelled. The vehicle depth limits are assumed values, not safety guidance. There is no camera detection, no trained model and no signal control. The sensor hardware sketch exists but was not part of this software-only delivery."

## Other demos

- Everything in Part 1 with no browser: `python simulator/demo.py story --auto --auto-officer --device-key $K --user officer --password $P`
- The alert story (suspicion only, two checks, pre-trip, in-trip, boards, all-clear, data deletion): `python simulator/alerts_demo.py --device-key $K --password $P --police-user unit1 --police-password $U --auto`
- The same flows run automatically in real Chrome: `python -m pytest -q tests/e2e` (see the README).

## If something goes wrong

| Symptom | Fix |
|---|---|
| Red map warning | `data\area.graphml` is missing or broken. Restore it from the repository (or `python tools/fetch_map.py`) and restart. |
| A step shows nothing | Wait 3 seconds (the apps refresh every 2.5 s). The header chip says **live**; **offline** means the server stopped. |
| You pressed things in the wrong order | Stop the server, delete `data\roadpulse.db*`, start it again (about 10 seconds) and begin at step 1. |
| The incident cleared itself | `CLEAR_WINDOW_S` ran out after step 9. Re-run step 3 and carry on. |
| Police window sees no call | Allow location in the browser, sign in as `unit1`, and check the control room shows the unit under the incident. |
| 401 or "session ended" | Sign in again; tokens last 8 hours and do not survive a changed `ROADPULSE_SECRET`. |
| Map tiles are grey | No internet for the background tiles. Roads, routing and every other function still work. |
| Total failure | Play the backup video, or run `python simulator/demo.py story --auto --auto-officer ...` and read the output aloud. |
