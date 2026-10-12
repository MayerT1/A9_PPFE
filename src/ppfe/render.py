"""Array -> RGBA PNG overlays with legends (no matplotlib dependency)."""
from __future__ import annotations

import io

import numpy as np
from PIL import Image

# (value, (r,g,b,a)) stops; values in data units after `transform`.
RAMPS = {
    "precip": {
        "label": "1 h precipitation (mm)",
        "stops": [(0.1, (190, 230, 255, 0)), (0.5, (150, 210, 250, 150)), (2, (60, 150, 230, 190)),
                  (5, (30, 90, 200, 210)), (10, (90, 50, 190, 225)), (20, (190, 40, 170, 235)),
                  (40, (250, 120, 200, 245))],
    },
    "temp": {
        "label": "2 m temperature (°C)",
        "transform": lambda a: a - 273.15,
        "stops": [(-20, (49, 54, 149, 180)), (-5, (116, 173, 209, 180)), (5, (224, 243, 248, 170)),
                  (15, (254, 224, 144, 170)), (25, (244, 109, 67, 180)), (35, (165, 0, 38, 190))],
    },
    "hazard": {
        "label": "Flood hazard index (0–1)",
        "stops": [(0.15, (255, 237, 160, 0)), (0.25, (255, 237, 160, 110)), (0.35, (254, 178, 76, 170)),
                  (0.55, (240, 59, 32, 205)), (0.75, (160, 0, 40, 230)), (1.0, (90, 0, 30, 240))],
    },
    "runoff": {
        "label": "Upstream runoff volume (m³, log)",
        "transform": lambda a: np.log10(np.maximum(a, 1)),
        "stops": [(3.5, (120, 220, 255, 0)), (4.0, (120, 220, 255, 90)), (5.0, (40, 150, 240, 170)),
                  (6.0, (20, 70, 200, 220)), (7.0, (10, 20, 120, 245))],
    },
}


def colorize(arr: np.ndarray, ramp: str) -> np.ndarray:
    spec = RAMPS[ramp]
    a = spec.get("transform", lambda v: v)(arr.astype(np.float32))
    vals = np.array([s[0] for s in spec["stops"]], np.float32)
    cols = np.array([s[1] for s in spec["stops"]], np.float32)
    out = np.zeros(a.shape + (4,), np.float32)
    for ch in range(4):
        out[..., ch] = np.interp(a, vals, cols[:, ch])
    out[a < vals[0]] = 0
    out[~np.isfinite(a)] = 0
    return out.astype(np.uint8)


def to_mercator_rows(arr: np.ndarray, grid) -> np.ndarray:
    """Re-space rows uniformly in Web-Mercator y so a 4-corner image overlay
    on a Mercator map lines up exactly (lat/lon rows are not linear in y)."""
    H = arr.shape[0]
    merc = lambda lat: np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))  # noqa: E731
    y = np.linspace(merc(grid.north), merc(grid.south), H, endpoint=False) + (merc(grid.south) - merc(grid.north)) / (2 * H)
    lat = np.degrees(2 * np.arctan(np.exp(y)) - np.pi / 2)
    src = np.clip(((grid.north - lat) / grid.res - 0.5).round().astype(int), 0, H - 1)
    return arr[src]


def png_bytes(arr: np.ndarray, ramp: str, grid=None) -> bytes:
    if grid is not None:
        arr = to_mercator_rows(arr, grid)
    buf = io.BytesIO()
    Image.fromarray(colorize(arr, ramp), "RGBA").save(buf, "PNG", optimize=False, compress_level=3)
    return buf.getvalue()


def legend(ramp: str) -> dict:
    spec = RAMPS[ramp]
    return {
        "label": spec["label"],
        "stops": [{"value": v, "color": f"rgba({c[0]},{c[1]},{c[2]},{max(c[3], 200) / 255:.2f})"} for v, c in spec["stops"]],
    }


def write_geotiff(path, arr: np.ndarray, grid, name: str) -> bool:
    """Optional COG/GeoTIFF export if rasterio is installed (pip install -e .[geo])."""
    try:
        import rasterio
        from rasterio.transform import from_origin
    except ImportError:
        return False
    profile = dict(driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1, dtype="float32",
                   crs="EPSG:4326", transform=from_origin(grid.west, grid.north, grid.res, grid.res),
                   compress="deflate", tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr.astype(np.float32), 1)
        dst.set_band_description(1, name)
    return True
