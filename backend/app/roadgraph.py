"""Road network: loads a real OpenStreetMap graph (data/area.graphml, exported by tools/fetch_map.py) or builds a
synthetic grid for offline demos.

Real-map rules (see tests/test_osm_graph.py):
- Directed edges are kept exactly as OSM/OSMnx gives them. One-way roads have one direction only; no reverse edges
  are ever invented.
- Curved roads: OSMnx stores the road's shape in `geometry`; it is split into straight pieces with shared
  intermediate points, so drawing, snapping and splitting follow the real road, not the chord between junctions.
- Parallel OSM edges between the same two junctions: the one with the lowest estimated travel time is kept (then
  more lanes, then higher road class).
- Speed: OSM `maxspeed` when it parses to a number ("50", "30 mph", "[40, 50]"); otherwise a highway-class
  estimate, marked speed_source="estimate".
- The largest WEAKLY connected component is kept (a strongly connected filter would drop every street that is only
  reachable one way). Some origin/destination pairs may therefore have no legal route; the API says so.
"""
from __future__ import annotations

import math
import os
import re
from collections import defaultdict

import networkx as nx

from . import config
from .geo import haversine_m, offset

# Estimates for urban Indian roads when OSM has no usable maxspeed (km/h). Labelled speed_source="estimate".
HIGHWAY_SPEED_KPH = {
    "motorway": 60, "trunk": 50, "primary": 45, "secondary": 40, "tertiary": 35,
    "motorway_link": 40, "trunk_link": 35, "primary_link": 35, "secondary_link": 30, "tertiary_link": 30,
    "residential": 25, "unclassified": 25, "service": 15, "living_street": 10, "road": 25,
}
HIGHWAY_RANK = {k: i for i, k in enumerate(
    ["living_street", "service", "road", "unclassified", "residential", "tertiary_link", "tertiary",
     "secondary_link", "secondary", "primary_link", "primary", "trunk_link", "trunk", "motorway_link", "motorway"])}
SATURATION_VPH_PER_LANE = 1800
CELL_DEG = 0.001  # spatial index cell, about 110 m


