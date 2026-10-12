import numpy as np
import pytest

from ppfe.data.grid import Grid
from ppfe.data.hrrr import fetch_and_stage
from ppfe.data.synthetic import synthetic_hrrr
from ppfe.decision.flow import flow_routing, trace_downstream
from ppfe.decision.hazard import hazard_index, summarize
from ppfe.models.registry import get_engine

NAMES = ["apcp", "prate", "tmp2m", "u10", "v10", "refc"]


def test_grid_snapping_and_refine():
    g = Grid.from_bbox([-74.71, 40.66, -74.06, 41.24], 0.03)
    assert g.west <= -74.71 and g.east >= -74.06
    f = g.refine(4)
    assert f.shape == (g.height * 4, g.width * 4)
    assert g.corners()[0] == [g.west, g.north]
    r, c = g.index(g.west + 1e-6, g.north - 1e-6)
    assert (r, c) == (0, 0)


def test_synthetic_is_grid_independent():
    big = Grid.from_bbox([-75.0, 40.0, -74.0, 41.0], 0.03)
    small = Grid(big.west + 0.3, big.north - 0.6, big.west + 0.6, big.north - 0.3, 0.03)
    a = synthetic_hrrr("2026-09-01", 12, 1, big, ["apcp", "tmp2m"])
    b = synthetic_hrrr("2026-09-01", 12, 1, small, ["apcp", "tmp2m"])
    np.testing.assert_allclose(a[:, 10:20, 10:20], b, rtol=1e-5, atol=1e-4)


def test_fetch_and_stage_caches():
    g = Grid.from_bbox([-93.85, 41.45, -93.45, 41.75], 0.03)
    x, m = fetch_and_stage("2026-09-01", 12, 1, g, NAMES)
    assert x.shape == (len(NAMES), *g.shape) and m["source"] == "synthetic" and not m["cached"]
    x2, m2 = fetch_and_stage("2026-09-01", 12, 1, g, NAMES)
    assert m2["cached"] and np.array_equal(x, x2)


def test_apcp_requires_fxx_ge_1():
    g = Grid.from_bbox([-93.8, 41.4, -93.4, 41.7], 0.03)
    with pytest.raises(ValueError):
        fetch_and_stage("2026-09-01", 12, 0, g, NAMES)


def test_engine_shapes_and_tiling_is_seamless():
    eng = get_engine()
    card = eng.model.card
    x = synthetic_hrrr("2026-09-01", 12, 1, Grid(-80, 38, -76.4, 41, 0.03), card.input_names)  # 100 x 120
    s = card.scale_factor
    eng.s.tile_size = 512
    y_one, i1 = eng.run_local(x)
    eng.s.tile_size, eng.s.tile_batch = 48, 3
    y_tiled, i2 = eng.run_local(x)
    eng.s.tile_size, eng.s.tile_batch = 256, 8
    assert y_one.shape == (len(card.outputs), x.shape[1] * s, x.shape[2] * s)
    assert i1["tiles"] == 1 and i2["tiles"] > 1
    np.testing.assert_allclose(y_one, y_tiled, atol=1e-3)


def test_flow_on_tilted_plane_drains_downhill():
    g = Grid(0, 0, 1, 1, 0.02)
    X, Y = g.mesh()
    dem = (X * 100).astype(np.float32)          # high in the east, drains west
    f = flow_routing(dem, np.ones_like(dem), g)
    assert f["area_km2"][:, 0].mean() > f["area_km2"][:, -1].mean() * 10
    path = trace_downstream(f["dir"], g, 0.9, 0.5)
    assert path[-1][0] < path[0][0]


def test_hazard_bounds_and_summary():
    rain = np.linspace(0, 50, 100).reshape(10, 10).astype(np.float32)
    h = hazard_index(rain, rain * 1e5, None)
    assert 0 <= h.min() and h.max() <= 1 and h[0, 0] < h[-1, -1]
    s = summarize(h)
    assert abs(sum(s["percent_area"].values()) - 100) < 0.5


def test_road_routing_avoids_hazard():
    from ppfe.data.external import synthetic_layer
    from ppfe.decision.roads import route

    bbox = [-74.5, 40.5, -74.3, 40.7]
    roads = synthetic_layer("roads", bbox)
    g = Grid(*bbox, 0.005)
    hz = np.zeros(g.shape, np.float32)
    hz[:, 15:25] = 0.9            # a flooded north-south band in the middle
    hz[:4, 15:25] = 0.0           # with one dry crossing in the far north
    r = route(roads, [-74.48, 40.6], [-74.32, 40.6], hz, g, block_threshold=0.7)
    assert r["baseline"] and r["safe"]
    assert r["blocked_edges"] > 0
    assert r["safe"]["max_hazard"] < 0.7 <= r["baseline"]["max_hazard"]
    assert r["safe"]["length_km"] > r["baseline"]["length_km"]
