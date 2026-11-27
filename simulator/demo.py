"""RoadPulse demo scenario. Sends SIMULATED readings through the same API the real devices use.

Usage:
  python simulator/demo.py all            # full story, pauses between steps (press Enter)
  python simulator/demo.py all --auto     # no pauses
  python simulator/demo.py flood | accident | drain | works
Options: --url http://localhost:8000  --device-key KEY  --user officer --password PASS
         --no-sensor   (skip simulated depth when the real ESP32 is connected)
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

    def probes(self, lat, lon, kph, n=4, tag="veh"):
        for i in range(n):
            self.c.post("/api/probes", json={"vehicle_id": f"{tag}-{i}", "lat": lat, "lon": lon, "speed_kph": kph,
                                                  "simulated": True},
                        headers=self.dev)

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
        self.probes(lat, lon, 3, n=4, tag="bus")
        r = self.c.post("/api/reports", json={"type": "accident", "lat": lat, "lon": lon, "reporter_id": "tg-demo-2",
                                              "simulated": True})
        print(f"    accident report → {r.json()}")
        print("    (a speed drop + one public report is not enough to auto-verify: an officer must verify it)")

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
        self.probes(self.hs["lat"], self.hs["lon"], 38, n=4, tag="car")
        lat, lon = self.on_road(-150, -75)
        self.probes(lat, lon, 24, n=4, tag="bus")
        print("    incidents will auto-clear after the clear window (CLEAR_WINDOW_S, default 120 s)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("scenario", choices=["all", "flood", "accident", "drain", "works"])
    p.add_argument("--url", default="http://localhost:8000")
    env = read_env()
    users = [u.split(":", 1) for u in env.get("OFFICER_USERS", "").split(",") if ":" in u]
    p.add_argument("--device-key", default=env.get("DEVICE_KEYS", "").split(",")[0] or None)
    p.add_argument("--user", default=users[0][0].strip() if users else "officer")
    p.add_argument("--password", default=users[0][1].strip() if users else None)
    p.add_argument("--auto", action="store_true")
    p.add_argument("--no-sensor", action="store_true")
    p.add_argument("--step", type=float, default=1.5, help="seconds between simulated readings")
    a = p.parse_args()
    if not a.device_key or not a.password:
        raise SystemExit("Set DEVICE_KEYS and OFFICER_USERS in .env (or pass --device-key and --password)")
    d = Demo(a)
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
