"""Run orchestration: fetch/stage HRRR -> FM inference -> decision layers -> serve.

Each run gets data/runs/<id>/ with:
  meta.json      params, timings, model card, stats, layer index
  arrays.npz     model outputs (fine grid) + hazard/runoff/D8 (analysis grid)
  *.png          map overlays
  *.tif          GeoTIFFs (if rasterio installed)
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np

from . import render
from .config import get_settings
from .data import external
from .data.aoi import get_aoi
from .data.grid import Grid
from .data.hrrr import fetch_and_stage
from .decision.flow import flow_routing
from .decision.hazard import HazardWeights, classify, hazard_index, summarize
from .models.registry import get_engine, input_spec, output_spec

log = logging.getLogger(__name__)

MAX_ANALYSIS_CELLS = 200_000
FEMA_MAX_DEG2 = 1.5   # statewide AOIs skip FEMA vector fetch (too heavy); map still shows FEMA tiles

_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_pool: ThreadPoolExecutor | None = None


def _executor() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=get_settings().max_workers, thread_name_prefix="ppfe-run")
    return _pool


def resample(arr: np.ndarray, src: Grid, dst: Grid) -> np.ndarray:
    from scipy.ndimage import map_coordinates

    X, Y = dst.mesh()
    cols = (X - src.west) / src.res - 0.5
    rows = (src.north - Y) / src.res - 0.5
    return map_coordinates(arr, [rows, cols], order=1, mode="nearest").astype(np.float32)


def analysis_grid(bbox, fine_res: float) -> Grid:
    w, s, e, n = bbox
    res = max(fine_res, math.sqrt((e - w) * (n - s) / MAX_ANALYSIS_CELLS))
    return Grid(w, s, e, n, res)


# ------------------------------------------------------------------ job API
def submit(params: dict, user_id: int | None = None) -> str:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    with _lock:
        _jobs[run_id] = {"id": run_id, "status": "queued", "step": "queued", "progress": 0,
                         "params": params, "user_id": user_id}
    _executor().submit(_run_safe, run_id, params)
    return run_id


def status(run_id: str) -> dict | None:
    with _lock:
        if run_id in _jobs:
            return dict(_jobs[run_id])
    p = get_settings().runs_dir / run_id / "meta.json"
    if p.exists():
        return json.loads(p.read_text())
    return None


def _update(run_id: str, **kw):
    with _lock:
        _jobs[run_id].update(kw)


def _run_safe(run_id: str, params: dict):
    try:
        meta = execute(run_id, params, progress=lambda step, pct: _update(run_id, status="running", step=step, progress=pct))
        _update(run_id, **meta)
    except Exception as e:
        log.exception("run %s failed", run_id)
        _update(run_id, status="error", error=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-2000:])


# --------------------------------------------------------------- the run
def execute(run_id: str, params: dict, progress=lambda s, p: None) -> dict:
    s = get_settings()
    out_dir = s.runs_dir / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    T: dict[str, float] = {}
    t_all = time.perf_counter()

    # ---- resolve AOI + grids
    if params.get("aoi_id"):
        aoi = get_aoi(params["aoi_id"])
        bbox, aoi_name = aoi["bbox"], aoi["name"]
    else:
        bbox, aoi_name = params["bbox"], "Custom extent"
    date = params["date"]
    cycle = int(params.get("cycle", 12))
    fxx = int(params.get("fxx", 1))

    stage = Grid.from_bbox(bbox, s.hrrr_grid_deg)
    names = input_spec()

    # ---- 1. fetch + stage HRRR
    progress("Fetching HRRR", 10)
    t = time.perf_counter()
    x, fetch_meta = fetch_and_stage(date, cycle, fxx, stage, names, params.get("source"))
    T["fetch_stage_s"] = round(time.perf_counter() - t, 3)

    # ---- 2. foundation-model inference
    progress("Running model", 35)
    engine = get_engine()
    y, infer_meta = engine.run(x)
    T["inference_s"] = infer_meta["infer_s"]
    out_names, sf = output_spec()
    fine = stage.refine(sf)
    pred = {n: y[i] for i, n in enumerate(out_names)}
    if "apcp_ds" in pred:
        pred["apcp_ds"] = np.maximum(pred["apcp_ds"], 0)

    # ---- 3. context + decision layers on an analysis grid
    progress("Flow routing", 55)
    t = time.perf_counter()
    ag = analysis_grid([stage.west, stage.south, stage.east, stage.north], fine.res)
    rain_key = "apcp_ds" if "apcp_ds" in pred else out_names[0]
    rain_a = resample(pred[rain_key], fine, ag)
    dem, dem_src = external.dem_on_grid(ag)
    flow = flow_routing(dem, rain_a, ag)
    T["flow_routing_s"] = round(time.perf_counter() - t, 3)

    progress("Flood zones + hazard", 72)
    t = time.perf_counter()
    area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
    fema_mask, fema_src = None, "skipped (AOI too large for vector fetch)"
    if area <= FEMA_MAX_DEG2 and params.get("use_fema", True):
        fz = external.fema_flood_zones(bbox)
        fema_src = fz.get("source", "")
        if fz.get("features"):
            fema_mask = external.rasterize_polygons(fz, ag)
    w = HazardWeights(**params.get("hazard_weights", {}))
    hz = hazard_index(rain_a, flow["runoff_m3"], fema_mask, w)
    T["hazard_s"] = round(time.perf_counter() - t, 3)

    # ---- 4. infrastructure exposure
    progress("Infrastructure exposure", 82)
    t = time.perf_counter()
    infra = external.osm_layer("infrastructure", bbox)
    exposed = []
    for f in infra.get("features", []):
        lon, lat = f["geometry"]["coordinates"]
        if not (ag.west <= lon <= ag.east and ag.south <= lat <= ag.north):
            continue
        h = ag.sample(hz, lon, lat)
        if h >= 0.35:
            exposed.append({"id": f.get("id"), "name": f["properties"].get("name") or "(unnamed)",
                            "class": external.INFRA_CLASSES.get(external.infra_class(f["properties"]), "Other"),
                            "lon": lon, "lat": lat, "hazard": round(h, 3), "level": classify(h)})
    exposed.sort(key=lambda r: -r["hazard"])
    T["exposure_s"] = round(time.perf_counter() - t, 3)

    # ---- 5. write outputs
    progress("Rendering layers", 92)
    t = time.perf_counter()
    layers = {}

    def add_layer(key, arr, ramp, grid, title):
        (out_dir / f"{key}.png").write_bytes(render.png_bytes(arr, ramp, grid))
        layers[key] = {"title": title, "png": f"{key}.png", "corners": grid.corners(),
                       "legend": render.legend(ramp), "grid": grid.to_dict()}
        render.write_geotiff(out_dir / f"{key}.tif", arr, grid, key)

    if "apcp_ds" in pred:
        add_layer("apcp_ds", pred["apcp_ds"], "precip", fine, "Downscaled precipitation (model)")
        add_layer("apcp_hrrr", x[names.index("apcp")], "precip", stage, "HRRR precipitation (input, 3 km)")
    if "tmp2m_ds" in pred:
        add_layer("tmp2m_ds", pred["tmp2m_ds"], "temp", fine, "Downscaled 2 m temperature (model)")
    add_layer("runoff", flow["runoff_m3"], "runoff", ag, "Runoff accumulation (flow routing)")
    add_layer("hazard", hz, "hazard", ag, "Flood hazard index")

    np.savez_compressed(out_dir / "arrays.npz", **{f"pred_{k}": v for k, v in pred.items()},
                        hazard=hz, runoff=flow["runoff_m3"].astype(np.float32), d8=flow["dir"],
                        rain_analysis=rain_a)
    T["render_s"] = round(time.perf_counter() - t, 3)
    T["total_s"] = round(time.perf_counter() - t_all, 3)

    def stat(a):
        return {"min": round(float(a.min()), 3), "max": round(float(a.max()), 3), "mean": round(float(a.mean()), 3)}

    meta = {
        "id": run_id, "status": "done", "step": "done", "progress": 100,
        "created": datetime.now(timezone.utc).isoformat(),
        "params": {**params, "cycle": cycle, "fxx": fxx},
        "aoi": {"name": aoi_name, "bbox": bbox},
        "init_time": f"{date}T{cycle:02d}:00Z",
        "valid_time": (datetime.strptime(date, "%Y-%m-%d") + timedelta(hours=cycle + fxx)).strftime("%Y-%m-%dT%H:00Z"),
        "hrrr": {**fetch_meta, "inputs": names, "staging_grid": stage.to_dict()},
        "model": engine.info() if get_settings().inference_backend == "local" else {"backend": "remote"},
        "inference": infer_meta,
        "grids": {"fine": fine.to_dict(), "analysis": ag.to_dict()},
        "context_sources": {"dem": dem_src, "fema": fema_src, "infrastructure": infra.get("source", "")},
        "hazard": {**summarize(hz), "weights": w.to_dict()},
        "stats": {k: stat(v) for k, v in pred.items()},
        "exposed_infrastructure": exposed[:200],
        "layers": layers,
        "timings": T,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
    return meta


def load_arrays(run_id: str):
    s = get_settings()
    meta = json.loads((s.runs_dir / run_id / "meta.json").read_text())
    arr = np.load(s.runs_dir / run_id / "arrays.npz")
    return meta, arr
