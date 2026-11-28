"""RoadPulse demo scenario. Sends SIMULATED readings through the same API the real devices use.

Usage:
  python simulator/demo.py all            # full story, pauses between steps (press Enter)
  python simulator/demo.py all --auto     # no pauses
  python simulator/demo.py flood | accident | drain | works
  python simulator/demo.py story          # the 10-step hackathon demo (see docs/DEMO.md), pauses between steps
  python simulator/demo.py accident-field # an accident through the two-stage police check (--police-user/--police-password)
  python simulator/demo.py story --auto --auto-officer   # unattended: the script also plays the officer
Options: --url http://localhost:8000  --device-key KEY  --user officer --password PASS
         --no-sensor   (skip simulated depth when the real ESP32 is connected)
         --auto-officer  (story only: approve and resolve through the officer API instead of the dashboard)
Defaults for the key and password are read from the repo's .env (DEVICE_KEYS, OFFICER_USERS).
"""
from __future__ import annotations

import argparse
import math
import time

import os

import httpx


def read_env(path=os.path.join(os.path.dirname(__file__), "..", ".env")) -> dict:
    env = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return {**env, **{k: v for k, v in os.environ.items() if k in ("DEVICE_KEYS", "OFFICER_USERS")}}


def offset(lat, lon, east_m, north_m):
    return lat + north_m / 111_320.0, lon + east_m / (111_320.0 * math.cos(math.radians(lat)))


