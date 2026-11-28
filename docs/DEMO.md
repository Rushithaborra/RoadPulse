# RoadPulse demo script (about 7 minutes)

Pilot area: Dwaraka Nagar and the RTC Complex (Dwaraka Bus Station), Visakhapatnam, on real OpenStreetMap roads.
Everything the simulator sends is marked **Simulated** in all three apps. Say so out loud.

## The three apps

| App | Address | Who | Sign in |
|---|---|---|---|
| Commuter map | http://localhost:8000 | Anyone: routes, live disruptions, report a problem | none |
| Control room | http://localhost:8000/control | Traffic control room: check 1 of 2, urgent, diversions, resolve | a name in `OFFICER_USERS` that does not start with `field` |
| Field police | http://localhost:8000/field | Police at the spot: check 2 of 2 (works on a phone) | a name in `OFFICER_USERS` starting with `field` |

With the example `.env`: control room `officer` / `change-me`, field police `field1` / `change-me-field`.
Change them before you share the server with anyone.

## How a citizen report is checked

```
citizen report ─▶ control room ─▶ field police at the spot ─▶ on the public map, routes change
                     │  └─ Urgent: publish now ─▶ on the map at once, field police still asked to confirm
                     └─ Reject
field police: "Not found" ─▶ report closed (an urgent one goes back to the control room instead)
```

A citizen report alone never publishes, and neither check alone publishes it. A keyed water sensor is trusted
evidence and publishes on its own (step 3 below), as in the proposal.

## Before you start

Open three windows side by side: the commuter map (left), the control room (middle, signed in) and the field app
(right; narrow the window or use your phone on the same Wi-Fi with `http://<laptop-ip>:8000/field` after starting
the server with `--host 0.0.0.0`). Check the commuter header: **OpenStreetMap roads** and a green **Live**. A red map
warning means the real map did not load: fix that first.

In VS Code: **Terminal > Run Task > RoadPulse: start server**, then **RoadPulse: demo - flood story (step by step)**.
The script waits for Enter before each step.

## Part 1: the flood (the ten-step story)

| # | Say / do | What the audience sees |
|---|---|---|
| 1 | "A commuter opens RoadPulse." Choose **Two-wheeler**; tap **A** on one end of 4 th Lane Dwaraka nagar beside the dashed circle (Demo hotspot A, simulated), **B** on the other end. | A route on the real road: **106 m**, about 15 s. "No confirmed disruption on your way." |
| 2 | "A citizen reports waterlogging." | Commuter map: **nothing changes**. Control room: it appears under **Needs your check**. |
| 3 | "A water-depth sensor reports rising water" (simulated, 4 to 35 cm). | The sensor is trusted evidence: the incident publishes on its own; the commuter's route **redraws by itself** and a toast says the route changed. The road turns red with a white dotted line: blocked for the vehicle you picked. |
| 4 | "The system assesses it." Click it in the control room. | Confidence **0.87**, severity **Severe**, 35 cm, blocked for two-wheeler, auto, car, ambulance. Evidence lists each signal, marked Simulated. |
| 5 | "The control room approves the diversion." **Approve diversion**. | Tag **Diversion approved**; commuter list says *Diversion in force*. |
| 6-7 | "The commuter gets a way round." | Commuter: **362 m** (or 299 m the other way) instead of 106 m, with the note naming the waterlogging. Switch to **Bus**: it still goes straight through, because the assumed bus limit is higher. |
| 8 | "The control room has decision support." **Compare diversions**, **Signal timing advice**, **Draft public bulletin**. | Model outputs, labelled as a simplified model and advice only. |
| 9 | "Recovery evidence arrives" (rain stops, depth falls to 3 cm). | Severity falls; the timeline shows the auto-clear countdown. |
| 10 | "The road is open again." **Resolve: road open again** (or wait for auto-clear). | Commuter: toast *Cleared ... available again*; the route returns to **106 m**. |

## Part 2: two checks on a citizen report (2 minutes)

1. Commuter map, **Report a problem**: choose **Road blocked**, **Choose on the map**, tap a road, write
   "Tree across the road", **Send report**. *Your reports* shows four steps with **Control room** highlighted.
2. Control room: the report appears under **Needs your check**. Open it, set **How bad is it?** to *Closed*, add a
   note, press **Send to field police**. Point out: it is still **Not public**; the commuter's tracker moves to
   **Field police**.
3. Field app: the check appears by itself. Open it: the citizen's words, the control room's note, a map and a
   **Directions** link. Choose **Fully closed**, press **Confirm: it is here**.
4. Commuter map: the road appears, routes change, and the tracker shows all four steps done.

**Urgent fast-track:** repeat with an accident and press **Urgent: publish now** in the control room. It is public at
once with a black and yellow edge, and still waits in the field app for confirmation. If the field officer presses
**Not found**, it goes back to the control room under *Needs your check* instead of disappearing.

Unattended version of part 2: **RoadPulse: demo - two checks on a citizen report** with `--auto --auto-officer`
(`python simulator/demo.py checks --auto --auto-officer`, add `--urgent` for the fast-track).

## What to say about honesty

- The road map is real OpenStreetMap data; the water depth, rain, sensor and reports in the demo are **simulated**.
- The hotspot is a **simulated point on a real road**, not a known flood location.
- The depth limits per vehicle are **assumed demo values**, not safety guidance.
- RoadPulse gives signal advice only; it controls no signal. There is no camera detection and no trained
  prediction model. The evaluation is a simplified traffic model, not SUMO and not field data.
- The ESP32 sensor sketch is written but has not been tested on hardware.

## If something goes wrong

| Symptom | Fix |
|---|---|
| Red map warning | `data/area.graphml` is missing or broken: `python tools/fetch_map.py`, restart. |
| Map background is plain grey | No internet for the map tiles: roads and routing still work (they come from the server). |
| Header says *Reconnecting* | The server stopped; restart it, the pages reconnect by themselves. |
| "This is a field police account" | You signed in to the wrong app: control room names must not start with `field`. |
| "Too many requests" at sign-in | Sign-in is limited to about 6 a minute per computer; wait a minute. |
| Route says no legal route | One-way streets: pick two points on the same two-way road, as in step 1. |
| Repeat the demo | Stop the server, delete `data/roadpulse.db`, start it again. |
| Backup | Record the run once beforehand as a video. |
