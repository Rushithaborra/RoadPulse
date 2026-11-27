"""Export a real road network for the demo area from OpenStreetMap (run once, on a machine with internet).

  pip install osmnx
  python tools/fetch_map.py --lat 18.6066 --lon 84.2290 --dist 1000

Writes data/area.graphml. The server uses it automatically on next start (else it falls back to a synthetic grid).
Keep --dist around 1000 m (a 2 km x 2 km area) so routing stays fast. Then set CENTER_LAT/CENTER_LON and move the
HOTSPOTS in backend/app/config.py onto real flood-prone roads.
"""
import argparse
import os

import osmnx as ox

p = argparse.ArgumentParser()
p.add_argument("--lat", type=float, required=True)
p.add_argument("--lon", type=float, required=True)
p.add_argument("--dist", type=int, default=1000)
a = p.parse_args()

g = ox.graph_from_point((a.lat, a.lon), dist=a.dist, network_type="drive", simplify=True)
out = os.path.join(os.path.dirname(__file__), "..", "data", "area.graphml")
ox.save_graphml(g, out)
print(f"saved {out}: {g.number_of_nodes()} nodes, {g.number_of_edges()} edges")
