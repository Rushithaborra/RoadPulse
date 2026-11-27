"""Simplified traffic simulation for the before/after evaluation, what-if diversions and signal advice.

This is a static assignment model (BPR volume-delay), not SUMO. It is good enough to compare options on the
same network with the same trips; quote its numbers as "simplified simulation" results.
"""
from __future__ import annotations

import random

import networkx as nx

from .geo import bearing_deg, haversine_m
from .models import Incident
from .roadgraph import RoadGraph
from .routing import edge_time, incident_effects, weighted_graph

VEHICLE_MIX = [("two_wheeler", 0.45), ("auto", 0.15), ("car", 0.30), ("bus", 0.10)]
STUCK_PENALTY_S = 15 * 60  # ASSUMPTION: a vehicle that reaches a road it cannot pass loses 15 min turning back
ASSUMPTIONS = [
    "Each vehicle that reaches a road it cannot pass loses 15 minutes (assumed, not measured).",
    "Without RoadPulse, drivers take their usual fastest route and do not know about the incident.",
    "Trips are random origin-destination pairs crossing the incident area over one hour; vehicle mix "
    "45% two-wheelers, 15% autos, 30% cars, 10% buses (assumed).",
    "Congestion follows the BPR volume-delay curve with assumed road capacities.",
]


def make_trips(rg: RoadGraph, n: int, seed: int, near: tuple | None = None, radius_m: float = 700) -> list[tuple]:
    """Random origin-destination trips; if `near` is given, trips cross that area so the incident matters."""
    rnd = random.Random(seed)
    nodes = list(rg.g.nodes)
    trips = []
    while len(trips) < n:
        a, b = rnd.choice(nodes), rnd.choice(nodes)
        if a == b:
            continue
        (alat, alon), (blat, blon) = rg.node_latlon(a), rg.node_latlon(b)
        if haversine_m(alat, alon, blat, blon) < 400:
            continue
        if near:
            mlat, mlon = (alat + blat) / 2, (alon + blon) / 2
            if haversine_m(mlat, mlon, *near) > radius_m:
                continue
        r, acc = rnd.random(), 0.0
        veh = VEHICLE_MIX[-1][0]
        for v, p in VEHICLE_MIX:
            acc += p
            if r <= acc:
                veh = v
                break
        trips.append((a, b, veh))
    return trips


def _bpr(t0: float, vol: float, cap: float) -> float:
    return t0 * (1 + 0.15 * (vol / cap) ** 4)


def simulate(rg: RoadGraph, trips: list[tuple], truth: list[Incident], known: list[Incident] | None,
             congestion_aware: bool) -> dict:
    """Assign trips one by one. `known` = what drivers are told (None = nothing); `truth` = real conditions."""
    true_fx = incident_effects(rg, truth)
    known_fx = incident_effects(rg, known or [])
    vol: dict[tuple, float] = {}
    total_t, exposed, unsafe_entries = 0.0, 0, 0
    graphs: dict[str, nx.DiGraph] = {}
    for i, (a, b, veh) in enumerate(trips):
        if congestion_aware and i % 25 == 0:
            graphs.clear()  # refresh weights with current volumes every 25 trips
        if veh not in graphs:
            g = weighted_graph(rg, veh, known_fx)
            if congestion_aware:
                for u, v, d in g.edges(data=True):
                    ed = rg.g.edges[(u, v)]
                    d["w"] = _bpr(d["w"], vol.get((u, v), 0), ed["capacity_vph"])
            graphs[veh] = g
        try:
            path = nx.shortest_path(graphs[veh], a, b, weight="w")
        except nx.NetworkXNoPath:
            continue
        t, hit = 0.0, False
        for e in zip(path, path[1:]):
            vol[e] = vol.get(e, 0) + 1
            et = edge_time(rg, e, veh, true_fx)
            if et is None:
                hit = True
                break
            t += et
        if hit:
            exposed += 1
            unsafe_entries += 1
            t += STUCK_PENALTY_S
        total_t += t
    # add congestion delay from final volumes (same formula for every scenario)
    cong = 0.0
    for e, v in vol.items():
        d = rg.g.edges[e]
        cong += v * (_bpr(d["base_time"], v, d["capacity_vph"]) - d["base_time"])
    n = max(1, len(trips))
    return {
        "trips": len(trips),
        "avg_trip_min": round((total_t + cong) / n / 60, 2),
        "vehicles_reaching_impassable_road": exposed,
        "total_vehicle_hours": round((total_t + cong) / 3600, 1),
        "edge_volumes": vol,
    }