class Demo:
    def __init__(self, a):
        self.a = a
        self.c = httpx.Client(base_url=a.url, timeout=30)
        self.dev = {"X-Device-Key": a.device_key}
        self.tok = None
        h = self.c.get("/api/hotspots").json()["hotspots"]
        self.hs = next(x for x in h if x["sensor_id"] == "sensor-01")
        health = self.c.get("/api/health").json()
        self.center = health["center"]
        print(f"map: {health['map_mode']} ({health['graph']}), hotspots: {health['hotspots_source']}")
        self.segments = self.c.get("/api/network").json()["segments"]

    def on_road(self, east_m, north_m):
        """Midpoint of the road segment nearest to an offset from the centre (works on synthetic and real maps)."""
        lat, lon = offset(*self.center, east_m, north_m)

        def mid(seg):
            (a, b), (c, d) = seg["coords"]
            return (a + c) / 2, (b + d) / 2
        return min((mid(sg) for sg in self.segments), key=lambda m: (m[0] - lat) ** 2 + (m[1] - lon) ** 2)

    def officer(self):
        if not self.tok:
            r = self.c.post("/api/auth/login", json={"username": self.a.user, "password": self.a.password})
            r.raise_for_status()
            self.tok = {"Authorization": f"Bearer {r.json()['token']}"}
        return self.tok

    def pause(self, msg):
        print(f"\n=== {msg}")
        if not self.a.auto:
            input("    press Enter to continue… ")

    def rain(self, mm):
        self.c.post("/api/weather/override", json={"rain_mm_per_hr": mm}, headers=self.officer()).raise_for_status()
        print(f"    rain set to {mm} mm/h (manual override; label as simulated in the demo)")

    def depth(self, cm):
        r = self.c.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": cm, "simulated": True}, headers=self.dev)
        r.raise_for_status()
        print(f"    simulated sensor-01 depth {cm} cm → incident {r.json().get('incident')}")

    def probes(self, lat, lon, kph, n=10, tag="veh", sustained=False):
        """Fleet speed samples. The detection rule needs MANY distinct vehicles (8+), and a slowdown must last longer
        than a signal cycle (3 min), so a slow batch is sent twice: once dated 4 minutes ago, once now."""
        batches = [time.time() - 240, time.time()] if sustained else [None]
        for ts in batches:
            for i in range(n):
                body = {"vehicle_id": f"{tag}-{i}", "lat": lat, "lon": lon, "speed_kph": kph, "simulated": True}
                if ts:
                    body["ts"] = ts
                self.c.post("/api/probes", json=body, headers=self.dev)

    def flood(self):
        self.pause("Step 1: heavy rain forecast for the area")
        self.rain(40)
        if not self.a.no_sensor:
            self.pause(f"Step 2: water rises at {self.hs['name']} (simulated sensor)")
            for cm in (4, 8, 12, 18, 25, 31, 35):
                self.depth(cm)
                time.sleep(self.a.step)
        else:
            self.pause("Step 2: raise the water at the real sensor now")
        self.pause("Step 3: a commuter reports the flood from the bot")
        r = self.c.post("/api/reports", json={"type": "flood", "lat": self.hs["lat"], "lon": self.hs["lon"],
                                              "reporter_id": "tg-demo-1", "note": "అండర్‌పాస్‌లో నీరు నిలిచింది",
                                              "simulated": True})
        print(f"    report → {r.json()}")

    def accident(self):
        self.pause("Step 4: an accident elsewhere (simulated speed drop + report)")
        lat, lon = self.on_road(-150, -75)  # a side street west of the centre
        self.probes(lat, lon, 3, tag="bus", sustained=True)
        r = self.c.post("/api/reports", json={"type": "accident", "lat": lat, "lon": lon, "reporter_id": "tg-demo-2",
                                              "simulated": True})
        print(f"    accident report → {r.json()}")
        print("    (sustained slow traffic + one public report is not enough to publish: an officer must verify it)")

    def works(self):
        self.pause("Planned works: pipeline digging on a side road for 4 hours")
        lat, lon = self.on_road(300, 225)
        now = time.time()
        r = self.c.post("/api/planned-works", headers=self.officer(), json={
            "title": "Pipeline digging", "lat": lat, "lon": lon, "starts": now, "ends": now + 4 * 3600,
            "full_closure": True, "agency": "Municipality", "simulated": True})
        print(f"    planned works → {r.status_code}")

    def drain(self):
        self.pause("Step 6: rain stops and water drains")
        self.rain(0)
        if not self.a.no_sensor:
            for cm in (28, 20, 12, 6, 3):
                self.depth(cm)
                time.sleep(self.a.step)
        self.probes(self.hs["lat"], self.hs["lon"], 38, tag="car")
        lat, lon = self.on_road(-150, -75)
        self.probes(lat, lon, 24, tag="bus")
        print("    incidents will auto-clear after the clear window (CLEAR_WINDOW_S, default 120 s)")

    def story(self):
        """The hackathon demo in the order an audience follows it. Every number printed is read back from the API."""
        a, hs = self.a, self.hs
        ends = self.road_ends()
        self.pause("1. A commuter opens RoadPulse (browser, Commuter tab). Choose Two-wheeler and click A and B on either "
                   f"side of the hotspot circle on {hs['name'].split(' · ')[-1]}")
        self.show_route("before any disruption", ends)
        self.show_public()

        self.pause("2. A road disruption is reported: a citizen reports waterlogging here (simulated report)")
        r = self.c.post("/api/reports", json={"type": "flood", "lat": hs["lat"], "lon": hs["lon"], "reporter_id": "story-citizen",
                                              "note": "water on the road", "simulated": True}).json()
        print(f"    report → status {r['status']}, confidence {r['confidence']} (a public report alone is never verified)")
        self.show_public("commuters still see nothing: the report is waiting for an officer or trusted evidence")

        self.pause("3. Sensor evidence is received (SIMULATED water-depth sensor posting to the real sensor endpoint)")
        self.rain(40)
        if a.no_sensor:
            print("    (--no-sensor: raise the water at the real sensor now)")
        else:
            for cm in (4, 8, 12, 18, 25, 31, 35):
                self.depth(cm)
                time.sleep(a.step)

        self.pause("4. The system assesses the incident (confidence fusion + severity rules)")
        inc = self.flood_incident()
        if inc:
            print(f"    {inc['title']}\n    status {inc['status']} · severity {inc['severity']} · confidence {inc['confidence']} · "
                  f"{round(inc['depth_cm'])} cm · avoid: {', '.join(inc['affected_vehicles'])}")
            print("    the keyed sensor is a trusted source, so with the report it passes the 0.70 bar automatically")

        self.pause("5. The authority reviews the evidence and approves the diversion (dashboard: Evidence, then Approve diversion)")
        if a.auto_officer and inc:
            r = self.c.post(f"/api/incidents/{inc['id']}/respond", json={}, headers=self.officer())
            print(f"    officer approved the diversion → {r.json()['status']}")
        else:
            print("    do it in the Traffic authority tab (log in, open Evidence, press Approve diversion)")

        self.pause("6-8. The affected road is marked, the commuter gets an alternative route and the dashboard updates")
        self.show_route("with the verified flood", ends)
        self.show_public()

        self.pause("9. Recovery evidence is received: rain stops, the water drains, probe speeds return to normal")
        self.drain_data()

        self.pause("10. The incident is resolved and the road is available again")
        inc = self.flood_incident(include_cleared=True)
        if a.auto_officer and inc and inc["status"] != "Cleared":
            r = self.c.post(f"/api/incidents/{inc['id']}/clear", json={}, headers=self.officer())
            print(f"    officer resolved the incident → {r.json()['status']}")
        elif inc and inc["status"] != "Cleared":
            print("    press Resolve in the dashboard, or wait: it auto-clears after CLEAR_WINDOW_S of normal readings")
        time.sleep(1)
        self.show_route("after recovery", ends)
        self.show_public()
        print("\nEverything above that came from the simulator is marked SIMULATED in the UI.")


    def road_ends(self):
        """The two ends of the road segment the flood-watch point sits on (from the public network API)."""
        hs = self.hs
        seg = min(self.segments, key=lambda s: ((s["coords"][0][0] + s["coords"][1][0]) / 2 - hs["lat"]) ** 2
                  + ((s["coords"][0][1] + s["coords"][1][1]) / 2 - hs["lon"]) ** 2)
        return seg["coords"]


    def show_route(self, label, ends):
        out = []
        for vehicle in ("two_wheeler", "bus"):
            r = self.c.post("/api/routes", json={"from_lat": ends[0][0], "from_lon": ends[0][1], "to_lat": ends[1][0],
                                                 "to_lon": ends[1][1], "vehicle": vehicle})
            if r.status_code != 200:
                out.append(f"{vehicle}: {r.json().get('detail', r.status_code)}")
                continue
            j = r.json()
            best = j["routes"][0] if j["routes"] else None
            hit = "; affected by " + ", ".join(h["title"] for h in j["usual_route_hits"]) if j["usual_route_hits"] else ""
            out.append(f"{vehicle}: {best['length_m']} m / {best['time_s']} s{hit}" if best else f"{vehicle}: no route")
        print(f"    route {label} → " + " | ".join(out))


    def show_public(self, note=""):
        inc = self.c.get("/api/incidents?scope=public").json()["incidents"]
        print(f"    commuters see {len(inc)} verified disruption(s)" + (f": {note}" if note else "")
              + "".join(f"\n      - {i['title']} [{i['severity']}]" for i in inc))


    def flood_incident(self, include_cleared=False):
        inc = self.c.get(f"/api/incidents?scope=all&include_cleared={str(include_cleared).lower()}", headers=self.officer()).json()["incidents"]
        return next((i for i in inc if i["type"] == "flood" and i.get("hotspot_id") == self.hs["hotspot_id"]), None)


    def drain_data(self):
        self.rain(0)
        if not self.a.no_sensor:
            for cm in (28, 20, 12, 6, 3):
                self.depth(cm)
                time.sleep(self.a.step)
        self.probes(self.hs["lat"], self.hs["lon"], 38, tag="car")
        print("    recovery evidence sent (SIMULATED)")


