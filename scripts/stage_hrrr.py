"""Pre-stage HRRR to local disk (the "local storage" data path).

Downloads only the needed GRIB messages via Herbie and writes one NetCDF per
(date, cycle, fxx) that `PPFE_HRRR_SOURCE=local` reads. Run on any machine with
internet, then copy data/hrrr_local/ to an offline box if needed.

    pip install -e .[hrrr]
    python scripts/stage_hrrr.py --start 2026-09-01 --end 2026-09-03 --cycles 0 12 --fxx 1 2 3
    python scripts/stage_hrrr.py --start 2026-09-01 --bbox -96.7 40.3 -73.8 43.6   # crop to IA..NJ band
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ppfe.data.hrrr import HERBIE_SEARCH, local_path  # noqa: E402


def stage_one(day: str, cycle: int, fxx: int, names, bbox=None, overwrite=False):
    import numpy as np
    import xarray as xr
    from herbie import Herbie

    out = local_path(day, cycle, fxx)
    if out.exists() and not overwrite:
        print("exists", out.name)
        return
    H = Herbie(f"{day} {cycle:02d}:00", model="hrrr", product="sfc", fxx=fxx, verbose=False)
    data, lat, lon = {}, None, None
    for n in names:
        if n == "apcp" and fxx < 1:
            continue
        pattern, conv = HERBIE_SEARCH[n]
        ds = H.xarray(pattern.format(fxx=fxx, fm1=max(fxx - 1, 0)))
        ds = ds[0] if isinstance(ds, list) else ds
        v = list(ds.data_vars)[0]
        lat = ds.latitude.values
        lon = np.where(ds.longitude.values > 180, ds.longitude.values - 360, ds.longitude.values)
        data[n] = conv(ds[v].values).astype("float32")
    if bbox:
        w, s, e, nn = bbox
        m = (lon >= w) & (lon <= e) & (lat >= s) & (lat <= nn)
        rows, cols = np.where(m)
        sl = (slice(rows.min(), rows.max() + 1), slice(cols.min(), cols.max() + 1))
        lat, lon = lat[sl], lon[sl]
        data = {k: a[sl] for k, a in data.items()}
    ds = xr.Dataset({k: (("y", "x"), a) for k, a in data.items()},
                    coords={"latitude": (("y", "x"), lat), "longitude": (("y", "x"), lon)},
                    attrs={"source": "NOAA HRRR via Herbie", "init": f"{day}T{cycle:02d}Z", "fxx": fxx})
    out.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(out, encoding={k: {"zlib": True, "complevel": 4} for k in data})
    print("wrote", out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end")
    ap.add_argument("--cycles", type=int, nargs="+", default=[12])
    ap.add_argument("--fxx", type=int, nargs="+", default=[1])
    ap.add_argument("--vars", nargs="+", default=list(HERBIE_SEARCH))
    ap.add_argument("--bbox", type=float, nargs=4, metavar=("W", "S", "E", "N"))
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args()
    d0 = date.fromisoformat(a.start)
    d1 = date.fromisoformat(a.end) if a.end else d0
    d = d0
    while d <= d1:
        for c in a.cycles:
            for f in a.fxx:
                try:
                    stage_one(d.isoformat(), c, f, a.vars, a.bbox, a.overwrite)
                except Exception as e:  # keep going on missing cycles
                    print(f"FAILED {d} t{c:02d}z f{f:02d}: {e}")
        d += timedelta(days=1)


if __name__ == "__main__":
    main()
