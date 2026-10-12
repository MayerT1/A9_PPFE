# A9_PPFE

A web portal that **hosts a geospatial foundation model for fast HRRR downscaling** and turns its prediction into flood decision support for **New Jersey** and **Iowa**.

The flow is: pick an AOI and a date, fetch HRRR, run the model, view the downscaled prediction over OSM, FEMA flood zones, rivers and infrastructure, route around hazards, annotate, save the workspace and export a PDF.

The model today is an **untrained placeholder** with the real input/output contract. Prithvi-WxC and the downscaling ensemble plug in later through `PPFE_MODEL`; see [docs/developer_guide/model_plugin.md](docs/developer_guide/model_plugin.md).

```
Browser (MapLibre UI)  ──►  FastAPI portal  ──►  HRRR fetch/stage  (AWS via Herbie | local NetCDF | synthetic)
   GitHub Pages or           │                    FM inference      (in-process ONNX Runtime GPU/CPU | remote worker)
   served by the API         │                    Decision layers   (DEM flow routing, FEMA NFHL, hazard, OSM infra)
                             └── SQLite: users + saved workspaces; PDF reports
```

## Quick start (Windows PowerShell, local)

```powershell
cd C:\Users\tjmayer\Documents\GitHub\A9_PPFE
py -3.12 -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e ".[hrrr,dev]"        # drop ,hrrr to skip GRIB deps (synthetic/local data only)
ppfe serve                           # http://127.0.0.1:8000
```

| Command | What it does |
|---|---|
| `ppfe serve --offline` | Runs with no network: synthetic HRRR and synthetic context layers (good for demos and CI). |
| `ppfe bench --size 512` | Times the inference engine (placeholder: about 0.35 s for 512² → 2048² on 2 CPU cores). |
| `pytest` | Runs the unit and API tests (offline). |
| `python scripts\stage_hrrr.py --start 2026-09-01 --cycles 12 --fxx 1 2 3` | Pre-stages HRRR to disk for `PPFE_HRRR_SOURCE=local`. |

On Windows GPUs (any vendor), run `pip uninstall onnxruntime; pip install onnxruntime-directml`, then set `PPFE_DEVICE=dml`. On NVIDIA, use `onnxruntime-gpu` and `PPFE_DEVICE=cuda`.

> **Herbie on Windows.** `cfgrib` needs ecCodes. If `pip install -e .[hrrr]` builds but decoding fails, install ecCodes via conda (`conda install -c conda-forge eccodes`) or run the Docker image. Without it, the portal falls back to local or synthetic data automatically and says so in the run panel.

## Fact sheet and versions

[FACT_SHEET.md](FACT_SHEET.md) is the user-facing description of the tool: what it does, methods, data, limits and version history. It is linked from the portal header (`factsheet.html`) and printable to PDF.

For every patch or release, run:

```powershell
python scripts\update_factsheet.py --bump patch -m "Short description of the change"   # or minor / major
```

This bumps the version, regenerates the AUTO blocks (version, model card, AOIs) and appends to Version history. Edit the prose sections by hand when behavior changes. CI runs `--check` and fails if the fact sheet is stale.

## Local by default, cloud when needed

Everything is controlled by environment variables or `.env`; see [.env.example](.env.example).

| Setting | Local default | Cloud / remote option |
|---|---|---|
| HRRR data `PPFE_HRRR_SOURCE` | `auto`: AWS → local → synthetic | `aws` on a cloud host, or `local` with a staged archive |
| Inference `PPFE_INFERENCE_BACKEND` | `local`: in-process, GPU auto-detected | `remote` + `PPFE_REMOTE_URL`: a GPU worker started with `ppfe worker` or `docker compose --profile gpu up` |
| UI hosting | Served by the API at `/` | GitHub Pages (`pages.yml`), pointing at `vars.PPFE_API_BASE` |
| Container | `docker compose up` | `ghcr.io/<owner>/a9_ppfe` built by `docker.yml` |

### GitHub Pages (`.github/workflows/pages.yml`)

- **Static demo (default).** With no repo variable set, the workflow precomputes the runs in [demo/demo_runs.json](demo/demo_runs.json) on the runner (live HRRR/OSM/DEM, with labelled fallbacks) and publishes them with the UI.
  - Works in the demo: map, layers, point queries, flow tracing, annotations, GeoJSON export and the fact sheet.
  - Needs the backend: new runs, road routing, accounts and PDF.
- **Live mode.** Set the repo variable `PPFE_API_BASE` to an HTTPS backend (the Docker image on a VM or lab server) and add the Pages origin to `PPFE_CORS_ORIGINS`. Every feature is then enabled.

Enable Pages once under **Settings → Pages → Source: GitHub Actions**.

## Features → code

| # | Feature | Where |
|---|---|---|
| 1 | Host a foundation model (placeholder; plugin contract for Prithvi-WxC and the ensemble) | `src/ppfe/models/` |
| 2 | Map and zoom to NJ / IA AOIs. Drop official GeoJSONs into `aois/`; they load without a restart. | `aois/`, `src/ppfe/data/aoi.py` |
| 3 | HRRR date, cycle and forecast-hour picker | UI Run tab |
| 4 | Fetch HRRR from the cloud (AWS + Herbie), local NetCDF, or synthetic; regrid and cache | `src/ppfe/data/hrrr.py` |
| 5 | Stage data, then run inference | `src/ppfe/pipeline.py` |
| 6 | Fast inference: warm model, ONNX Runtime (CUDA/DirectML/CoreML/CPU), halo tiling with batching, ESPCN-style compute at input resolution | `src/ppfe/models/registry.py`, `onnx_runtime.py` |
| 7 | Serve predictions on the map (Mercator-correct PNG overlays, optional GeoTIFF via `.[geo]`) and point queries | `src/ppfe/render.py`, `/api/runs/*` |
| 8 | Overlay OSM rivers and infrastructure and FEMA NFHL; hazard index; road routing that avoids flooded segments; D8 stormwater flow tracing; exposed facilities | `src/ppfe/data/external.py`, `src/ppfe/decision/` |
| 9 | Accounts, saved workspaces, annotations (point/line/area), GeoJSON import/export, PDF report | `src/ppfe/store.py`, `report.py`, UI Annotate and Report tabs |

API docs are at `/api/docs`.

## Data and attribution

- NOAA HRRR (AWS Open Data)
- OpenStreetMap contributors, ODbL (Overpass API)
- FEMA National Flood Hazard Layer
- AWS Terrain Tiles (Terrarium)
- Basemaps: OSM, USGS The National Map, Esri World Imagery
