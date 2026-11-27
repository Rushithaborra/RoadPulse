"""Vehicle-aware, load-balanced routing over the road graph."""
from __future__ import annotations

import networkx as nx

from . import config
from .models import Incident
from .roadgraph import RoadGraph, _point_segment_m, project_on_segment
from .severity import edge_multiplier


def incident_effects(rg: RoadGraph, incidents: list[Incident]) -> dict[tuple, list[Incident]]:
    """Which road segments each active incident touches."""
    effects: dict[tuple, list[Incident]] = {}
    for inc in incidents:
        for e in rg.edges_near(inc.lat, inc.lon, config.INCIDENT_EDGE_RADIUS_M):
            effects.setdefault(e, []).append(inc)
    return effects


def edge_time(rg: RoadGraph, e: tuple, vehicle: str, effects: dict, speeds: dict | None = None,
              g: nx.DiGraph | None = None) -> float | None:
    """Travel time in seconds for this vehicle, or None if it must not use the road.

    `g` may be a per-request copy with temporary split edges; a split edge carries `root` (the permanent edge it
    came from) and `frac` (its share of that edge), so incidents, observed speeds and travel time follow the root.
    """
    d = (g if g is not None else rg.g).edges[e]
    root, frac = d.get("root", e), d.get("frac", 1.0)
    rd = rg.g.edges[root]
    t = rd["base_time"]
    if speeds and speeds.get(root):
        t = max(t, rd["length"] / (max(speeds[root], 3) / 3.6))
    for inc in effects.get(root, ()):
        if frac < 1.0 and g is not None:
            # a split piece of a long road: the incident only affects the piece it actually lies on
            a, b = e
            if _point_segment_m(inc.lat, inc.lon, g.nodes[a]["lat"], g.nodes[a]["lon"],
                                g.nodes[b]["lat"], g.nodes[b]["lon"]) > config.INCIDENT_EDGE_RADIUS_M:
                continue
        m = edge_multiplier(inc.type, inc.severity, inc.depth_cm, vehicle)
        if m is None:
            return None
        t *= m
    return t * frac


SPLIT_EPS = 0.02  # within 2% of a junction, start from the junction itself


def _split_at(rg: RoadGraph, g: nx.DiGraph, lat: float, lon: float, label: str):
    """Insert a temporary node where the point projects onto its nearest road segment, in the copy `g` only.

    Every directed edge lying on that segment (both directions of a two-way road, one direction of a one-way road)
    is split into two pieces whose travel time is shared in proportion to distance. Returns (node, projected point).
    """
    root = rg.nearest_edge(lat, lon, max_m=config.MAX_SNAP_M)
    if root is None:
        return None, None
    u, v = root
    targets = [e for e in ((u, v), (v, u)) if rg.g.has_edge(*e)]
    # in the copy, an edge may already have been split (both ends on the same road): find the live piece
    pieces = g.graph.setdefault("pieces", {})  # root edge -> its current pieces in this copy
    best = None
    for r in targets:
        for a, b in pieces.get(r, [r]):
            t, dist = project_on_segment(lat, lon, g.nodes[a]["lat"], g.nodes[a]["lon"],
                                         g.nodes[b]["lat"], g.nodes[b]["lon"])
            if best is None or dist < best[0] - 1e-6:
                best = (dist, (a, b), t)
    _, (a, b), t = best
    if t <= SPLIT_EPS:
        n = a
    elif t >= 1 - SPLIT_EPS:
        n = b
    else:
        alat, alon = g.nodes[a]["lat"], g.nodes[a]["lon"]
        blat, blon = g.nodes[b]["lat"], g.nodes[b]["lon"]
        n = f"__{label}"
        g.add_node(n, lat=alat + t * (blat - alat), lon=alon + t * (blon - alon), virtual=True)
        for x, y in ((a, b), (b, a)):  # split this piece in each direction that exists
            if not g.has_edge(x, y):
                continue
            d = g.edges[x, y]
            r, f = d.get("root", (x, y)), d.get("frac", 1.0)
            part = t if (x, y) == (a, b) else 1 - t  # share of the piece from x to the new point
            g.remove_edge(x, y)
            g.add_edge(x, n, root=r, frac=f * part)
            g.add_edge(n, y, root=r, frac=f * (1 - part))
            live = pieces.setdefault(r, [r])
            live.remove((x, y))
            live += [(x, n), (n, y)]
    return n, (g.nodes[n]["lat"], g.nodes[n]["lon"])


def graph_with_endpoints(rg: RoadGraph, from_lat, from_lon, to_lat, to_lon):
    """Per-request copy of the road graph with the origin and destination inserted on their road segments.
    The permanent graph is never modified."""
    g = rg.g.copy(as_view=False)
    src, src_pt = _split_at(rg, g, from_lat, from_lon, "origin")
    dst, dst_pt = _split_at(rg, g, to_lat, to_lon, "destination")
    return g, src, dst, src_pt, dst_pt


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
                       k: int = 3, max_ratio: float = 1.4, g: nx.DiGraph | None = None) -> list[dict]:
    """Up to k distinct routes (penalty method), with a share of traffic for each."""
    routes, penalties = [], {}
    cache: dict[tuple, float | None] = {}

    def base(u, v):
        e = (u, v)
        if e not in cache:
            cache[e] = edge_time(rg, e, vehicle, effects, speeds, g=g)
        return cache[e]

    def weight(u, v, _d):  # None hides the edge (impassable for this vehicle)
        t = base(u, v)
        return None if t is None else t * penalties.get((u, v), 1.0)

    for _ in range(k * 2):
        try:
            path = nx.shortest_path(g if g is not None else rg.g, src, dst, weight=weight)
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
        r["length_m"] = round(sum(_edge_length(rg, g, a, b) for a, b in zip(r["path"], r["path"][1:])))
        r["coords"] = rg.path_coords(r["path"], g)
        r["time_s"] = round(r["time_s"])
        r["avoided"] = []
    return routes


def _edge_length(rg: RoadGraph, g, a, b) -> float:
    d = (g if g is not None else rg.g).edges[a, b]
    return rg.g.edges[d.get("root", (a, b))]["length"] * d.get("frac", 1.0)
