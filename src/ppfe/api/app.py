"""FastAPI application.

    uvicorn ppfe.api.app:app --port 8000        # portal (API + frontend)
    PPFE_ROLE=worker uvicorn ppfe.api.app:app   # inference-only worker (/v1/*)
"""
from __future__ import annotations

import io
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import jwt
import numpy as np
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__, pipeline, report, store
from ..config import get_settings
from ..data import external
from ..data.aoi import list_aois
from ..data.grid import Grid
from ..data.hrrr import available_local_dates
from ..decision import roads
from ..decision.flow import trace_downstream
from ..models.registry import get_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ppfe")
S = get_settings()

@asynccontextmanager
async def lifespan(_app):
    store.init_db()
    if S.inference_backend == "local":
        # Warm the model so the first user run isn't paying load/compile cost.
        t = time.perf_counter()
        info = get_engine().model.runtime_info
        log.info("Model warm in %.2fs (%s)", time.perf_counter() - t, info)
    yield


app = FastAPI(lifespan=lifespan, title="A9_PPFE", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=S.cors_origins, allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["x-ppfe-timing"])
app.add_middleware(GZipMiddleware, minimum_size=2048)


@app.middleware("http")
async def private_network_access(request: Request, call_next):
    """Lets the GitHub Pages UI (public https origin) call a backend on
    localhost / the lab LAN: Chrome's Private Network Access preflight needs this."""
    resp = await call_next(request)
    if request.headers.get("access-control-request-private-network"):
        resp.headers["Access-Control-Allow-Private-Network"] = "true"
    return resp


# =================================================================== auth
class Signup(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str = ""
    password: str = Field(min_length=8)


class Login(BaseModel):
    email: str
    password: str


def _token(user: dict) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=S.jwt_ttl_hours)
    return jwt.encode({"sub": str(user["id"]), "email": user["email"], "exp": exp}, S.jwt_secret, algorithm="HS256")


def current_user(authorization: str | None = Header(None)) -> dict | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    try:
        payload = jwt.decode(authorization.split(" ", 1)[1], S.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Session expired, please log in again")
    return store.get_user(int(payload["sub"]))


def require_user(user: dict | None = Depends(current_user)) -> dict:
    if not user:
        raise HTTPException(401, "Login required")
    return user


@app.post("/api/auth/signup")
def signup(body: Signup):
    if not S.allow_signup:
        raise HTTPException(403, "Sign-up disabled on this deployment")
    try:
        u = store.create_user(str(body.email), body.name, body.password)
    except Exception:
        raise HTTPException(409, "An account with that email already exists")
    return {"token": _token(u), "user": u}


@app.post("/api/auth/login")
def login(body: Login):
    u = store.auth_user(body.email, body.password)
    if not u:
        raise HTTPException(401, "Wrong email or password")
    return {"token": _token(u), "user": u}


@app.get("/api/auth/me")
def me(user: dict = Depends(require_user)):
    return user


# ============================================================ platform info
@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__, "role": S.role}


@app.get("/api/config")
def config():
    return {
        "version": __version__,
        "hrrr_source": S.hrrr_source,
        "inference_backend": S.inference_backend,
        "offline": S.offline,
        "allow_signup": S.allow_signup,
        "local_hrrr_dates": available_local_dates(),
    }


@app.get("/api/model")
def model_info():
    return get_engine().info()


@app.get("/api/aois")
def aois():
    return list_aois()


# ==================================================================== runs
class RunRequest(BaseModel):
    aoi_id: str | None = None
    bbox: list[float] | None = None
    date: str
    cycle: int = Field(12, ge=0, le=23)
    fxx: int = Field(1, ge=1, le=48)
    source: str | None = None
    use_fema: bool = True


@app.post("/api/runs")
def create_run(body: RunRequest, user: dict | None = Depends(current_user)):
    if not body.aoi_id and not body.bbox:
        raise HTTPException(422, "aoi_id or bbox required")
    try:
        datetime.strptime(body.date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(422, "date must be YYYY-MM-DD")
    rid = pipeline.submit(body.model_dump(exclude_none=True), user["id"] if user else None)
    return {"id": rid}


@app.get("/api/runs/{run_id}")
def run_status(run_id: str):
    st = pipeline.status(run_id)
    if not st:
        raise HTTPException(404, "Unknown run")
    return st


@app.get("/api/runs/{run_id}/files/{name}")
def run_file(run_id: str, name: str):
    if "/" in name or ".." in name or "\\" in name:
        raise HTTPException(400)
    p = S.runs_dir / run_id / name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, headers={"Cache-Control": "public, max-age=86400, immutable"})


