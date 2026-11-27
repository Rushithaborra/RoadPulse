"""RoadPulse demo scenario. Sends SIMULATED readings through the same API the real devices use.

Usage:
  python simulator/demo.py all            # full story, pauses between steps (press Enter)
  python simulator/demo.py all --auto     # no pauses
  python simulator/demo.py flood | accident | drain | works
Options: --url http://localhost:8000  --device-key dev-sensor-key  --user officer --password roadpulse-demo
         --no-sensor   (skip simulated depth when the real ESP32 is connected)
"""
from __future__ import annotations

import argparse
import math
import time

import httpx


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
        self.center = self.c.get("/api/health").json()["center"]

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
        r = self.c.post("/api/sensors/readings", json={"sensor_id": "sensor-01", "depth_cm": cm}, headers=self.dev)
        r.raise_for_status()
        print(f"    simulated sensor-01 depth {cm} cm → incident {r.json().get('incident')}")

    def probes(self, lat, lon, kph, n=4, tag="veh"):
        for i in range(n):
            self.c.post("/api/probes", json={"vehicle_id": f"{tag}-{i}", "lat": lat, "lon": lon, "speed_kph": kph},
                        headers=self.dev)

    def flood(self):
        self.pause("Step 1: heavy rain forecast for the area")
        self.rain(40)
        if not self.a.no_sensor:
            self.pause("Step 2: water rises at the railway underpass (simulated sensor)")
            for cm in (4, 8, 12, 18, 25, 31, 35):
                self.depth(cm)
                time.sleep(self.a.step)
        else:
            self.pause("Step 2: raise the water at the real sensor now")
        self.pause("Step 3: a commuter reports the flood from the bot")
        r = self.c.post("/api/reports", json={"type": "flood", "lat": self.hs["lat"], "lon": self.hs["lon"],
                                              "reporter_id": "tg-demo-1", "note": "అండర్‌పాస్‌లో నీరు నిలిచింది"})
        print(f"    report → {r.json()}")

    def accident(self):
        self.pause("Step 4: an accident elsewhere (simulated speed drop + report)")
        lat, lon = offset(*self.center, -150, -75)  # side street west of the centre
        self.probes(lat, lon, 3, n=4, tag="bus")
        r = self.c.post("/api/reports", json={"type": "accident", "lat": lat, "lon": lon, "reporter_id": "tg-demo-2"})
        print(f"    accident report → {r.json()}")

    def works(self):
        self.pause("Planned works: pipeline digging on a side road for 4 hours")
        lat, lon = offset(*self.center, 300, 225)
        now = time.time()
        r = self.c.post("/api/planned-works", headers=self.officer(), json={
            "title": "Pipeline digging", "lat": lat, "lon": lon, "starts": now, "ends": now + 4 * 3600,
            "full_closure": True, "agency": "Municipality"})
        print(f"    planned works → {r.status_code}")

    def drain(self):
        self.pause("Step 6: rain stops and water drains")
        self.rain(0)
        if not self.a.no_sensor:
            for cm in (28, 20, 12, 6, 3):
                self.depth(cm)
                time.sleep(self.a.step)
        self.probes(self.hs["lat"], self.hs["lon"], 38, n=4, tag="car")
        lat, lon = offset(*self.center, -150, -75)
        self.probes(lat, lon, 24, n=4, tag="bus")
        print("    incidents will auto-clear after the clear window (CLEAR_WINDOW_S, default 120 s)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("scenario", choices=["all", "flood", "accident", "drain", "works"])
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--device-key", default="dev-sensor-key")
    p.add_argument("--user", default="officer")
    p.add_argument("--password", default="roadpulse-demo")
    p.add_argument("--auto", action="store_true")
    p.add_argument("--no-sensor", action="store_true")
    p.add_argument("--step", type=float, default=1.5, help="seconds between simulated readings")
    a = p.parse_args()
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