class RoadGraph:
    def __init__(self, g: nx.DiGraph, source: str):
        self.g = g
        self.source = source
        self.mode = "synthetic" if source == "synthetic-grid" else "osm"
        lats = [d["lat"] for _, d in g.nodes(data=True)]
        lons = [d["lon"] for _, d in g.nodes(data=True)]
        self.bbox = (min(lats), min(lons), max(lats), max(lons))
        self._nodes = [(n, d["lat"], d["lon"]) for n, d in g.nodes(data=True)]
        self._build_index()

    # ---------- construction ----------
    warning: str | None = None  # set when the real road graph is NOT in use; shown to users, never silent

    @classmethod
    def load(cls) -> "RoadGraph":
        """The real OSM graph if data/area.graphml loads; otherwise the synthetic grid WITH a warning that the API
        and the UI both show. A broken file does not stop the server, but it can never pass as real data."""
        path = os.path.abspath(config.GRAPHML_PATH)
        if os.path.exists(path):
            try:
                return cls(_from_graphml(path), f"osm:{os.path.basename(path)}")
            except Exception as e:  # corrupt or unsupported file
                reason = f"{os.path.basename(path)} could not be loaded ({type(e).__name__}: {str(e)[:120]})"
        else:
            reason = f"No real road graph found ({os.path.basename(path)} is missing; run tools/fetch_map.py)"
        rg = cls(_synthetic_grid(config.CENTER_LAT, config.CENTER_LON), "synthetic-grid")
        rg.warning = f"{reason}. Showing a SYNTHETIC demo grid: these are not real roads and routes are not real."
        return rg

    def _build_index(self):
        """Grid buckets of edges and nodes so nearest-road lookups do not scan the whole graph."""
        self._edge_cells: dict[tuple, list] = defaultdict(list)
        self._node_cells: dict[tuple, list] = defaultdict(list)
        for n, lat, lon in self._nodes:
            self._node_cells[self._cell(lat, lon)].append((n, lat, lon))
        for u, v in self.g.edges():
            (alat, alon), (blat, blon) = self.node_latlon(u), self.node_latlon(v)
            for i in range(int(min(alat, blat) // CELL_DEG), int(max(alat, blat) // CELL_DEG) + 1):
                for j in range(int(min(alon, blon) // CELL_DEG), int(max(alon, blon) // CELL_DEG) + 1):
                    self._edge_cells[(i, j)].append((u, v))

    @staticmethod
    def _cell(lat, lon):
        return int(lat // CELL_DEG), int(lon // CELL_DEG)

    def _cells_around(self, lat, lon, radius_m):
        r = int(radius_m / 100) + 1
        ci, cj = self._cell(lat, lon)
        return [(ci + di, cj + dj) for di in range(-r, r + 1) for dj in range(-r, r + 1)]

    def _candidate_edges(self, lat, lon, radius_m):
        seen = set()
        for c in self._cells_around(lat, lon, radius_m):
            for e in self._edge_cells.get(c, ()):
                if e not in seen:
                    seen.add(e)
                    yield e

    # ---------- lookups ----------
    def centre(self) -> tuple[float, float]:
        s, w, n, e = self.bbox
        return (s + n) / 2, (w + e) / 2

    def node_latlon(self, n) -> tuple[float, float]:
        d = self.g.nodes[n]
        return d["lat"], d["lon"]

    def nearest_node(self, lat: float, lon: float):
        k = math.cos(math.radians(lat))
        for radius in (300, 1500):
            cands = [t for c in self._cells_around(lat, lon, radius) for t in self._node_cells.get(c, ())]
            if cands:
                return min(cands, key=lambda t: (t[1] - lat) ** 2 + ((t[2] - lon) * k) ** 2)[0]
        return min(self._nodes, key=lambda t: (t[1] - lat) ** 2 + ((t[2] - lon) * k) ** 2)[0]

    def snap(self, lat: float, lon: float) -> tuple[object, float]:
        """Nearest road node and its real (haversine) distance in metres."""
        n = self.nearest_node(lat, lon)
        nlat, nlon = self.node_latlon(n)
        return n, haversine_m(lat, lon, nlat, nlon)

    def distance_to_network(self, lat: float, lon: float) -> float:
        """Metres from the point to the nearest road SEGMENT (not just the nearest junction)."""
        s_lat, w_lon, n_lat, e_lon = self.bbox
        c_lat = min(max(lat, s_lat), n_lat)
        c_lon = min(max(lon, w_lon), e_lon)
        to_box = haversine_m(lat, lon, c_lat, c_lon)
        if to_box > 1000:
            return to_box  # lower bound, already beyond any snap limit
        best = float("inf")
        for u, v in self._candidate_edges(lat, lon, 1000):
            best = min(best, _point_segment_m(lat, lon, *self.node_latlon(u), *self.node_latlon(v)))
        return best

    def edges_near(self, lat: float, lon: float, radius_m: float) -> list[tuple]:
        return [(u, v) for u, v in self._candidate_edges(lat, lon, radius_m)
                if _point_segment_m(lat, lon, *self.node_latlon(u), *self.node_latlon(v)) <= radius_m]

    def nearest_edge(self, lat: float, lon: float, max_m: float = 60) -> tuple | None:
        best, best_d = None, max_m
        for u, v in self._candidate_edges(lat, lon, max_m):
            d = _point_segment_m(lat, lon, *self.node_latlon(u), *self.node_latlon(v))
            if d <= best_d:
                best, best_d = (u, v), d
        return best

    def road_name(self, lat: float, lon: float) -> str | None:
        e = self.nearest_edge(lat, lon, max_m=60)
        return (self.g.edges[e].get("name") or None) if e else None

    def path_coords(self, path: list, g: nx.DiGraph | None = None) -> list[list[float]]:
        g = g if g is not None else self.g
        return [[g.nodes[n]["lat"], g.nodes[n]["lon"]] for n in path]

    def edge_geo(self, u, v) -> list[list[float]]:
        return [list(self.node_latlon(u)), list(self.node_latlon(v))]

    def stats(self) -> dict:
        names = {d.get("name") for _, _, d in self.g.edges(data=True) if d.get("name")}
        oneway = sum(1 for u, v in self.g.edges() if not self.g.has_edge(v, u))
        osm_speed = sum(1 for _, _, d in self.g.edges(data=True) if d.get("speed_source") == "osm_maxspeed")
        return {"nodes": self.g.number_of_nodes(), "edges": self.g.number_of_edges(), "named_roads": len(names),
                "one_way_edges": oneway, "edges_with_osm_maxspeed": osm_speed,
                "weak_components": nx.number_weakly_connected_components(self.g),
                "strong_components": nx.number_strongly_connected_components(self.g),
                "bbox": {"south": self.bbox[0], "west": self.bbox[1], "north": self.bbox[2], "east": self.bbox[3]}}


def project_on_segment(plat, plon, alat, alon, blat, blon) -> tuple[float, float]:
    """(t, distance_m): t in [0, 1] is how far along A->B the closest point lies."""
    kx = 111_320.0 * math.cos(math.radians(plat))
    ky = 111_320.0
    ax, ay = (alon - plon) * kx, (alat - plat) * ky
    bx, by = (blon - plon) * kx, (blat - plat) * ky
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    t = 0.0 if seg2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg2))
    return t, math.hypot(ax + t * dx, ay + t * dy)


def _point_segment_m(plat, plon, alat, alon, blat, blon) -> float:
    """Distance from point P to segment AB in metres (local flat projection)."""
    return project_on_segment(plat, plon, alat, alon, blat, blon)[1]


def _edge_attrs(length_m: float, speed_kph: float, lanes: int, name: str = "", highway: str = "residential",
                speed_source: str = "estimate") -> dict:
    return {
        "name": name,
        "highway": highway,
        "length": length_m,
        "speed_kph": speed_kph,
        "speed_source": speed_source,
        "base_time": length_m / (speed_kph / 3.6),
        "lanes": lanes,
        "capacity_vph": lanes * SATURATION_VPH_PER_LANE * 0.5,  # mid-block share of saturation flow
    }


def _synthetic_grid(lat0: float, lon0: float, half: int = 4, spacing: float = 150) -> nx.DiGraph:
    """A 9x9 grid of streets around the centre. Row 0 and column 0 are faster arterials."""
    g = nx.DiGraph()
    for i in range(-half, half + 1):
        for j in range(-half, half + 1):
            lat, lon = offset(lat0, lon0, i * spacing, j * spacing)
            g.add_node(f"n{i}_{j}", lat=lat, lon=lon)
    for i in range(-half, half + 1):
        for j in range(-half, half + 1):
            for di, dj in ((1, 0), (0, 1)):
                a, b = (i, j), (i + di, j + dj)
                if abs(b[0]) > half or abs(b[1]) > half:
                    continue
                arterial = (dj == 0 and j == 0) or (di == 0 and i == 0)
                speed, lanes = (40, 2) if arterial else (25, 1)
                # deliberately artificial names: this grid is not a real place
                if di == 0:  # north-south street, constant i
                    name = "Synthetic Main Avenue" if i == 0 else f"Synthetic Avenue {i + half + 1}"
                else:
                    name = "Synthetic Main Street" if j == 0 else f"Synthetic Street {j + half + 1}"
                hw = "secondary" if arterial else "residential"
                u, v = f"n{a[0]}_{a[1]}", f"n{b[0]}_{b[1]}"
                g.add_edge(u, v, **_edge_attrs(spacing, speed, lanes, name, hw))
                g.add_edge(v, u, **_edge_attrs(spacing, speed, lanes, name, hw))
    return g


# ---------------- OpenStreetMap GraphML ----------------

def _first(value) -> str:
    """OSMnx writes list attributes as "['a', 'b']"; take the first item."""
    s = str(value or "").strip()
    if s.startswith("["):
        s = s.strip("[]").split(",")[0]
    return s.strip(" '\"")


def parse_maxspeed(value) -> float | None:
    """Numeric km/h from an OSM maxspeed value, or None when it is missing or not a number (e.g. 'IN:urban')."""
    if value in (None, ""):
        return None
    s = str(value).lower()
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s)]
    if not nums:
        return None
    kph = min(nums) * (1.609 if "mph" in s else 1.0)  # several limits listed: take the lowest
    return kph if 5 <= kph <= 130 else None


def parse_lanes(value) -> int:
    nums = [int(float(x)) for x in re.findall(r"\d+(?:\.\d+)?", str(value or ""))]
    return max(1, min(nums)) if nums else 1  # osm 'lanes' counts both directions on two-way roads; conservative


def parse_linestring(wkt: str) -> list[tuple[float, float]] | None:
    """[(lat, lon), ...] from 'LINESTRING (lon lat, lon lat, ...)'."""
    m = re.match(r"\s*LINESTRING\s*\((.*)\)\s*$", str(wkt or ""), re.I)
    if not m:
        return None
    pts = []
    for pair in m.group(1).split(","):
        x, y = pair.split()[:2]
        pts.append((float(y), float(x)))
    return pts if len(pts) >= 2 else None


def _from_graphml(path: str) -> nx.DiGraph:
    raw = nx.read_graphml(path, force_multigraph=True)
    if not raw.is_directed():
        raise ValueError(f"{path}: expected a directed OSMnx graph")

    # 1. choose one edge per (u, v): lowest estimated travel time, then more lanes, then higher road class
    chosen: dict[tuple, dict] = {}
    for u, v, d in raw.edges(data=True):
        hw = _first(d.get("highway")) or "unclassified"
        osm_speed = parse_maxspeed(_first(d.get("maxspeed")) if str(d.get("maxspeed", "")).startswith("[")
                                   else d.get("maxspeed"))
        speed = osm_speed or HIGHWAY_SPEED_KPH.get(hw, 25)
        ulat, ulon = float(raw.nodes[u]["y"]), float(raw.nodes[u]["x"])
        vlat, vlon = float(raw.nodes[v]["y"]), float(raw.nodes[v]["x"])
        length = float(d.get("length") or 0) or haversine_m(ulat, ulon, vlat, vlon)
        cand = {
            "name": _first(d.get("name")), "highway": hw, "length": length, "speed": speed,
            "speed_source": "osm_maxspeed" if osm_speed else "estimate", "lanes": parse_lanes(d.get("lanes")),
            "geometry": parse_linestring(d.get("geometry")), "time": length / (speed / 3.6),
        }
        key = (u, v)
        best = chosen.get(key)
        rank = (cand["time"], -cand["lanes"], -HIGHWAY_RANK.get(hw, 0))
        if best is None or rank < (best["time"], -best["lanes"], -HIGHWAY_RANK.get(best["highway"], 0)):
            chosen[key] = cand

    # 2. build the routing graph; curved roads become chains of straight pieces with shared intermediate points
    g = nx.DiGraph()
    for n, d in raw.nodes(data=True):
        g.add_node(n, lat=float(d["y"]), lon=float(d["x"]))

    def shape_node(lat, lon):
        nid = f"p{lat:.7f},{lon:.7f}"  # same point on both directions of a two-way road -> same node
        if nid not in g:
            g.add_node(nid, lat=lat, lon=lon, shape=True)
        return nid

    for (u, v), c in chosen.items():
        pts = c["geometry"]
        ulat, ulon = g.nodes[u]["lat"], g.nodes[u]["lon"]
        vlat, vlon = g.nodes[v]["lat"], g.nodes[v]["lon"]
        if not pts or len(pts) <= 2:
            chain = [u, v]
        else:
            if haversine_m(pts[0][0], pts[0][1], ulat, ulon) > haversine_m(pts[-1][0], pts[-1][1], ulat, ulon):
                pts = pts[::-1]  # geometry must run from u to v
            chain = [u] + [shape_node(lat, lon) for lat, lon in pts[1:-1]] + [v]
        pieces = [haversine_m(*_ll(g, a), *_ll(g, b)) for a, b in zip(chain, chain[1:])]
        total = sum(pieces) or 1.0
        for (a, b), piece in zip(zip(chain, chain[1:]), pieces):
            if a == b:
                continue
            seg_len = c["length"] * piece / total  # keep OSM's measured length, shared out along the shape
            attrs = _edge_attrs(max(seg_len, 0.1), c["speed"], c["lanes"], c["name"], c["highway"], c["speed_source"])
            if not g.has_edge(a, b) or g[a][b]["base_time"] > attrs["base_time"]:
                g.add_edge(a, b, **attrs)

    # 3. keep the largest weakly connected component (one-way streets stay; isolated fragments go)
    biggest = max(nx.weakly_connected_components(g), key=len)
    return g.subgraph(biggest).copy()


def _ll(g, n):
    d = g.nodes[n]
    return d["lat"], d["lon"]
