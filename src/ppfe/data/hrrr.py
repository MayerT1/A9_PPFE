"""HRRR fetch + stage onto the regular AOI grid.

Sources (PPFE_HRRR_SOURCE):
  aws       NOAA HRRR on AWS Open Data (s3://noaa-hrrr-bdp-pds) via Herbie.
            Herbie byte-range downloads only the GRIB messages we need and
            keeps them in data/cache/herbie, so repeat runs are local.
  local     Pre-staged NetCDF in PPFE_HRRR_LOCAL_DIR named
            hrrr_{YYYYMMDD}_t{HH}z_f{FF}.nc with 2-D latitude/longitude and
            one variable per platform name (see scripts/stage_hrrr.py).
  synthetic HRRR-like analytic fields (offline / CI).
  auto      aws -> local -> synthetic.

Every result is regridded to a regular lat/lon Grid and cached as .npz,
so the second request for the same date/cycle/fxx/AOI is ~instant.

Notes
- HRRR APCP is absent at f00; fxx >= 1 is required for "apcp".
- HRRR 10 m U/V are grid-relative (Lambert), not earth-relative. Fine for the
  placeholder; rotate before feeding a model that cares.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

import numpy as np

from ..config import get_settings
from .grid import Grid
from .synthetic import synthetic_hrrr

log = logging.getLogger(__name__)

# platform name -> (Herbie search regex, unit conversion)
HERBIE_SEARCH = {
    "apcp": (":APCP:surface:{fm1}-{fxx} hour acc", lambda a: a),            # kg/m2 == mm
    "prate": (":PRATE:surface", lambda a: a * 3600.0),                       # kg/m2/s -> mm/h
    "tmp2m": (":TMP:2 m above ground", lambda a: a),                         # K
    "u10": (":UGRD:10 m above ground", lambda a: a),
    "v10": (":VGRD:10 m above ground", lambda a: a),
    "refc": (":REFC:entire atmosphere", lambda a: a),                        # dBZ
}


class HRRRUnavailable(RuntimeError):
    pass


def _cache_key(date, cycle, fxx, grid: Grid, names) -> str:
    raw = json.dumps([date, cycle, fxx, grid.to_dict(), names], sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _regrid(lat2d: np.ndarray, lon2d: np.ndarray, field: np.ndarray, grid: Grid) -> np.ndarray:
    """Curvilinear (Lambert) -> regular lat/lon via linear interpolation."""
    from scipy.interpolate import griddata

    lon2d = np.where(lon2d > 180, lon2d - 360, lon2d)
    pad = 3 * grid.res + 0.05
    m = (
        (lon2d >= grid.west - pad) & (lon2d <= grid.east + pad)
        & (lat2d >= grid.south - pad) & (lat2d <= grid.north + pad)
    )
    if m.sum() < 4:
        raise HRRRUnavailable("AOI outside HRRR domain")
    X, Y = grid.mesh()
    out = griddata((lon2d[m], lat2d[m]), field[m], (X, Y), method="linear")
    nn = np.isnan(out)
    if nn.any():
        out[nn] = griddata((lon2d[m], lat2d[m]), field[m], (X[nn], Y[nn]), method="nearest")
    return out.astype(np.float32)


def _from_aws(date, cycle, fxx, grid, names) -> np.ndarray:
    try:
        from herbie import Herbie
    except ImportError as e:
        raise HRRRUnavailable("Herbie not installed (pip install -e .[hrrr])") from e
    s = get_settings()
    H = Herbie(
        f"{date} {cycle:02d}:00", model="hrrr", product="sfc", fxx=fxx,
        save_dir=str(s.cache_dir / "herbie"), verbose=False,
    )
    if H.grib is None:
        raise HRRRUnavailable(f"No HRRR file for {date} t{cycle:02d}z f{fxx:02d}")
    out = []
    for n in names:
        pattern, conv = HERBIE_SEARCH[n]
        ds = H.xarray(pattern.format(fxx=fxx, fm1=max(fxx - 1, 0)), remove_grib=False)
        if isinstance(ds, list):
            ds = ds[0]
        var = list(ds.data_vars)[0]
        out.append(_regrid(ds.latitude.values, ds.longitude.values, conv(ds[var].values), grid))
    return np.stack(out)


def local_path(date: str, cycle: int, fxx: int) -> Path:
    s = get_settings()
    return s.hrrr_local_dir / f"hrrr_{date.replace('-', '')}_t{cycle:02d}z_f{fxx:02d}.nc"


def _from_local(date, cycle, fxx, grid, names) -> np.ndarray:
    p = local_path(date, cycle, fxx)
    if not p.exists():
        raise HRRRUnavailable(f"No local HRRR file {p.name}")
    import xarray as xr

    with xr.open_dataset(p) as ds:
        lat, lon = ds.latitude.values, ds.longitude.values
        return np.stack([_regrid(lat, lon, ds[n].values, grid) for n in names])


def available_local_dates() -> list[str]:
    s = get_settings()
    if not s.hrrr_local_dir.exists():
        return []
    return sorted({p.name.split("_")[1] for p in s.hrrr_local_dir.glob("hrrr_*.nc")})


def fetch_and_stage(date: str, cycle: int, fxx: int, grid: Grid, names: list[str],
                    source: str | None = None) -> tuple[np.ndarray, dict]:
    s = get_settings()
    source = source or s.hrrr_source
    if "apcp" in names and fxx < 1:
        raise ValueError("HRRR APCP needs forecast hour >= 1 (no accumulation at f00)")

    key = _cache_key(date, cycle, fxx, grid, names)
    cpath = s.cache_dir / "staged" / f"{key}.npz"
    if cpath.exists():
        z = np.load(cpath)
        return z["x"], {"source": str(z["source"]), "cached": True, "fetch_s": 0.0}

    order = {"auto": ["aws", "local", "synthetic"]}.get(source, [source])
    if s.offline and source == "auto":
        order = ["local", "synthetic"]
    errors = {}
    t0 = time.perf_counter()
    for src in order:
        try:
            if src == "aws":
                x = _from_aws(date, cycle, fxx, grid, names)
            elif src == "local":
                x = _from_local(date, cycle, fxx, grid, names)
            elif src == "synthetic":
                x = synthetic_hrrr(date, cycle, fxx, grid, names)
            else:
                raise ValueError(f"unknown HRRR source {src}")
            cpath.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(cpath, x=x, source=src)
            return x, {
                "source": src, "cached": False,
                "fetch_s": round(time.perf_counter() - t0, 3), "fallback_errors": errors,
            }
        except HRRRUnavailable as e:
            errors[src] = str(e)
            log.info("HRRR source %s unavailable: %s", src, e)
        except Exception as e:  # network errors etc. -> try next source
            errors[src] = f"{type(e).__name__}: {e}"
            log.warning("HRRR source %s failed: %s", src, e)
    raise HRRRUnavailable(f"All HRRR sources failed: {errors}")
