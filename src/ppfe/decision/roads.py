"""Hazard-aware road routing on OSM roads (networkx).

Builds a graph from OSM highway LineStrings in the start/end bbox, then finds:
  baseline  - fastest route ignoring the forecast
  safe      - fastest route where edges are slowed by predicted hazard and
              edges above `block_threshold` are closed
so planners can see the detour cost of avoiding flooded roads.
"""
from __future__ import annotations

import math

import networkx as nx
import numpy as np

from ..data.grid import Grid

SPEED_KPH = {
    "motorway": 100, "trunk": 85, "primary": 70, "secondary": 60, "tertiary": 50,
    "unclassified": 40, "residential": 30,
}


def _hav_m(a, b) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 12_742_000 * math.asin(math.sqrt(h))


def _key(p) -> tuple[float, float]:
    return (round(p[0], 6), round(p[1], 6))


def build_graph(roads: dict) -> nx.Graph:
    G = nx.Graph()
    for f in roads.get("features", []):
        g = f.get("geometry") or {}
        if g.get("type") != "LineString":
            continue
        hw = (f.get("properties", {}).get("highway") or "residential").replace("_link", "")
        v = SPEED_KPH.get(hw, 30)
        pts = g["coordinates"]
        for a, b in zip(pts[:-1], pts[1:], strict=False):
            ka, kb = _key(a), _key(b)
            d = _hav_m(a, b)
            if d <= 0:
                continue
            t = d / (v / 3.6)
            if not G.has_edge(ka, kb) or G[ka][kb]["base_s"] > t:
                G.add_edge(ka, kb, length_m=d, base_s=t, highway=hw)
    return G


def _nearest(G: nx.Graph, p) -> tuple:
    nodes = np.array(list(G.nodes))
    i = int(np.argmin((nodes[:, 0] - p[0]) ** 2 + ((nodes[:, 1] - p[1]) * 1.3) ** 2))
    return tuple(nodes[i])


def _summ(G, path, attr):
    coords = [list(n) for n in path]
    length = sum(G[a][b]["length_m"] for a, b in zip(path[:-1], path[1:], strict=False))
    t = sum(G[a][b][attr] for a, b in zip(path[:-1], path[1:], strict=False))
    return coords, length, t


def route(roads: dict, start, end, hazard: np.ndarray | None, grid: Grid | None,
          block_threshold: float = 0.7, penalty: float = 4.0) -> dict:
    G = build_graph(roads)
    if G.number_of_nodes() < 2:
        raise ValueError("No road network in this area")
    # Snap to the largest connected component so fragments don't strand a route.
    G = G.subgraph(max(nx.connected_components(G), key=len)).copy()
    s, e = _nearest(G, start), _nearest(G, end)

    blocked = 0
    for a, b, d in G.edges(data=True):
        h = 0.0
        if hazard is not None and grid is not None:
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            if grid.west <= mx <= grid.east and grid.south <= my <= grid.north:
                h = grid.sample(hazard, mx, my)
        d["hazard"] = h
        if h >= block_threshold:
            d["safe_s"] = math.inf
            blocked += 1
        else:
            d["safe_s"] = d["base_s"] * (1 + penalty * h * h)

    out = {"blocked_edges": blocked, "graph_nodes": G.number_of_nodes(), "graph_edges": G.number_of_edges(),
           "snapped_start": list(s), "snapped_end": list(e)}
    try:
        bp = nx.shortest_path(G, s, e, weight="base_s")
        c, L, T = _summ(G, bp, "base_s")
        out["baseline"] = {"coordinates": c, "length_km": round(L / 1000, 2), "minutes": round(T / 60, 1),
                           "max_hazard": round(max((G[a][b]["hazard"] for a, b in zip(bp[:-1], bp[1:], strict=False)), default=0), 3)}
    except nx.NetworkXNoPath:
        out["baseline"] = None

    def w(a, b, d):
        return None if math.isinf(d["safe_s"]) else d["safe_s"]

    try:
        sp = nx.shortest_path(G, s, e, weight=w)
        c, L, _ = _summ(G, sp, "base_s")
        T = sum(G[a][b]["safe_s"] for a, b in zip(sp[:-1], sp[1:], strict=False))
        out["safe"] = {"coordinates": c, "length_km": round(L / 1000, 2), "minutes": round(T / 60, 1),
                       "max_hazard": round(max((G[a][b]["hazard"] for a, b in zip(sp[:-1], sp[1:], strict=False)), default=0), 3)}
    except nx.NetworkXNoPath:
        out["safe"] = None
    return out
