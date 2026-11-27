"""Load test of RoadPulse's critical workflows: normal load -> sudden burst -> recovery, plus a live-stream (SSE) check.

  python tools/loadtest.py --url http://localhost:8000 --device-key KEY --password PASS

Start the server with rate limits raised for this test, otherwise most requests are (correctly) answered 429:
  REPORT_RATE_PER_MIN=1000000 ROUTE_RATE_PER_MIN=1000000 DEVICE_RATE_PER_MIN=1000000

Prints p50 / p95 / p99 latency and error rate per phase and workflow. Numbers are only valid for the machine and
map you ran on; do not quote them as targets met unless this output shows it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import time

import httpx

# interleaved so every phase (even with few clients) exercises all four workflows; read-heavy like rain-time traffic
MIX = ["route", "sensor", "list", "route", "sensor", "report", "route", "list", "sensor", "route"]


def pct(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return float("nan")
    k = max(0, min(len(sorted_vals) - 1, math.ceil(p / 100 * len(sorted_vals)) - 1))
    return sorted_vals[k]


async def one(c: httpx.AsyncClient, kind: str, lat: float, lon: float, key: str) -> int:
    if kind == "route":
        r = await c.post("/api/routes", json={
            "from_lat": lat - 0.004 + random.random() * 0.001, "from_lon": lon - 0.004 + random.random() * 0.008,
            "to_lat": lat + 0.003 + random.random() * 0.001, "to_lon": lon - 0.004 + random.random() * 0.008,
            "vehicle": random.choice(["two_wheeler", "car", "bus"])})
    elif kind == "sensor":
        r = await c.post("/api/sensors/readings", json={"sensor_id": "sensor-02", "depth_cm": random.random() * 5},
                         headers={"X-Device-Key": key})
    elif kind == "report":
        r = await c.post("/api/reports", json={"type": "blockage", "lat": lat + random.uniform(-0.003, 0.003),
                                               "lon": lon + random.uniform(-0.003, 0.003),
                                               "reporter_id": f"load{random.randint(1, 10**6)}"})
    else:
        r = await c.get("/api/incidents")
    return r.status_code


async def phase(c, name: str, concurrency: int, seconds: float, lat, lon, key) -> dict:
    stop = time.time() + seconds
    res: dict[str, dict] = {}

    async def worker(i):
        kind = MIX[i % len(MIX)]
        s = res.setdefault(kind, {"lat": [], "err": 0, "limited": 0})
        while time.time() < stop:
            t0 = time.perf_counter()
            try:
                code = await one(c, kind, lat, lon, key)
                if code == 429:
                    s["limited"] += 1
                elif code >= 400:
                    s["err"] += 1
            except httpx.HTTPError:
                s["err"] += 1
            s["lat"].append((time.perf_counter() - t0) * 1000)

    await asyncio.gather(*[worker(i) for i in range(concurrency)])
    return {"name": name, "concurrency": concurrency, "seconds": seconds, "results": res}


def report(ph: dict):
    print(f"\n[{ph['name']}] {ph['concurrency']} concurrent clients, {ph['seconds']:.0f} s")
    print(f"{'workflow':<8} {'reqs':>6} {'req/s':>7} {'p50':>6} {'p95':>6} {'p99':>6} {'err %':>6} {'429':>5}")
    for k, v in sorted(ph["results"].items()):
        lat = sorted(v["lat"])
        n = len(lat)
        if not n:
            continue
        print(f"{k:<8} {n:>6} {n / ph['seconds']:>7.1f} {pct(lat, 50):>6.0f} {pct(lat, 95):>6.0f} {pct(lat, 99):>6.0f} "
              f"{100 * v['err'] / n:>6.1f} {v['limited']:>5}")


async def sse_check(url: str, n_clients: int, key: str) -> None:
    """Open n SSE clients, trigger one change, measure how long each client takes to receive it."""
    got: list[float] = []
    ready = asyncio.Event()
    connected = {"n": 0}
    trigger_at = {"t": 0.0}

    async def client():
        async with httpx.AsyncClient(base_url=url, timeout=None) as c:
            async with c.stream("GET", "/api/stream") as r:
                first = True
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    if first:  # initial snapshot
                        first = False
                        connected["n"] += 1
                        if connected["n"] == n_clients:
                            ready.set()
                        continue
                    if trigger_at["t"] and json.loads(line[5:]).get("version") is not None:
                        got.append((time.perf_counter() - trigger_at["t"]) * 1000)
                        return

    tasks = [asyncio.create_task(client()) for _ in range(n_clients)]
    try:
        await asyncio.wait_for(ready.wait(), 30)
    except asyncio.TimeoutError:
        print(f"\n[sse] only {connected['n']}/{n_clients} clients connected within 30 s")
    async with httpx.AsyncClient(base_url=url) as c:
        trigger_at["t"] = time.perf_counter()
        await c.post("/api/sensors/readings", json={"sensor_id": "sensor-02", "depth_cm": 1.0},
                     headers={"X-Device-Key": key})
    await asyncio.wait(tasks, timeout=15)
    for t in tasks:
        t.cancel()
    lat = sorted(got)
    print(f"\n[sse] {connected['n']}/{n_clients} clients connected; {len(lat)} received the update")
    if lat:
        print(f"      update delivery p50 {pct(lat, 50):.0f} ms, p95 {pct(lat, 95):.0f} ms, max {lat[-1]:.0f} ms "
              f"(server checks for changes once per second)")


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--device-key", required=True)
    p.add_argument("--normal", type=int, default=10, help="concurrent clients in the normal phase")
    p.add_argument("--burst", type=int, default=50, help="concurrent clients in the burst phase")
    p.add_argument("--seconds", type=float, default=15)
    p.add_argument("--sse-clients", type=int, default=50)
    a = p.parse_args()
    async with httpx.AsyncClient(base_url=a.url, timeout=30,
                                 limits=httpx.Limits(max_connections=a.burst + 10)) as c:
        h = (await c.get("/api/health")).json()
        lat, lon = h["center"]
        print(f"target {a.url} · map {h['map_mode']} ({h['graph']}, {h['edges']} edges)")
        for name, conc, secs in (("normal", a.normal, a.seconds), ("burst", a.burst, a.seconds / 2),
                                 ("recovery", a.normal, a.seconds)):
            report(await phase(c, name, conc, secs, lat, lon, a.device_key))
    await sse_check(a.url, a.sse_clients, a.device_key)


if __name__ == "__main__":
    asyncio.run(main())
