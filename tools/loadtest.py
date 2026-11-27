"""Targeted load test of the critical workflows (sensor ingest, reports, routing, incident list).

  python tools/loadtest.py --url http://localhost:8000 --concurrency 20 --seconds 20

Run the server with relaxed rate limits for this test, e.g.
  REPORT_RATE_PER_MIN=100000 ROUTE_RATE_PER_MIN=100000 DEVICE_RATE_PER_MIN=100000
Results are for the machine you run on; quote them with the machine spec.
"""
from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import time

import httpx


async def worker(c: httpx.AsyncClient, kind: str, stop: float, lat: float, lon: float, out: dict, key: str):
    lat_s = out.setdefault(kind, {"lat": [], "err": 0})
    while time.time() < stop:
        t0 = time.perf_counter()
        try:
            if kind == "route":
                r = await c.post("/api/routes", json={
                    "from_lat": lat - 0.005 + random.random() * 0.002, "from_lon": lon - 0.005 + random.random() * 0.01,
                    "to_lat": lat + 0.003 + random.random() * 0.002, "to_lon": lon - 0.005 + random.random() * 0.01,
                    "vehicle": random.choice(["two_wheeler", "car", "bus"])})
            elif kind == "sensor":
                r = await c.post("/api/sensors/readings", json={"sensor_id": "sensor-02", "depth_cm": random.random() * 5},
                                 headers={"X-Device-Key": key})
            elif kind == "report":
                r = await c.post("/api/reports", json={"type": "blockage", "lat": lat + random.random() * 0.01,
                                                       "lon": lon + random.random() * 0.01,
                                                       "reporter_id": f"load-{random.randint(1, 10**6)}"})
            else:
                r = await c.get("/api/incidents")
            if r.status_code >= 400:
                lat_s["err"] += 1
        except httpx.HTTPError:
            lat_s["err"] += 1
        lat_s["lat"].append((time.perf_counter() - t0) * 1000)


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--concurrency", type=int, default=20)
    p.add_argument("--seconds", type=int, default=20)
    p.add_argument("--device-key", default="dev-sensor-key")
    a = p.parse_args()
    async with httpx.AsyncClient(base_url=a.url, timeout=30) as c:
        lat, lon = (await c.get("/api/health")).json()["center"]
        stop = time.time() + a.seconds
        mix = ["route"] * 8 + ["sensor"] * 6 + ["report"] * 2 + ["list"] * 4
        out: dict = {}
        await asyncio.gather(*[worker(c, mix[i % len(mix)], stop, lat, lon, out, a.device_key)
                               for i in range(a.concurrency)])
    print(f"{a.concurrency} concurrent clients for {a.seconds}s against {a.url}")
    print(f"{'workflow':<8} {'requests':>9} {'req/s':>7} {'p50 ms':>8} {'p95 ms':>8} {'errors':>7}")
    for k, v in out.items():
        l = sorted(v["lat"])
        if not l:
            continue
        p95 = l[int(0.95 * (len(l) - 1))]
        print(f"{k:<8} {len(l):>9} {len(l) / a.seconds:>7.1f} {statistics.median(l):>8.0f} {p95:>8.0f} {v['err']:>7}")


if __name__ == "__main__":
    asyncio.run(main())
