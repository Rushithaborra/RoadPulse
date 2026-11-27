"""Export the real road network for the demo area from OpenStreetMap (run once, on a machine with internet).

  pip install osmnx
  python tools/fetch_map.py                      # default: Dwaraka Nagar, Visakhapatnam, 1500 m radius
  python tools/fetch_map.py --lat 17.72867 --lon 83.308634 --dist 1500

Writes data/area.graphml (OSMnx GraphML: directed, one-way roads kept, curved roads keep their geometry) and prints
the actual statistics after RoadPulse's loading rules. The server uses the file automatically on next start; delete
it to return to the synthetic grid. Road data (c) OpenStreetMap contributors, ODbL: keep the attribution visible.
Keep --dist around 1000-2000 m so routing stays fast.
"""
import argparse
import json
import os
import sys

import osmnx as ox

p = argparse.ArgumentParser()
p.add_argument("--lat", type=float, default=17.728670)
p.add_argument("--lon", type=float, default=83.308634)
p.add_argument("--dist", type=int, default=1500)
a = p.parse_args()

g = ox.graph_from_point((a.lat, a.lon), dist=a.dist, network_type="drive", simplify=True)
out = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "area.graphml"))
ox.save_graphml(g, out)
print(f"saved {out} ({os.path.getsize(out) / 1e6:.1f} MB)")
print(f"OSMnx graph: {g.number_of_nodes()} nodes, {g.number_of_edges()} edges")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from app.roadgraph import RoadGraph, _from_graphml  # noqa: E402

rg = RoadGraph(_from_graphml(out), "osm:area.graphml")
print("RoadPulse routing graph:", json.dumps(rg.stats(), indent=2))
