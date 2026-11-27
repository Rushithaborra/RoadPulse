"""Slowdown detection from fleet speed probes, rule v2.

Why a new rule: the first version judged each tiny road piece (the median piece of the Dwaraka Nagar graph is about
21 m, because curves are split into shape points) from as few as 3 vehicles, against the speed LIMIT, and raised a
signal at once. That mistakes ordinary things for incidents. This rule answers the doubts we discussed:

  * One slow driver (a senior citizen, a loaded cart) must not matter: a vehicle gets ONE vote, and a clear majority
    (PROBE_SLOW_FRAC) must be slow, as well as the median.
  * Too few vehicles means "not enough data", never an alert (PROBE_MIN_VEHICLES).
  * "Slow" is judged against what is normal for THIS road at THIS time of the week (learned), not the speed limit.
  * Red-light queues are normal: samples near a junction are ignored (PROBE_JUNCTION_ZONE_M).
  * The dip must last longer than a signal cycle (PROBE_SUSTAIN_S).
  * Roads are judged as LINKS (junction to junction), so mid-block incidents are found even with no sensor there,
    and the incident is placed where the slow vehicles actually are.
  * If most neighbouring links are slow too, it is area-wide congestion, not a local incident.

Even then, a probe only raises a "Suspected slowdown" for officers: it is not a trusted source and never publishes.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

from . import config
from .geo import haversine_m
from .roadgraph import RoadGraph
from .store import Store


@dataclass(frozen=True)
class Link:
    key: tuple          # (start junction, end junction, first edge): one direction of one road between junctions
    edges: tuple        # the directed graph edges that make it up (curve pieces included)
    length: float


def _is_shape(rg: RoadGraph, n) -> bool:
    return bool(rg.g.nodes[n].get("shape"))


def link_of(rg: RoadGraph, edge: tuple) -> Link:
    """The whole junction-to-junction stretch that this graph edge belongs to (cached on the graph object)."""
    cache = rg.__dict__.setdefault("_link_cache", {})
    if edge in cache:
        return cache[edge]
    chain = [edge]
    seen = {edge}
    while _is_shape(rg, chain[0][0]):
        a, b = chain[0]
        cands = [p for p in rg.g.predecessors(a) if p != b and (p, a) not in seen and rg.g.has_edge(p, a)]
        if len(cands) != 1:
            break
        chain.insert(0, (cands[0], a))
        seen.add((cands[0], a))
    while _is_shape(rg, chain[-1][1]):
        a, b = chain[-1]
        cands = [s for s in rg.g.successors(b) if s != a and (b, s) not in seen]
        if len(cands) != 1:
            break
        chain.append((b, cands[0]))
        seen.add((b, cands[0]))
    link = Link(key=(chain[0][0], chain[-1][1], chain[0][1]), edges=tuple(chain),
                length=sum(rg.g.edges[e]["length"] for e in chain))
    for e in chain:
        cache[e] = link
    return link


def neighbour_links(rg: RoadGraph, link: Link) -> list[Link]:
    out, seen = [], {link.key}
    for node in (link.edges[0][0], link.edges[-1][1]):
        for nbr in set(rg.g.successors(node)) | set(rg.g.predecessors(node)):
            for e in ((node, nbr), (nbr, node)):
                if rg.g.has_edge(*e):
                    other = link_of(rg, e)
                    if other.key not in seen:
                        seen.add(other.key)
                        out.append(other)
    return out


def _in_junction_zone(rg: RoadGraph, link: Link, lat: float, lon: float) -> bool:
    zone = min(config.PROBE_JUNCTION_ZONE_M, 0.25 * link.length)
    for node in (link.edges[0][0], link.edges[-1][1]):
        nlat, nlon = rg.node_latlon(node)
        if haversine_m(lat, lon, nlat, nlon) < zone:
            return True
    return False


def hour_of_week(ts: float) -> int:
    import time
    t = time.gmtime(ts + config.UTC_OFFSET_S)
    return t.tm_wday * 24 + t.tm_hour


def baseline_kph(store: Store, rg: RoadGraph, link: Link, ts: float) -> float:
    """Normal speed for this link now: learned for this hour of the week once seen enough times, else a fraction of the
    speed limit (clearly an assumption until traffic data accumulates)."""
    learned = store.baseline.get((link.key, hour_of_week(ts)))
    if learned and learned[1] >= config.PROBE_BASELINE_MIN_UPDATES:
        return learned[0]
    speeds = [rg.g.edges[e]["speed_kph"] for e in link.edges]
    return (sum(speeds) / len(speeds)) * config.PROBE_FALLBACK_FRACTION


def analyze(store: Store, rg: RoadGraph, link: Link, ts: float) -> dict:
    """Judge one link from the vehicles seen in the last PROBE_WINDOW_S. Never decides from a single vehicle."""
    latest: dict = {}
    for t, vid, kph, lat, lon in store.link_samples.get(link.key, ()):
        if ts - t > config.PROBE_WINDOW_S or t > ts + 1:
            continue
        if _in_junction_zone(rg, link, lat, lon):
            continue  # red lights make everyone stop here: normal
        if vid not in latest or t > latest[vid][0]:
            latest[vid] = (t, kph, lat, lon)  # one vote per vehicle
    n = len(latest)
    base = baseline_kph(store, rg, link, ts)
    if n < config.PROBE_MIN_VEHICLES:
        return {"status": "insufficient", "n": n, "baseline": round(base, 1)}
    speeds = [v[1] for v in latest.values()]
    med = statistics.median(speeds)
    thr = config.PROBE_SLOW_RATIO * base
    slow = [v for v in latest.values() if v[1] < thr]
    frac = len(slow) / n
    anomalous = med < thr and frac >= config.PROBE_SLOW_FRAC
    res = {"status": "anomaly" if anomalous else "normal", "n": n, "median": round(med, 1),
           "baseline": round(base, 1), "slow_frac": round(frac, 2)}
    if slow:  # where the slow vehicles are: a mid-block incident is placed on the road, not at a junction
        res["lat"] = sum(v[2] for v in slow) / len(slow)
        res["lon"] = sum(v[3] for v in slow) / len(slow)
    return res


def update(store: Store, rg: RoadGraph, edge: tuple, vehicle_id: str, lat: float, lon: float, kph: float,
           ts: float) -> dict:
    """Record one probe sample and re-judge its link. Returns the analysis plus: link, sustained, since."""
    link = link_of(rg, edge)
    store.link_samples[link.key].append((ts, vehicle_id, kph, lat, lon))
    res = analyze(store, rg, link, ts)
    status = res["status"]
    if status == "anomaly":
        near = [store.link_state.get(o.key) for o in neighbour_links(rg, link)]
        near = [s for s in near if s in ("normal", "anomaly", "congestion")]
        if near and sum(1 for s in near if s != "normal") / len(near) >= 0.5:
            status = "congestion"  # the neighbours are slow too: area-wide, not a local incident
    res["status"] = status
    store.link_state[link.key] = status
    res["link"] = link
    res["sustained"] = False
    if status == "anomaly":
        since = store.link_anomaly_since.setdefault(link.key, ts)
        res["since"] = since
        res["sustained"] = ts - since >= config.PROBE_SUSTAIN_S
    else:
        store.link_anomaly_since.pop(link.key, None)
        if status == "normal":
            key = (link.key, hour_of_week(ts))
            old, count = store.baseline.get(key, (res["median"], 0))
            store.baseline[key] = (0.9 * old + 0.1 * res["median"] if count else res["median"], count + 1)
    return res