def _load(run_id: str):
    try:
        return pipeline.load_arrays(run_id)
    except FileNotFoundError:
        raise HTTPException(404, "Run not finished or unknown")


@app.get("/api/runs/{run_id}/sample")
def sample(run_id: str, lon: float, lat: float):
    meta, arr = _load(run_id)
    fine = Grid(**{k: meta["grids"]["fine"][k] for k in ("west", "south", "east", "north", "res")})
    ag = Grid(**{k: meta["grids"]["analysis"][k] for k in ("west", "south", "east", "north", "res")})
    if not (ag.west <= lon <= ag.east and ag.south <= lat <= ag.north):
        return {"inside": False}
    out = {"inside": True, "lon": lon, "lat": lat}
    for k in arr.files:
        if k.startswith("pred_"):
            out[k[5:]] = round(fine.sample(arr[k], lon, lat), 3)
    out["hazard"] = round(ag.sample(arr["hazard"], lon, lat), 3)
    out["runoff_m3"] = round(ag.sample(arr["runoff"], lon, lat), 1)
    from ..decision.hazard import classify
    out["hazard_class"] = classify(out["hazard"])
    return out


@app.get("/api/runs/{run_id}/trace")
def trace(run_id: str, lon: float, lat: float):
    meta, arr = _load(run_id)
    ag = Grid(**{k: meta["grids"]["analysis"][k] for k in ("west", "south", "east", "north", "res")})
    path = trace_downstream(arr["d8"], ag, lon, lat)
    hz = [ag.sample(arr["hazard"], p[0], p[1]) for p in path]
    ro = [ag.sample(arr["runoff"], p[0], p[1]) for p in path]
    km = len(path) * ag.res * 111.32 * np.cos(np.radians(lat))
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": path},
            "properties": {"cells": len(path), "approx_km": round(float(km), 1),
                           "max_hazard": round(max(hz), 3) if hz else 0,
                           "outlet_runoff_m3": round(ro[-1], 1) if ro else 0}}


class RouteRequest(BaseModel):
    start: list[float]
    end: list[float]
    block_threshold: float = 0.7
    penalty: float = 4.0


@app.post("/api/runs/{run_id}/route")
def route(run_id: str, body: RouteRequest):
    meta, arr = _load(run_id)
    ag = Grid(**{k: meta["grids"]["analysis"][k] for k in ("west", "south", "east", "north", "res")})
    (x0, y0), (x1, y1) = body.start, body.end
    pad = max(0.03, 0.25 * max(abs(x1 - x0), abs(y1 - y0)))
    bbox = [min(x0, x1) - pad, min(y0, y1) - pad, max(x0, x1) + pad, max(y0, y1) + pad]
    if (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) > 2.5:
        raise HTTPException(422, "Route extent too large; pick points closer together (< ~150 km)")
    net = external.osm_layer("roads", bbox)
    try:
        r = roads.route(net, body.start, body.end, arr["hazard"], ag, body.block_threshold, body.penalty)
    except ValueError as e:
        raise HTTPException(422, str(e))
    r["network_source"] = net.get("source")
    return r


# ================================================================== layers
@app.get("/api/layers/{kind}")
def layer(kind: str, bbox: str = Query(..., description="west,south,east,north")):
    b = [float(v) for v in bbox.split(",")]
    if len(b) != 4:
        raise HTTPException(422, "bbox=w,s,e,n")
    if kind == "fema":
        if (b[2] - b[0]) * (b[3] - b[1]) > 1.5:
            raise HTTPException(422, "Zoom in further for FEMA vectors (raster tiles shown instead)")
        return external.fema_flood_zones(b)
    if kind not in ("waterways", "infrastructure", "roads"):
        raise HTTPException(404)
    if kind == "roads" and (b[2] - b[0]) * (b[3] - b[1]) > 0.6:
        raise HTTPException(422, "Zoom in further to load roads")
    gj = external.osm_layer(kind, b)
    if kind == "infrastructure":
        for f in gj["features"]:
            c = external.infra_class(f["properties"])
            f["properties"]["class"] = c
            f["properties"]["label"] = external.INFRA_CLASSES.get(c, "Other")
    return gj