def police_token(self):
    if not getattr(self, "_ptok", None):
        r = self.c.post("/api/auth/login", json={"username": self.a.police_user, "password": self.a.police_password})
        if r.status_code != 200:
            raise SystemExit(f"Police login failed ({r.status_code}). Set POLICE_USERS in .env or pass --police-user and --police-password.")
        self._ptok = {"Authorization": f"Bearer {r.json()['token']}"}
    return self._ptok


def accident_field(self):
    """An accident through the two-stage check. Nothing reaches drivers until police have confirmed on the scene AND
    the control room has confirmed in turn. Every status printed is read back from the API."""
    off, pol = self.officer(), self.police_token()
    lat, lon = self.on_road(-150, -75)

    def public():
        return self.c.get("/api/incidents?scope=public").json()["incidents"]

    def mine(iid):
        return next(i for i in self.c.get("/api/incidents?scope=all", headers=off).json()["incidents"] if i["id"] == iid)

    self.pause("1. A citizen reports an accident (SIMULATED report)")
    r = self.c.post("/api/reports", json={"type": "accident", "lat": lat, "lon": lon, "reporter_id": "field-demo", "note": "two bikes down", "simulated": True}).json()
    iid = r["incident_id"]
    print(f"    report → {r['status']} (confidence {r['confidence']}); commuters see {len(public())} incident(s)")

    self.pause("2. The control room tries to publish it straight away: refused until police have checked")
    v = self.c.post(f"/api/incidents/{iid}/verify", json={}, headers=off)
    print(f"    control room verify → HTTP {v.status_code}: {v.json().get('detail')}")
    print(f"    commuters still see {len(public())} incident(s)")

    self.pause(f"3. The police unit signs in and shares its location; the control room sends it ({self.a.police_user})")
    self.c.post("/api/police/position", json={"lat": lat + 0.0006, "lon": lon + 0.0004}, headers=pol)
    units = self.c.get(f"/api/incidents/{iid}/units", headers=off).json()["units"]
    near = next((u for u in units if u["unit"] == self.a.police_user), units[0])
    print(f"    nearest unit: {near['unit']}" + (f", {near['eta_min']} min away" if near.get("eta_min") is not None else ""))
    a = self.c.post(f"/api/incidents/{iid}/assign", json={"unit": near["unit"]}, headers=off)
    print(f"    sent → HTTP {a.status_code}")

    self.pause("4. The unit accepts, travels, arrives, and reports what it SEES")
    for st in ("accepted", "on_way", "arrived"):
        self.c.post(f"/api/police/calls/{iid}/status", json={"status": st}, headers=pol)
        print(f"    unit status: {st}")
    self.c.post(f"/api/police/calls/{iid}/report", json={"finding": "blocked", "need": ["tow"], "note": "two bikes down, lane blocked"}, headers=pol)
    inc = mine(iid)
    print(f"    police report sent → field state: {inc['field_state']}; still not public: {len(public())} incident(s)")

    self.pause("5. The control room confirms: only now are drivers warned")
    v = self.c.post(f"/api/incidents/{iid}/verify", json={}, headers=off)
    inc = mine(iid)
    print(f"    control room verify → HTTP {v.status_code}; status {inc['status']}, severity {inc['severity']}; commuters see {len(public())} incident(s)")

    self.pause("6. The unit reports the road is open; the control room clears it")
    self.c.post(f"/api/police/calls/{iid}/report", json={"finding": "open", "need": [], "note": "road open"}, headers=pol)
    print(f"    field state: {mine(iid)['field_state']}")
    c = self.c.post(f"/api/incidents/{iid}/clear", json={}, headers=off)
    print(f"    control room clear → HTTP {c.status_code}; commuters see {len(public())} incident(s)")
    print("\nEverything the simulator sent is marked SIMULATED in the apps.")


