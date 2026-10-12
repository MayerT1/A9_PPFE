# A9_PPFE architecture (v0.1)

## Run pipeline (`ppfe.pipeline.execute`)

| Step | Module | Notes |
|---|---|---|
| 1. Resolve AOI → staging grid (regular lat/lon, 0.03°) | `data/aoi.py`, `data/grid.py` | Row 0 is north (image convention). |
| 2. Fetch + stage HRRR | `data/hrrr.py` | Sources tried in order: AWS (Herbie byte-range GRIB) → local NetCDF → synthetic. Lambert grid is regridded to lat/lon. Results are cached as `.npz` by (date, cycle, fxx, grid). |
| 3. FM inference | `models/registry.py` | One warm model per process, reflect-halo tiling, batched tiles. Local, or remote over `/v1/infer`. |
| 4. Analysis grid (≤ 200k cells) | `pipeline.py` | Model rain is resampled onto it. |
| 5. DEM → priority-flood → D8 → rain-weighted runoff accumulation | `data/external.py`, `decision/flow.py` | DEM is AWS Terrarium tiles (cached) or synthetic offline. |
| 6. FEMA NFHL SFHA polygons → raster mask | `data/external.py` | Only for AOIs ≤ 1.5 deg²; statewide AOIs show FEMA as tiles only. |
| 7. Hazard index | `decision/hazard.py` | Rule-based rain + flow + FEMA. This is the extension point for a learned decision model. |
| 8. OSM infrastructure → exposure list | `data/external.py` | Overpass, cached. |
| 9. Render outputs | `render.py` | PNG overlays (rows re-spaced to Web Mercator), optional GeoTIFFs, `arrays.npz`, `meta.json`. |

Measured offline on 2 CPU cores: Passaic basin 0.6 s total; Iowa statewide 1.8 s total (inference 0.03 s, flow routing 1.1 s).

## On-demand services (per run)

- `GET /api/runs/{id}/sample`: point values.
- `GET /api/runs/{id}/trace`: D8 downstream path from a point.
- `POST /api/runs/{id}/route`: OSM road graph in the start/end bbox. Edge time is multiplied by (1 + 4h²) and edges with h ≥ threshold are closed. Returns the fastest and the hazard-aware routes.

## Persistence

SQLite at `data/ppfe.sqlite3`, with `users` and `projects` tables. A project's `state` JSON holds the AOI, run_id, HRRR selection, annotations (GeoJSON), routes, traces, layer settings and the map view. Passwords use PBKDF2-SHA256; sessions are HS256 JWTs. To run several instances, swap `store.py` for Postgres; the interface is a handful of functions.

## Deployment shapes

1. **Laptop / lab PC.** `ppfe serve` serves the UI and API with in-process inference.
2. **Pages UI + per-user local backend.** The Pages build defaults `API_BASE` to `http://127.0.0.1:8000`.
3. **Pages UI + shared cloud backend.** The GHCR image runs on a VM or container service behind HTTPS.
4. **Split inference.** The portal runs on a small CPU host with `PPFE_INFERENCE_BACKEND=remote`; `ppfe worker` runs on the GPU host.

## Known limits / to verify

- **FEMA layer id.** The NFHL MapServer flood-zone layer id is assumed to be **28**. Verify it at `…/NFHL/MapServer?f=json` (`external.FEMA_FLOOD_ZONE_LAYER`).
- **Untested against live services.** The live Overpass, FEMA, Terrarium and HRRR/Herbie paths were written against the public APIs but not exercised from the build sandbox, which had no outbound access. Run `pytest -m integration` once those tests are added, or smoke-test with `ppfe serve` on a networked machine.
- **HRRR winds are grid-relative.** HRRR 10 m U/V are not rotated to earth-relative.
- **Overlay geometry.** Overlays are lat/lon grids re-spaced to Mercator rows. They are exact on Mercator maps; switch to COG tiles (TiTiler) if a non-Mercator view is ever needed.
