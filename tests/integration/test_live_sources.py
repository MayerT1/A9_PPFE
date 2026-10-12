"""Live-service checks for Phase 1. Run on a networked machine:

    pytest -m integration -p no:cacheprovider tests/integration

They bypass the offline test config by calling the fetchers directly.
"""
import os

import pytest

pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(os.environ.get("PPFE_RUN_INTEGRATION") != "1", reason="set PPFE_RUN_INTEGRATION=1")]

BBOX = [-74.30, 40.85, -74.15, 40.95]  # small Passaic-area box


@pytest.fixture(autouse=True)
def online(monkeypatch):
    from ppfe.config import get_settings

    monkeypatch.setattr(get_settings(), "offline", False)


def test_hrrr_aws_herbie():
    from ppfe.data.grid import Grid
    from ppfe.data.hrrr import _from_aws

    x = _from_aws("2025-07-14", 12, 1, Grid.from_bbox(BBOX, 0.03), ["apcp", "tmp2m", "refc"])
    assert x.shape[0] == 3 and 250 < x[1].mean() < 320


def test_overpass_infrastructure():
    from ppfe.data.external import osm_layer

    gj = osm_layer("infrastructure", BBOX)
    assert "synthetic" not in gj and len(gj["features"]) > 0


def test_fema_nfhl_layer():
    from ppfe.data.external import fema_flood_zones

    gj = fema_flood_zones(BBOX)
    assert "error" not in gj, gj.get("error")
    assert gj["features"], "No SFHA polygons: check FEMA_FLOOD_ZONE_LAYER id"


def test_terrarium_dem():
    from ppfe.data.external import _dem_from_tiles
    from ppfe.data.grid import Grid

    dem = _dem_from_tiles(Grid.from_bbox(BBOX, 0.0075))
    assert -10 < dem.mean() < 500
