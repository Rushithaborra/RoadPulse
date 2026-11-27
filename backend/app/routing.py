"""Vehicle-aware, load-balanced routing over the road graph."""
from __future__ import annotations

import networkx as nx

from . import config
from .models import Incident
from .roadgraph import RoadGraph
from .severity import edge_multiplier


def incident_effects(rg: RoadGraph, incidents: list[Incident]) -> dict[tuple, list[Incident]]:
    """Which road segments each active incident touches."""
    effects: dict[tuple, list[Incident]] = {}
    for inc in incidents:
        for e in rg.edges_near(inc.lat, inc.lon, config.INCIDENT_EDGE_RADIUS_M):
            effects.setdefault(e, []).append(inc)
    return effects


def edge_time(rg: RoadGraph, e: tuple, vehicle: str, effects: dict, speeds: dict | None = None) -> float | None:
    """Travel time in seconds for this vehicle, or None if it must not use the road."""
    d = rg.g.edges[e]
    t = d["base_time"]
    if speeds and e in speeds and speeds[e]:
        t = max(t, d["length"] / (max(speeds[e], 3) / 3.6))
    for inc in effects.get(e, ()):
        m = edge_multiplier(inc.type, inc.severity, inc.depth_cm, vehicle)
        if m is None:
            return None
        t *= m
    return t


def weighted_graph(rg: RoadGraph, vehicle: str, effects: dict, speeds: dict | None = None,
                   extra: dict | None = None) -> nx.DiGraph:
    """Copy of the graph with a per-vehicle 'w' weight; impassable edges removed. `extra` multiplies weights."""
    g = nx.DiGraph()
    g.add_nodes_from(rg.g.nodes)
    for e in rg.g.edges:
        t = edge_time(rg, e, vehicle, effects, speeds)
        if t is None:
            continue
        if extra and e in extra:
            t *= extra[e]
        g.add_edge(*e, w=t)
    return g


def route_alternatives(rg: RoadGraph, src, dst, vehicle: str, effects: dict, speeds: dict | None = None,
                       k: int = 3, max_ratio: float = 1.4) -> list[dict]:
    """Up to k distinct routes (penalty method), with a share of traffic for each."""
    routes, penalties = [], {}
    cache: dict[tuple, float | None] = {}

    def base(u, v):
        e = (u, v)
        if e not in cache:
            cache[e] = edge_time(rg, e, vehicle, effects, speeds)
        return cache[e]

    def weight(u, v, _d):  # None hides the edge (impassable for this vehicle)
        t = base(u, v)
        return None if t is None else t * penalties.get((u, v), 1.0)

    for _ in range(k * 2):
        try:
            path = nx.shortest_path(rg.g, src, dst, weight=weight)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            break
        true_t = sum(base(a, b) for a, b in zip(path, path[1:]))
        if all(path != r["path"] for r in routes):
            routes.append({"path": path, "time_s": true_t})
        for a, b in zip(path, path[1:]):
            penalties[(a, b)] = penalties.get((a, b), 1.0) * 1.6
        if len(routes) >= k:
            break
    if not routes:
        return []
    best = min(r["time_s"] for r in routes)
    routes = [r for r in routes if r["time_s"] <= best * max_ratio]
    routes.sort(key=lambda r: r["time_s"])
    # split traffic: faster routes get more, but alternatives still take a share so the detour does not jam
    weights = [(best / r["time_s"]) ** 4 for r in routes]
    total = sum(weights)
    for r, w in zip(routes, weights):
        r["share"] = round(w / total, 2)
        r["length_m"] = round(sum(rg.g.edges[(a, b)]["length"] for a, b in zip(r["path"], r["path"][1:])))
        r["coords"] = rg.path_coords(r["path"])
        r["time_s"] = round(r["time_s"])
        r["avoided"] = []
    return routes