for _f in (police_token, accident_field):
    setattr(Demo, _f.__name__, _f)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("scenario", choices=["all", "flood", "accident", "drain", "works", "story", "accident-field"])
    p.add_argument("--auto-officer", action="store_true", help="story: act as the officer through the API")
    p.add_argument("--url", default="http://localhost:8000")
    env = read_env()
    users = [u.split(":", 1) for u in env.get("OFFICER_USERS", "").split(",") if ":" in u]
    p.add_argument("--device-key", default=env.get("DEVICE_KEYS", "").split(",")[0] or None)
    p.add_argument("--user", default=users[0][0].strip() if users else "officer")
    p.add_argument("--password", default=users[0][1].strip() if users else None)
    cops = [u.split(":", 1) for u in env.get("POLICE_USERS", "").split(",") if ":" in u]
    p.add_argument("--police-user", default=cops[0][0].strip() if cops else "unit1")
    p.add_argument("--police-password", default=cops[0][1].strip() if cops else None)
    p.add_argument("--auto", action="store_true")
    p.add_argument("--no-sensor", action="store_true")
    p.add_argument("--step", type=float, default=1.5, help="seconds between simulated readings")
    a = p.parse_args()
    if not a.device_key or not a.password:
        raise SystemExit("Set DEVICE_KEYS and OFFICER_USERS in .env (or pass --device-key and --password)")
    d = Demo(a)
    if a.scenario == "story":
        d.story()
        return
    if a.scenario == "accident-field":
        d.accident_field()
        return
    if a.scenario in ("all", "works"):
        d.works()
    if a.scenario in ("all", "flood"):
        d.flood()
    if a.scenario in ("all", "accident"):
        d.accident()
    if a.scenario == "all":
        d.pause("Step 5: in the dashboard, verify the accident and approve the flood diversion")
    if a.scenario in ("all", "drain"):
        d.drain()
    print("\nDone.")


if __name__ == "__main__":
    main()
