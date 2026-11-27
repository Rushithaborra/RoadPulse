"""Alert-delivery story for RoadPulse: an accident in the middle of a commute, and who hears about it, when, and how.

Run the server first, then:
    python simulator/alerts_demo.py --url http://localhost:8000 --device-key KEY --user officer --password PASS [--auto]

Everything it sends is marked simulated. It uses only the public API, like a real phone, sensor and officer would.
Steps: two commuters sign up -> slow traffic raises a SUSPICION only -> a citizen report is still not enough ->
the officer verifies and closes the road -> the pre-trip alert goes out, the driver on the road is warned before the
turn-off, a board shows what to avoid -> the officer sends police and an ambulance -> the road reopens and everyone
who was warned hears it.
"""
from __future__ import annotations

import argparse
import math
import random
import time

import httpx


def hav(a, b):
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(x))


def walk(coords, step):
    """Points every `step` metres along a polyline."""
    out, carry = [coords[0]], 0.0
    for p, q in zip(coords, coords[1:]):
        d = hav(p, q)
        pos = step - carry
        while pos <= d:
            f = pos / d
            out.append((p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f))
            pos += step
        carry = (carry + d) % step
    return out


class Story:
    def __init__(self, a):
        self.a = a
        self.c = httpx.Client(base_url=a.url, timeout=30)
        self.dev = {"X-Device-Key": a.device_key}
        r = self.c.post("/api/auth/login", json={"username": a.user, "password": a.password})
        r.raise_for_status()
        self.off = {"Authorization": f"Bearer {r.json()['token']}"}

    def step(self, msg):
        print(f"\n=== {msg}")
        if not self.a.auto:
            input("    press Enter to continue… ")

    # ---- finding a good trip on whatever map the server loaded
    def pick_trip(self):
        """A trip whose usual route has a diversion, with an incident spot that is (a) well into the trip, (b) 150-400 m
        from a board so the board can show it, and (c) mid-block: the fleet check confirms that slow samples there are
        actually counted (samples near a junction are ignored on purpose)."""
        net = self.c.get("/api/network").json()
        boards = self.c.get("/api/boards").json()["boards"]
        pts = [tuple(s["coords"][0]) for s in net["segments"]]
        rnd = random.Random(11)
        for _ in range(600):
            a, b = rnd.sample(pts, 2)
            if not 1100 < hav(a, b) < 1900:
                continue
            r = self.c.post("/api/routes", json={"from_lat": a[0], "from_lon": a[1], "to_lat": b[0], "to_lon": b[1],
                                                 "vehicle": "car", "alternatives": 2})
            if r.status_code != 200 or len(r.json()["routes"]) < 2:
                continue   # a diversion must exist, or the story has nothing to show
            coords = r.json()["usual_route"]
            dense = walk(coords, 20)
            for k in range(int(len(dense) * 0.35), int(len(dense) * 0.7)):
                p = dense[k]
                nearest = min(hav(p, (bd["lat"], bd["lon"])) for bd in boards)
                if 150 <= nearest <= 400 and self.counts_as_midblock(p):
                    return a, b, coords, p
        raise SystemExit("could not find a trip with a diversion and a mid-block spot near a board on this map")

    def counts_as_midblock(self, p):
        """One normal-speed sample: the server reports how many vehicles it counted there (0 = junction zone)."""
        r = self.c.post("/api/probes", json={"vehicle_id": "spot-check", "lat": p[0], "lon": p[1], "speed_kph": 30,
                                             "simulated": True}, headers=self.dev)
        return r.status_code == 200 and (r.json().get("vehicles") or 0) >= 1

    def signup(self, **kw):
        body = {"consent_location": True, **kw}
        r = self.c.post("/api/subscribers", json=body)
        r.raise_for_status()
        d = r.json()
        return d["id"], {"X-Subscriber-Token": d["token"]}

    def inbox(self, sid, h):
        return self.c.get(f"/api/subscribers/{sid}/alerts", headers=h).json()["alerts"]

    def show(self, name, sid, h, seen):
        for al in self.inbox(sid, h):
            if al["id"] not in seen:
                seen.add(al["id"])
                print(f"    [{name}] {al['kind']}: {al['text']}" + ("  (spoken aloud)" if al.get("speak") else ""))

    def run(self):
        c = self.c
        A, B, route, spot = self.pick_trip()
        print(f"Trip: {hav(A, B):.0f} m as the crow flies. Incident spot: {spot[0]:.5f}, {spot[1]:.5f} (mid-block)")

        self.step("1. Two commuters sign up (consent to location). Ravi has a saved commute leaving in 10 minutes; "
                  "Lakshmi is hands-free (voice) and sets off now.")
        rav, hr = self.signup(name="Ravi (demo)", vehicle="car", home_lat=A[0], home_lon=A[1], work_lat=B[0],
                              work_lon=B[1])
        c.post(f"/api/subscribers/{rav}/trip", json={"dest": "work", "depart_in_min": 10}, headers=hr)
        lak, hl = self.signup(name="Lakshmi (demo)", vehicle="car", voice=True, home_lat=A[0], home_lon=A[1],
                              work_lat=B[0], work_lon=B[1])
        c.post(f"/api/subscribers/{lak}/trip", json={"dest": "work"}, headers=hl)
        seen_r, seen_l = set(), set()
        pts = walk(route, 40)
        i0 = 0
        c.post(f"/api/subscribers/{lak}/position", json={"lat": pts[0][0], "lon": pts[0][1], "simulated": True}, headers=hl)

        self.step("2. Slow traffic on the road, many vehicles, for 4 minutes. This is only a SUSPICION for officers: "
                  "one slow driver, a red light or a short dip would not count.")
        for ts in (time.time() - 240, time.time()):
            for i in range(10):
                c.post("/api/probes", json={"vehicle_id": f"fleet-{int(ts)}-{i}", "lat": spot[0], "lon": spot[1],
                                            "speed_kph": 3, "ts": ts, "simulated": True}, headers=self.dev)
        inc = c.get("/api/incidents?scope=all", headers=self.off).json()["incidents"]
        print("    officer sees:", [(i["title"], i["status"], f"published={i['published']}") for i in inc])
        print("    public sees:", c.get("/api/incidents").json()["incidents"], "(nothing: speed alone never reaches commuters)")

        self.step("3. A citizen reports an accident at the same spot. Confidence rises but is still not enough to publish.")
        r = c.post("/api/reports", json={"type": "accident", "lat": spot[0], "lon": spot[1], "reporter_id": "demo-citizen",
                                         "simulated": True}).json()
        iid = r["incident_id"]
        print(f"    report → status {r['status']}, confidence {r['confidence']}, public incidents: "
              f"{len(c.get('/api/incidents').json()['incidents'])}")

        self.step("4. The control room verifies it and decides the road is CLOSED (a human decision), then sends help.")
        c.post(f"/api/incidents/{iid}/verify", json={"severity": "Closed"}, headers=self.off).raise_for_status()
        for unit in ("police", "ambulance", "tow"):
            c.post(f"/api/incidents/{iid}/dispatch", json={"unit": unit}, headers=self.off).raise_for_status()
        print("    verified, severity Closed; police, ambulance and tow recorded as dispatched")
        self.show("Ravi, still at home", rav, hr, seen_r)

        self.step("5. Lakshmi drives her usual route; positions arrive every 40 m. She is warned before the turn-off.")
        self.show("Lakshmi", lak, hl, seen_l)          # the alert may already have been sent when it was verified
        if not seen_l:
            for k, p in enumerate(pts):
                c.post(f"/api/subscribers/{lak}/position", json={"lat": p[0], "lon": p[1], "simulated": True}, headers=hl)
                self.show("Lakshmi", lak, hl, seen_l)
                if seen_l:
                    i0 = k
                    break
                time.sleep(0.05)
            else:
                print("    (no alert: the road was not on her route)")
        got = [a for a in self.inbox(lak, hl) if a["kind"] in ("in_trip", "uturn")]
        if got:
            print(f"    time to the turn-off when warned: {got[0].get('lead_s')} s"
                  f"{' (late warning)' if got[0].get('late') else ''}; she had driven {i0 * 40} m of the route")

        self.step("6. People with no app: the roadside boards near the incident.")
        for b in c.get("/api/boards").json()["boards"]:
            if b["lines"]:
                print(f"    BOARD {b['id']} ({b['name']}):")
                for ln in b["lines"]:
                    print(f"      AVOID {ln['road']}: {ln['title']} {ln['metres']} m ahead")
                print(f"      USE: {', '.join(b['alternatives'])}")
        print("    open /board to see them as displays")

        self.step("7. The road reopens. Everyone who was warned hears it.")
        c.post(f"/api/incidents/{iid}/clear", json={"reason": "demo: cleared"}, headers=self.off).raise_for_status()
        self.show("Ravi", rav, hr, seen_r)
        self.show("Lakshmi", lak, hl, seen_l)

        self.step("8. Privacy: each commuter can delete their data.")
        for sid, h in ((rav, hr), (lak, hl)):
            print("    delete", sid, c.delete(f"/api/subscribers/{sid}", headers=h).json())
        print("\nDone. Everything above was simulated.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--device-key", required=True)
    p.add_argument("--user", default="officer")
    p.add_argument("--password", required=True)
    p.add_argument("--auto", action="store_true", help="do not pause between steps")
    Story(p.parse_args()).run()


if __name__ == "__main__":
    main()