@app.get("/api/tiles/fema/{z}/{x}/{y}.png")
def fema_tile(z: int, x: int, y: int):
    """Caching proxy for FEMA NFHL flood-zone raster tiles (avoids CORS + repeat hits)."""
    if S.offline:
        raise HTTPException(404)
    p = S.cache_dir / "fema_tiles" / f"{z}_{x}_{y}.png"
    if not p.exists():
        import math as _m

        import httpx

        n = 2 ** z
        R = 6378137.0
        span = 2 * _m.pi * R / n
        x0, y1 = -_m.pi * R + x * span, _m.pi * R - y * span
        params = {"bbox": f"{x0},{y1 - span},{x0 + span},{y1}", "bboxSR": 3857, "imageSR": 3857,
                  "size": "256,256", "format": "png32", "transparent": "true",
                  "layers": f"show:{external.FEMA_FLOOD_ZONE_LAYER}", "f": "image"}
        try:
            r = httpx.get(f"{S.fema_nfhl_url}/export", params=params, headers=external.UA, timeout=45)
            r.raise_for_status()
            if not r.headers.get("content-type", "").startswith("image"):
                raise ValueError(r.text[:200])
        except Exception as e:
            raise HTTPException(502, f"FEMA NFHL unavailable: {e}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(r.content)
    return FileResponse(p, media_type="image/png", headers={"Cache-Control": "public, max-age=604800"})


# ================================================================ projects
class ProjectBody(BaseModel):
    name: str
    state: dict


@app.get("/api/projects")
def projects(user: dict = Depends(require_user)):
    return store.list_projects(user["id"])


@app.post("/api/projects")
def create_project(body: ProjectBody, user: dict = Depends(require_user)):
    return store.save_project(user["id"], body.name, body.state)


@app.get("/api/projects/{pid}")
def get_project(pid: int, user: dict = Depends(require_user)):
    p = store.get_project(user["id"], pid)
    if not p:
        raise HTTPException(404)
    return p


@app.put("/api/projects/{pid}")
def update_project(pid: int, body: ProjectBody, user: dict = Depends(require_user)):
    try:
        return store.save_project(user["id"], body.name, body.state, pid)
    except KeyError:
        raise HTTPException(404)


@app.delete("/api/projects/{pid}")
def delete_project(pid: int, user: dict = Depends(require_user)):
    if not store.delete_project(user["id"], pid):
        raise HTTPException(404)
    return {"deleted": pid}


@app.get("/api/projects/{pid}/export.geojson")
def export_project(pid: int, user: dict = Depends(require_user)):
    p = store.get_project(user["id"], pid)
    if not p:
        raise HTTPException(404)
    fc = p["state"].get("annotations") or {"type": "FeatureCollection", "features": []}
    fc = {**fc, "properties": {"project": p["name"], "run_id": p["state"].get("run_id"), "exported": datetime.now(timezone.utc).isoformat()}}
    fname = "".join(ch if ch.isalnum() else "_" for ch in p["name"])[:60] or "project"
    return Response(json.dumps(fc, indent=1), media_type="application/geo+json",
                    headers={"Content-Disposition": f'attachment; filename="{fname}.geojson"'})


# ================================================================== report
class ReportBody(BaseModel):
    title: str = "A9 PPFE situation report"
    notes: str = ""
    run_id: str | None = None
    map_png: str | None = None          # data:image/png;base64,...
    annotations: dict | None = None
    routes: dict | None = None
    layers_on: list[str] | None = None


@app.post("/api/report")
def make_report(body: ReportBody, user: dict | None = Depends(current_user)):
    meta = pipeline.status(body.run_id) if body.run_id else None
    if meta and meta.get("status") != "done":
        meta = None
    pdf = report.build_pdf(title=body.title, author=(user or {}).get("name") or (user or {}).get("email") or "",
                           notes=body.notes, meta=meta, map_png=body.map_png, annotations=body.annotations,
                           routes=body.routes, layers_on=body.layers_on)
    fname = "".join(ch if ch.isalnum() else "_" for ch in body.title)[:60] or "report"
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{fname}.pdf"'})


# ======================================================== worker endpoints
def _worker_auth(authorization: str | None = Header(None)):
    tok = get_settings().remote_token
    if tok and authorization != f"Bearer {tok}":
        raise HTTPException(401)


@app.get("/v1/info", dependencies=[Depends(_worker_auth)])
def worker_info():
    return get_engine().info()


@app.post("/v1/infer", dependencies=[Depends(_worker_auth)])
async def worker_infer(request: Request):
    """Raw tensor in, raw tensor out (np.save format). Always runs locally."""
    x = np.load(io.BytesIO(await request.body()), allow_pickle=False)
    y, timing = get_engine().run_local(x)
    buf = io.BytesIO()
    np.save(buf, y, allow_pickle=False)
    return Response(buf.getvalue(), media_type="application/octet-stream",
                    headers={"x-ppfe-timing": json.dumps({k: v for k, v in timing.items() if k != "runtime"})})


@app.exception_handler(Exception)
async def _err(request: Request, exc: Exception):
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)


# ================================================================ frontend
if S.role == "app" and S.frontend_dir.exists():
    app.mount("/", StaticFiles(directory=S.frontend_dir, html=True), name="frontend")
