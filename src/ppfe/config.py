"""Runtime configuration.

Everything is driven by environment variables (or a `.env` file in the repo
root) so the same code runs on a laptop, a GPU workstation, or a cloud host.
Defaults = fully local, no network-required data, CPU/GPU auto-detected.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _bool(key: str, default: bool) -> bool:
    return _env(key, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # --- role ---------------------------------------------------------------
    # "app"    = full portal (API + static frontend)
    # "worker" = inference-only service (for a cloud GPU box); exposes /v1/infer
    role: str = "app"

    # --- paths --------------------------------------------------------------
    data_dir: Path = REPO_ROOT / "data"
    aoi_dir: Path = REPO_ROOT / "aois"
    model_dir: Path = REPO_ROOT / "models"
    frontend_dir: Path = REPO_ROOT / "frontend"

    # --- HRRR ingest --------------------------------------------------------
    # "aws"       = NOAA HRRR on AWS Open Data via Herbie (needs `pip install .[hrrr]`)
    # "local"     = read pre-staged GRIB2/NetCDF from PPFE_HRRR_LOCAL_DIR
    # "synthetic" = generated HRRR-like fields (offline dev / CI)
    # "auto"      = aws -> local -> synthetic, first that works
    hrrr_source: str = "auto"
    hrrr_local_dir: Path = REPO_ROOT / "data" / "hrrr_local"
    hrrr_grid_deg: float = 0.03  # regular lat/lon staging grid (~3 km)

    # --- model / inference --------------------------------------------------
    # model: registry key ("placeholder") or import path "pkg.module:ClassName"
    model: str = "placeholder"
    # "local"  = run in-process (ONNX Runtime / torch, GPU if available)
    # "remote" = POST tensors to a PPFE worker (PPFE_REMOTE_URL)
    inference_backend: str = "local"
    remote_url: str = ""
    remote_token: str = ""
    device: str = "auto"  # auto | cpu | cuda | dml
    tile_size: int = 256
    tile_overlap: int = 16
    tile_batch: int = 8

    # --- external layers ----------------------------------------------------
    offline: bool = False  # skip Overpass/FEMA/DEM fetches, use synthetic stand-ins
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    fema_nfhl_url: str = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer"
    dem_tile_url: str = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"

    # --- auth ---------------------------------------------------------------
    jwt_secret: str = ""
    jwt_ttl_hours: int = 72
    allow_signup: bool = True

    # --- serving ------------------------------------------------------------
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
    max_workers: int = 2

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ppfe.sqlite3"


@lru_cache
def get_settings() -> Settings:
    _load_dotenv(REPO_ROOT / ".env")
    s = Settings(
        role=_env("PPFE_ROLE", "app"),
        data_dir=Path(_env("PPFE_DATA_DIR", str(REPO_ROOT / "data"))),
        aoi_dir=Path(_env("PPFE_AOI_DIR", str(REPO_ROOT / "aois"))),
        model_dir=Path(_env("PPFE_MODEL_DIR", str(REPO_ROOT / "models"))),
        hrrr_source=_env("PPFE_HRRR_SOURCE", "auto"),
        hrrr_local_dir=Path(_env("PPFE_HRRR_LOCAL_DIR", str(REPO_ROOT / "data" / "hrrr_local"))),
        hrrr_grid_deg=float(_env("PPFE_HRRR_GRID_DEG", "0.03")),
        model=_env("PPFE_MODEL", "placeholder"),
        inference_backend=_env("PPFE_INFERENCE_BACKEND", "local"),
        remote_url=_env("PPFE_REMOTE_URL", ""),
        remote_token=_env("PPFE_REMOTE_TOKEN", ""),
        device=_env("PPFE_DEVICE", "auto"),
        tile_size=int(_env("PPFE_TILE_SIZE", "256")),
        tile_overlap=int(_env("PPFE_TILE_OVERLAP", "16")),
        tile_batch=int(_env("PPFE_TILE_BATCH", "8")),
        offline=_bool("PPFE_OFFLINE", False),
        overpass_url=_env("PPFE_OVERPASS_URL", "https://overpass-api.de/api/interpreter"),
        fema_nfhl_url=_env(
            "PPFE_FEMA_NFHL_URL",
            "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer",
        ),
        dem_tile_url=_env(
            "PPFE_DEM_TILE_URL",
            "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png",
        ),
        jwt_secret=_env("PPFE_JWT_SECRET", ""),
        jwt_ttl_hours=int(_env("PPFE_JWT_TTL_HOURS", "72")),
        allow_signup=_bool("PPFE_ALLOW_SIGNUP", True),
        cors_origins=[o.strip() for o in _env("PPFE_CORS_ORIGINS", "*").split(",") if o.strip()],
        max_workers=int(_env("PPFE_MAX_WORKERS", "2")),
    )
    for d in (s.data_dir, s.runs_dir, s.cache_dir, s.model_dir):
        d.mkdir(parents=True, exist_ok=True)
    if not s.jwt_secret:
        # Persist a generated secret locally so logins survive restarts.
        p = s.data_dir / ".jwt_secret"
        if not p.exists():
            p.write_text(secrets.token_urlsafe(48))
        s.jwt_secret = p.read_text().strip()
    return s
