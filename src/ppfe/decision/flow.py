"""Hydrologic flow routing: depression fill + D8 + rainfall-weighted accumulation.

Pure NumPy + heapq; no GDAL/WhiteboxTools needed. Grid sizes are capped by the
pipeline (~250k cells) so this stays well under a second or two.
"""
from __future__ import annotations

import heapq

import numpy as np

from ..data.grid import Grid

# D8 neighbour offsets, index 0..7 (E, SE, S, SW, W, NW, N, NE)
DR = np.array([0, 1, 1, 1, 0, -1, -1, -1])
DC = np.array([1, 1, 0, -1, -1, -1, 0, 1])
DIST = np.array([1, 1.4142, 1, 1.4142, 1, 1.4142, 1, 1.4142])


def priority_flood(dem: np.ndarray, eps: float = 1e-4) -> np.ndarray:
    """Barnes et al. (2014) priority-flood with epsilon so every cell drains.

    Flat-index pure-Python loop over lists (much faster than numpy scalar
    indexing). ~1 s per 150k cells.
    """
    H, W = dem.shape
    a = dem.astype(np.float64)
    if np.isnan(a).any():
        a = np.where(np.isnan(a), np.nanmin(a) if np.isfinite(a).any() else 0.0, a)
    z = a.ravel().tolist()
    done = bytearray(H * W)
    pq: list = []
    edge = set(range(W)) | set(range((H - 1) * W, H * W)) | set(range(0, H * W, W)) | set(range(W - 1, H * W, W))
    for i in edge:
        pq.append((z[i], i))
        done[i] = 1
    heapq.heapify(pq)
    offs = [(int(dr), int(dc)) for dr, dc in zip(DR, DC, strict=False)]
    push, pop = heapq.heappush, heapq.heappop
    while pq:
        e, i = pop(pq)
        r, c = divmod(i, W)
        for dr, dc in offs:
            rr, cc = r + dr, c + dc
            if 0 <= rr < H and 0 <= cc < W:
                j = rr * W + cc
                if not done[j]:
                    done[j] = 1
                    if z[j] <= e:
                        z[j] = e + eps
                    push(pq, (z[j], j))
    return np.asarray(z).reshape(H, W)


def d8_direction(z: np.ndarray) -> np.ndarray:
    """Index 0..7 of steepest descent neighbour, -1 = outlet/edge sink."""
    H, W = z.shape
    zp = np.pad(z, 1, mode="constant", constant_values=np.inf)
    best = np.full((H, W), -1, np.int8)
    best_slope = np.zeros((H, W))
    for k in range(8):
        nb = zp[1 + DR[k]:1 + DR[k] + H, 1 + DC[k]:1 + DC[k] + W]
        slope = (z - nb) / DIST[k]
        m = slope > best_slope
        best[m] = k
        best_slope[m] = slope[m]
    # Edge cells with no downhill neighbour drain off-grid.
    return best


def accumulate(direction: np.ndarray, z: np.ndarray, weight: np.ndarray) -> np.ndarray:
    """Sum `weight` over each cell's upstream area (inclusive)."""
    H, W = direction.shape
    acc = weight.astype(np.float64).ravel().copy()
    order = np.argsort(-z.ravel(), kind="stable")  # high -> low
    d = direction.ravel()
    tgt = np.full(H * W, -1, np.int64)
    valid = d >= 0
    idx = np.nonzero(valid)[0]
    r, c = np.divmod(idx, W)
    rr, cc = r + DR[d[idx]], c + DC[d[idx]]
    inside = (rr >= 0) & (rr < H) & (cc >= 0) & (cc < W)
    tgt[idx[inside]] = rr[inside] * W + cc[inside]
    a, tl = acc.tolist(), tgt.tolist()
    for i in order.tolist():  # tight loop; ~0.1 s per 250k cells
        t = tl[i]
        if t >= 0:
            a[t] += a[i]
    return np.asarray(a).reshape(H, W)


def flow_routing(dem: np.ndarray, rain_mm: np.ndarray, grid: Grid, runoff_coeff: float = 0.5) -> dict:
    """Returns filled DEM, D8 dir, and upstream runoff volume (m^3) per cell."""
    z = priority_flood(dem)
    d = d8_direction(z)
    lat = np.deg2rad(grid.lats)[:, None]
    cell_m2 = (grid.res * 111_320) ** 2 * np.cos(lat) * np.ones_like(dem)
    vol = runoff_coeff * np.maximum(rain_mm, 0) * 1e-3 * cell_m2   # m^3 per cell
    acc_vol = accumulate(d, z, vol)
    acc_area = accumulate(d, z, cell_m2) / 1e6                    # km^2
    return {"filled": z, "dir": d, "runoff_m3": acc_vol, "area_km2": acc_area}


def trace_downstream(direction: np.ndarray, grid: Grid, lon: float, lat: float, max_steps: int = 20000):
    """D8 path from a point to the grid edge / outlet, as [[lon, lat], ...]."""
    H, W = direction.shape
    r, c = grid.index(lon, lat)
    path = [list(grid.cell_center(r, c))]
    seen = set()
    for _ in range(max_steps):
        k = direction[r, c]
        if k < 0 or (r, c) in seen:
            break
        seen.add((r, c))
        r, c = r + DR[k], c + DC[k]
        if not (0 <= r < H and 0 <= c < W):
            break
        path.append(list(grid.cell_center(r, c)))
    return path
