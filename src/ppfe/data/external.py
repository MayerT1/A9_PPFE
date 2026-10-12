"""External context layers: OSM (Overpass), FEMA NFHL flood zones, DEM.

All fetches are cached on disk under data/cache/<kind>/ and degrade to
clearly-labelled synthetic stand-ins when offline (PPFE_OFFLINE=1) or when
the remote service fails, so the portal never hard-fails on a third party.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import math
from pathlib import Path

import numpy as np

from ..config import get_settings
from .grid import Grid
from .synthetic import _seed, _texture

log = logging.getLogger(__name__)

UA = {"User-Agent": "A9_PPFE/0.1 (NASA EarthRISE, UAH)"}

# FEMA NFHL MapServer layer for flood hazard zone polygons (S_FLD_HAZ_AR).
# VERIFY against {fema_nfhl_url}?f=json if results look empty -- layer ids
# have changed historically.
FEMA_FLOOD_ZONE_LAYER = 28


def _ckey(*parts) -> str:
    return hashlib.sha1(json.dumps(parts).encode()).hexdigest()[:16]


def _cached_json(kind: str, key: str, fn):
    p = get_settings().cache_dir / kind / f"{key}.json"
    if p.exists():
        return json.loads(p.read_text())
    data = fn()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data))
    return data


# ============================================================== Overpass/OSM
def _overpass(query: str) -> dict:
    import httpx

    s = get_settings()
    r = httpx.post(s.overpass_url, data={"data": query}, headers=UA, timeout=90)
    r.raise_for_status()
    return r.json()


def _osm_to_geojson(osm: dict, kind: str) -> dict:
    feats = []
    for el in osm.get("elements", []):
        tags = el.get("tags", {})
        if "geometry" in el and el["type"] == "way":
            coords = [[g["lon"], g["lat"]] for g in el["geometry"]]
            geom = {"type": "LineString", "coordinates": coords}
        elif "center" in el:
            geom = {"type": "Point", "coordinates": [el["center"]["lon"], el["center"]["lat"]]}
        elif el["type"] == "node":
            geom = {"type": "Point", "coordinates": [el["lon"], el["lat"]]}
        else:
            continue
        feats.append({
            "type": "Feature", "id": f"{el['type']}/{el['id']}", "geometry": geom,
            "properties": {"kind": kind, "name": tags.get("name", ""), **{
                k: tags[k] for k in ("waterway", "highway", "amenity", "power", "man_made",
                                     "emergency", "bridge", "maxspeed") if k in tags}},
        })
    return {"type": "FeatureCollection", "features": feats}


def _area_deg2(b) -> float:
    return (b[2] - b[0]) * (b[3] - b[1])


INFRA_CLASSES = {
    "hospital": "Hospital", "fire_station": "Fire station", "police": "Police",
    "school": "School", "shelter": "Shelter", "substation": "Power substation",
    "wastewater_plant": "Wastewater plant", "water_works": "Water works",
    "pumping_station": "Pumping station",
}


def infra_class(props: dict) -> str:
    for k in ("amenity", "power", "man_made"):
        v = props.get(k)
        if v in INFRA_CLASSES:
            return v
    return "other"


def osm_layer(kind: str, bbox: list[float]) -> dict:
    """kind: waterways | infrastructure | roads"""
    s = get_settings()
    w, so, e, n = bbox
    bb = f"{so},{w},{n},{e}"
    big = _area_deg2(bbox) > 1.5
    if kind == "waterways":
        sel = '"waterway"~"^(river|canal)$"' if big else '"waterway"~"^(river|stream|canal|drain)$"'
        q = f"[out:json][timeout:80];way[{sel}]({bb});out geom;"
    elif kind == "infrastructure":
        q = (f"[out:json][timeout:80];("
             f'nwr["amenity"~"^(hospital|fire_station|police|school|shelter)$"]({bb});'
             f'nwr["power"="substation"]({bb});'
             f'nwr["man_made"~"^(wastewater_plant|water_works|pumping_station)$"]({bb});'
             f");out center;")
    elif kind == "roads":
        cls = "motorway|trunk|primary|secondary|tertiary" + ("" if big else "|unclassified|residential")
        q = f'[out:json][timeout:80];way["highway"~"^({cls})(_link)?$"]({bb});out geom;'
    else:
        raise ValueError(kind)

    if s.offline:
        return synthetic_layer(kind, bbox)
    try:
        gj = _cached_json(f"osm_{kind}", _ckey(kind, [round(v, 4) for v in bbox]),
                          lambda: _osm_to_geojson(_overpass(q), kind))
        gj["source"] = "OpenStreetMap (Overpass)"
        return gj
    except Exception as e:
        log.warning("Overpass %s failed (%s); synthetic stand-in", kind, e)
        gj = synthetic_layer(kind, bbox)
        gj["error"] = str(e)
        return gj


def synthetic_layer(kind: str, bbox: list[float]) -> dict:
    """Plausible stand-ins so offline dev exercises the same code paths."""
    w, s, e, n = bbox
    rng = np.random.default_rng(_seed(kind, *[round(v, 3) for v in bbox]))
    feats = []
    if kind == "roads":
        step = max((e - w), (n - s)) / 18
        xs = np.arange(w, e + 1e-9, step)
        ys = np.arange(s, n + 1e-9, step)
        jit = lambda: rng.normal(0, step * 0.08)  # noqa: E731
        P = {(i, j): [float(x + jit()), float(y + jit())] for i, x in enumerate(xs) for j, y in enumerate(ys)}
        for i in range(len(xs)):
            for j in range(len(ys)):
                for di, dj in ((1, 0), (0, 1)):
                    if (i + di, j + dj) in P:
                        major = (i % 4 == 0 and di == 0) or (j % 4 == 0 and dj == 0)
                        feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [P[i, j], P[i + di, j + dj]]},
                                      "properties": {"kind": "roads", "highway": "primary" if major else "residential"}})
    elif kind == "waterways":
        for k in range(4):
            y = s + (k + 0.5) * (n - s) / 4
            xs = np.linspace(w, e, 60)
            ys = y + 0.08 * (n - s) * np.sin(xs * rng.uniform(5, 12) + rng.uniform(0, 6))
            feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": np.c_[xs, ys].round(5).tolist()},
                          "properties": {"kind": "waterways", "waterway": "river", "name": f"Synthetic river {k + 1}"}})
    elif kind == "infrastructure":
        kinds = list(INFRA_CLASSES)
        for k in range(40):
            c = kinds[k % len(kinds)]
            key = "power" if c == "substation" else ("man_made" if c in ("wastewater_plant", "water_works", "pumping_station") else "amenity")
            feats.append({"type": "Feature", "id": f"syn/{k}",
                          "geometry": {"type": "Point", "coordinates": [float(rng.uniform(w, e)), float(rng.uniform(s, n))]},
                          "properties": {"kind": "infrastructure", key: c, "name": f"{INFRA_CLASSES[c]} {k + 1} (synthetic)"}})
    return {"type": "FeatureCollection", "features": feats, "source": "synthetic (offline)", "synthetic": True}


# ================================================================ FEMA NFHL
def fema_flood_zones(bbox: list[float]) -> dict:
    s = get_settings()
    if s.offline:
        return {"type": "FeatureCollection", "features": [], "source": "offline: FEMA not fetched", "synthetic": True}

    def fetch():
        import httpx

        params = {
            "geometry": ",".join(map(str, bbox)), "geometryType": "esriGeometryEnvelope",
            "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
            "outFields": "FLD_ZONE,ZONE_SUBTY,SFHA_TF", "returnGeometry": "true",
            "outSR": 4326, "geometryPrecision": 5, "maxAllowableOffset": 0.0005,
            "where": "SFHA_TF='T'", "f": "geojson", "resultRecordCount": 4000,
        }
        url = f"{s.fema_nfhl_url}/{FEMA_FLOOD_ZONE_LAYER}/query"
        r = httpx.get(url, params=params, headers=UA, timeout=120)
        r.raise_for_status()
        return r.json()

    try:
        gj = _cached_json("fema", _ckey([round(v, 4) for v in bbox]), fetch)
        gj["source"] = "FEMA NFHL (Special Flood Hazard Areas)"
        return gj
    except Exception as e:
        log.warning("FEMA NFHL failed (%s)", e)
        return {"type": "FeatureCollection", "features": [], "source": "FEMA unavailable", "error": str(e)}


def rasterize_polygons(gj: dict, grid: Grid) -> np.ndarray:
    """Binary mask of polygon features on the grid (PIL, fast, no GDAL)."""
    from PIL import Image, ImageDraw

    img = Image.new("L", (grid.width, grid.height), 0)
    d = ImageDraw.Draw(img)

    def px(pt):
        return ((pt[0] - grid.west) / grid.res, (grid.north - pt[1]) / grid.res)

    for f in gj.get("features", []):
        g = f.get("geometry") or {}
        polys = [g["coordinates"]] if g.get("type") == "Polygon" else g.get("coordinates", []) if g.get("type") == "MultiPolygon" else []
        for poly in polys:
            if poly:
                d.polygon([px(p) for p in poly[0]], fill=1)
                for hole in poly[1:]:
                    d.polygon([px(p) for p in hole], fill=0)
    return np.asarray(img, dtype=bool)


# ===================================================================== DEM
def _tile_xy(lon, lat, z):
    n = 2 ** z
    x = (lon + 180) / 360 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def _fetch_tile(z, x, y) -> np.ndarray:
    import httpx
    from PIL import Image

    s = get_settings()
    p = s.cache_dir / "dem" / f"{z}_{x}_{y}.png"
    if not p.exists():
        r = httpx.get(s.dem_tile_url.format(z=z, x=x, y=y), headers=UA, timeout=30)
        r.raise_for_status()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(r.content)
    a = np.asarray(Image.open(io.BytesIO(p.read_bytes())).convert("RGB"), dtype=np.float32)
    return a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768  # Terrarium encoding


def dem_on_grid(grid: Grid) -> tuple[np.ndarray, str]:
    s = get_settings()
    if not s.offline:
        try:
            return _dem_from_tiles(grid), "AWS Terrain Tiles (Terrarium)"
        except Exception as e:
            log.warning("DEM tiles failed (%s); synthetic DEM", e)
    return synthetic_dem(grid), "synthetic DEM (offline)"


def _dem_from_tiles(grid: Grid, max_tiles: int = 64) -> np.ndarray:
    from scipy.ndimage import map_coordinates

    z = int(np.clip(math.floor(math.log2(360 / (256 * grid.res))), 4, 12))
    while True:
        x0, y0 = _tile_xy(grid.west, grid.north, z)
        x1, y1 = _tile_xy(grid.east, grid.south, z)
        tx = range(int(x0), int(x1) + 1)
        ty = range(int(y0), int(y1) + 1)
        if len(tx) * len(ty) <= max_tiles or z <= 4:
            break
        z -= 1
    mosaic = np.vstack([np.hstack([_fetch_tile(z, x, y) for x in tx]) for y in ty])
    lon, lat = grid.mesh()
    n = 2 ** z
    px = ((lon + 180) / 360 * n - tx[0]) * 256
    py = ((1 - np.arcsinh(np.tan(np.radians(lat))) / np.pi) / 2 * n - ty[0]) * 256
    return map_coordinates(mosaic, [py - 0.5, px - 0.5], order=1, mode="nearest").astype(np.float32)


def synthetic_dem(grid: Grid) -> np.ndarray:
    lon, lat = grid.mesh()
    rng = np.random.default_rng(_seed("dem"))
    t1 = _texture(lon, lat, rng, n=10, kmin=3, kmax=30)
    t2 = _texture(lon, lat, np.random.default_rng(_seed("dem2")), n=10, kmin=20, kmax=120)
    # Regional tilt: NJ drains SE to the Atlantic, Iowa drains to the Mississippi (E).
    tilt = np.where(lon > -80, 120 * (lat - 39) + 60 * (-74 - lon), 80 * (-90 - lon) + 30 * (lat - 40))
    return (200 + tilt + 60 * t1 + 12 * t2).astype(np.float32)


def cache_dir_size() -> int:
    p: Path = get_settings().cache_dir
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