def before_after(rg: RoadGraph, incidents: list[Incident], n: int = 400, seed: int = 7) -> dict:
    near = (incidents[0].lat, incidents[0].lon) if incidents else None
    trips = make_trips(rg, n, seed, near)
    base = simulate(rg, trips, incidents, known=None, congestion_aware=False)
    rp = simulate(rg, trips, incidents, known=incidents, congestion_aware=True)
    for r in (base, rp):
        r.pop("edge_volumes")
    saved = base["total_vehicle_hours"] - rp["total_vehicle_hours"]
    return {
        "model": "simplified static assignment (BPR), not SUMO",
        "assumptions": ASSUMPTIONS,
        "incidents": [i.title for i in incidents],
        "without_roadpulse": base,
        "with_roadpulse": rp,
        "vehicle_hours_saved": round(saved, 1),
        "pct_time_saved": round(100 * saved / base["total_vehicle_hours"], 1) if base["total_vehicle_hours"] else 0,
    }


def what_if(rg: RoadGraph, inc: Incident, others: list[Incident], n: int = 400, seed: int = 7) -> dict:
    """Compare officer options for one incident on the same trips."""
    trips = make_trips(rg, n, seed, (inc.lat, inc.lon))
    closed = inc.model_copy(update={"severity": "Closed", "type": "blockage", "depth_cm": None})
    # order matters for ties: prefer keeping the road open to the vehicles that can still pass
    options = {
        "Vehicle-aware diversion (RoadPulse)": dict(truth=[inc] + others, known=[inc] + others),
        "Full closure, all vehicles": dict(truth=[closed] + others, known=[closed] + others),
        "No action (caution only)": dict(truth=[inc] + others, known=others),
    }
    results = {}
    for name, o in options.items():
        r = simulate(rg, trips, o["truth"], o["known"], congestion_aware=True)
        r.pop("edge_volumes")
        results[name] = r
    safe = {k: v for k, v in results.items() if v["vehicles_reaching_impassable_road"] == 0} or results
    best = min(safe, key=lambda k: safe[k]["avg_trip_min"])
    return {"incident": inc.title, "model": "simplified static assignment (BPR), not SUMO",
            "assumptions": ASSUMPTIONS, "options": results, "recommended": best}


def signal_advice(rg: RoadGraph, inc: Incident, others: list[Incident], n: int = 400, seed: int = 7,
                  top: int = 3) -> dict:
    """Webster cycle and green splits for the junctions that gain the most traffic from the detour."""
    trips = make_trips(rg, n, seed, (inc.lat, inc.lon))
    before = simulate(rg, trips, others, known=others, congestion_aware=True)["edge_volumes"]
    after = simulate(rg, trips, [inc] + others, known=[inc] + others, congestion_aware=True)["edge_volumes"]

    def node_in(vols, node):
        return sum(v for (a, b), v in vols.items() if b == node)

    junctions = [x for x in rg.g.nodes if rg.g.in_degree(x) >= 3
                 and haversine_m(*rg.node_latlon(x), inc.lat, inc.lon) <= 800]
    gains = sorted(junctions, key=lambda x: node_in(after, x) - node_in(before, x), reverse=True)[:top]
    out = []
    for x in gains:
        xlat, xlon = rg.node_latlon(x)
        phases = {"North-South": 0.0, "East-West": 0.0}
        for a in rg.g.predecessors(x):
            flow = after.get((a, x), 0)  # trips assigned over one hour = vehicles per hour
            lanes = rg.g.edges[(a, x)]["lanes"]
            brg = bearing_deg(*rg.node_latlon(a), xlat, xlon)
            ph = "North-South" if brg < 45 or brg >= 315 or 135 <= brg < 225 else "East-West"
            phases[ph] = max(phases[ph], flow / (1800 * lanes))  # critical flow ratio y
        y_total = min(0.85, sum(phases.values()))
        lost = 4 * len(phases)
        cycle = max(40, min(120, round((1.5 * lost + 5) / (1 - y_total))))
        eff_green = cycle - lost
        greens = {k: (round(eff_green * v / y_total) if y_total > 0 else eff_green // 2) for k, v in phases.items()}
        out.append({
            "junction": x, "lat": xlat, "lon": xlon,
            "extra_vehicles_per_hr": round(node_in(after, x) - node_in(before, x)),
            "recommended_cycle_s": cycle, "recommended_green_s": greens,
            "current_assumed": {"cycle_s": 60, "green_s": {k: 26 for k in phases}},
        })
    return {"incident": inc.title, "method": "Webster optimum cycle, 2 phases, saturation 1800 veh/h/lane",
            "junctions": out}
