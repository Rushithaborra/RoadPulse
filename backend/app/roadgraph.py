"""Road network: loads a real OSM graph (data/area.graphml) or builds a synthetic grid for offline demos."""
from __future__ import annotations

import math
import os

import networkx as nx

from . import config
from .geo import haversine_m, offset

HIGHWAY_SPEED_KPH = {
    "trunk": 50, "primary": 45, "secondary": 40, "tertiary": 35,
    "residential": 25, "unclassified": 25, "service": 15, "living_street": 10,
}
SATURATION_VPH_PER_LANE = 1800


class RoadGraph:
    def __init__(self, g: nx.DiGraph, source: str):
        self.g = g
        self.source = source
        self._nodes = [(n, d["lat"], d["lon"]) for n, d in g.nodes(data=True)]

    # ---------- construction ----------
    @classmethod
    def load(cls) -> "RoadGraph":
        path = os.path.abspath(config.GRAPHML_PATH)
        if os.path.exists(path):
            return cls(_from_graphml(path), f"osm:{os.path.basename(path)}")
        return cls(_synthetic_grid(config.CENTER_LAT, config.CENTER_LON), "synthetic-grid")

    # ---------- lookups ----------
    def node_latlon(self, n) -> tuple[float, float]:
        d = self.g.nodes[n]
        return d["lat"], d["lon"]

    def nearest_node(self, lat: float, lon: float):
        return min(self._nodes, key=lambda t: (t[1] - lat) ** 2 + ((t[2] - lon) * math.cos(math.radians(lat))) ** 2)[0]

    def edges_near(self, lat: float, lon: float, radius_m: float) -> list[tuple]:
        out = []
        for u, v in self.g.edges():
            if _point_segment_m(lat, lon, *self.node_latlon(u), *self.node_latlon(v)) <= radius_m:
                out.append((u, v))
        return out

    def nearest_edge(self, lat: float, lon: float, max_m: float = 60) -> tuple | None:
        best, best_d = None, max_m
        for u, v in self.g.edges():
            d = _point_segment_m(lat, lon, *self.node_latlon(u), *self.node_latlon(v))
            if d <= best_d:
                best, best_d = (u, v), d
        return best

    def road_name(self, lat: float, lon: float) -> str | None:
        e = self.nearest_edge(lat, lon, max_m=60)
        return (self.g.edges[e].get("name") or None) if e else None

    def path_coords(self, path: list) -> list[list[float]]:
        return [list(self.node_latlon(n)) for n in path]

    def edge_geo(self, u, v) -> list[list[float]]:
        return [list(self.node_latlon(u)), list(self.node_latlon(v))]


def _point_segment_m(plat, plon, alat, alon, blat, blon) -> float:
    """Distance from point P to segment AB in metres (local flat projection)."""
    kx = 111_320.0 * math.cos(math.radians(plat))
    ky = 111_320.0
    ax, ay = (alon - plon) * kx, (alat - plat) * ky
    bx, by = (blon - plon) * kx, (blat - plat) * ky
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    t = 0.0 if seg2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg2))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(cx, cy)


def _edge_attrs(length_m: float, speed_kph: float, lanes: int, name: str = "") -> dict:
    return {
        "name": name,
        "length": length_m,
        "speed_kph": speed_kph,
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
                if di == 0:  # north-south street, constant i
                    name = "Station Road" if i == 0 else f"Cross Road {i + half + 1}"
                else:
                    name = "Market Road" if j == 0 else f"Line {j + half + 1}"
                u, v = f"n{a[0]}_{a[1]}", f"n{b[0]}_{b[1]}"
                g.add_edge(u, v, **_edge_attrs(spacing, speed, lanes, name))
                g.add_edge(v, u, **_edge_attrs(spacing, speed, lanes, name))
    return g


def _from_graphml(path: str) -> nx.DiGraph:
    raw = nx.read_graphml(path)
    g = nx.DiGraph()
    for n, d in raw.nodes(data=True):
        g.add_node(n, lat=float(d["y"]), lon=float(d["x"]))
    for u, v, d in raw.edges(data=True):
        hw = d.get("highway", "residential")
        if hw.startswith("["):
            hw = hw.strip("[]").split(",")[0].strip(" '")
        speed = HIGHWAY_SPEED_KPH.get(hw, 25)
        try:
            lanes = max(1, int(float(str(d.get("lanes", "1")).strip("[]'").split(",")[0])))
        except ValueError:
            lanes = 1
        length = float(d.get("length") or haversine_m(g.nodes[u]["lat"], g.nodes[u]["lon"], g.nodes[v]["lat"], g.nodes[v]["lon"]))
        if g.has_edge(u, v) and g[u][v]["length"] <= length:
            continue
        name = str(d.get("name", "") or "").strip("[]'").split("',")[0]
        g.add_edge(u, v, **_edge_attrs(length, speed, lanes, name))
    # keep the largest strongly connected part so every route is possible
    biggest = max(nx.strongly_connected_components(g), key=len)
    return g.subgraph(biggest).copy()
