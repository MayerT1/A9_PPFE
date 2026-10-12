"""Synthetic HRRR-like fields for offline dev, CI, and demos.

Fields are analytic functions of (lon, lat, time) so any AOI/grid sampled for
the same date/cycle/fxx gets a consistent picture. Storm cells are seeded
from the date so different dates look different but are reproducible.
"""
from __future__ import annotations

import hashlib
from datetime import datetime

import numpy as np

from .grid import Grid

# Domain storms are scattered over (covers IA through NJ).
_DOM = (-97.5, 38.0, -73.0, 44.5)


def _seed(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def _texture(lon, lat, rng, n=12, kmin=2.0, kmax=40.0):
    """Sum of random plane waves ~ isotropic multi-scale texture in [-1, 1]."""
    out = np.zeros_like(lon)
    norm = 0.0
    for _ in range(n):
        k = np.exp(rng.uniform(np.log(kmin), np.log(kmax)))
        th = rng.uniform(0, np.pi)
        ph = rng.uniform(0, 2 * np.pi)
        out += np.cos(k * (lon * np.cos(th) + lat * np.sin(th)) + ph) / np.sqrt(k)
        norm += 1 / np.sqrt(k)
    # Normalize by the analytic bound (not the sampled max) so the field is
    # identical no matter which AOI/grid samples it.
    return np.clip(2.0 * out / norm, -1.0, 1.0)


def synthetic_hrrr(date: str, cycle: int, fxx: int, grid: Grid, names: list[str]) -> np.ndarray:
    lon, lat = grid.mesh()
    rng = np.random.default_rng(_seed(date, cycle))
    t_hours = cycle + fxx
    doy = datetime.strptime(date, "%Y-%m-%d").timetuple().tm_yday

    # --- storm cells (anisotropic gaussians drifting east-northeast) -------
    n = 120
    cx = rng.uniform(_DOM[0], _DOM[2], n)
    cy = rng.uniform(_DOM[1], _DOM[3], n)
    amp = rng.gamma(1.6, 6.0, n)                 # peak mm/h
    sx = rng.uniform(0.08, 0.6, n)
    sy = sx * rng.uniform(0.35, 1.0, n)
    th = rng.uniform(0, np.pi, n)
    vx, vy = 0.35, 0.12                          # deg/h drift
    rate = np.zeros_like(lon)
    for i in range(n):
        x = lon - (cx[i] + vx * fxx)
        y = lat - (cy[i] + vy * fxx)
        xr = x * np.cos(th[i]) + y * np.sin(th[i])
        yr = -x * np.sin(th[i]) + y * np.cos(th[i])
        rate += amp[i] * np.exp(-0.5 * ((xr / sx[i]) ** 2 + (yr / sy[i]) ** 2))
    tex = _texture(lon, lat, np.random.default_rng(_seed(date, cycle, "tex")))
    rate = np.clip(rate * (1 + 0.45 * tex), 0, None)
    rate[rate < 0.1] = 0.0

    apcp = rate * rng.uniform(0.8, 1.0)          # ~1 h accumulation
    refc = np.where(rate > 0.05, 10 * np.log10(200 * np.maximum(rate, 1e-3) ** 1.6), -10.0)

    # --- temperature: seasonal + latitudinal + diurnal + rain cooling ------
    season = 12 * np.cos(2 * np.pi * (doy - 200) / 365)
    diurnal = 5 * np.cos(2 * np.pi * (t_hours - 21) / 24)
    tmp = 283 + season - 0.9 * (lat - 41) + diurnal - 0.5 * np.sqrt(rate) + 1.2 * tex

    # --- winds: smooth large-scale flow + storm outflow --------------------
    t2 = _texture(lon, lat, np.random.default_rng(_seed(date, cycle, "wind")), n=6, kmax=6)
    u = 6 + 4 * t2 + 0.3 * rate
    t3 = _texture(lon, lat, np.random.default_rng(_seed(date, cycle, "wind2")), n=6, kmax=6)
    v = 2 - 3 * t3

    fields = {
        "apcp": apcp, "prate": rate, "tmp2m": tmp, "u10": u, "v10": v, "refc": refc,
    }
    return np.stack([fields[n].astype(np.float32) for n in names])
