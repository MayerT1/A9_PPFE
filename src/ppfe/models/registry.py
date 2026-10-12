"""Model registry + inference engine (local tiled/batched, or remote worker).

PPFE_MODEL selects the model:
  "placeholder"                     -> built-in placeholder downscaler
  "my_pkg.prithvi:PrithviWxCModel"  -> any FoundationModel subclass on PYTHONPATH
"""
from __future__ import annotations

import importlib
import io
import logging
import math
import threading
import time

import numpy as np

from ..config import Settings, get_settings
from .base import FoundationModel

log = logging.getLogger(__name__)

_BUILTIN = {"placeholder": "ppfe.models.placeholder:PlaceholderModel"}


def _resolve(spec: str):
    spec = _BUILTIN.get(spec, spec)
    mod, _, cls = spec.partition(":")
    return getattr(importlib.import_module(mod), cls)


def resolve_device(device: str) -> str:
    if device != "auto":
        return device
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "auto"  # let ONNX Runtime pick (DirectML/CoreML/CPU)


class InferenceEngine:
    """Owns one warm model instance per process; thread-safe."""

    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()
        self._model: FoundationModel | None = None
        self._lock = threading.Lock()
        self.load_seconds = 0.0

    # ------------------------------------------------------------------ model
    @property
    def model(self) -> FoundationModel:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    t = time.perf_counter()
                    cls = _resolve(self.s.model)
                    m = cls(self.s.model_dir)
                    m.load(resolve_device(self.s.device))
                    self.load_seconds = time.perf_counter() - t
                    self._model = m
                    log.info("Model %s loaded in %.2fs", m.card.name, self.load_seconds)
        return self._model

    def info(self) -> dict:
        if self.s.inference_backend == "remote":
            return {"backend": "remote", "url": self.s.remote_url, **self._remote_info()}
        m = self.model
        return {
            "backend": "local",
            "card": m.card.to_dict(),
            "runtime": m.runtime_info,
            "load_seconds": round(self.load_seconds, 3),
            "tile_size": self.s.tile_size,
            "tile_batch": self.s.tile_batch,
        }

    # -------------------------------------------------------------- inference
    def run(self, x: np.ndarray) -> tuple[np.ndarray, dict]:
        """x: raw [C_in, H, W] -> raw [C_out, H*s, W*s], timing dict."""
        if self.s.inference_backend == "remote":
            return self._run_remote(x)
        return self.run_local(x)

    def run_local(self, x: np.ndarray) -> tuple[np.ndarray, dict]:
        m = self.model
        t0 = time.perf_counter()
        xn = m.normalize(x[None].astype(np.float32))[0]
        xn = np.nan_to_num(xn, nan=0.0)
        yn, ntiles = self._tiled(m, xn)
        y = m.denormalize(yn[None])[0]
        dt = time.perf_counter() - t0
        return y, {"infer_s": round(dt, 4), "tiles": ntiles, "runtime": m.runtime_info}

    def _tiled(self, m: FoundationModel, x: np.ndarray) -> tuple[np.ndarray, int]:
        C, H, W = x.shape
        s = m.card.scale_factor
        T = self.s.tile_size
        o = max(self.s.tile_overlap, m.card.receptive_halo)
        if H + 2 * o <= T and W + 2 * o <= T:
            # Single pass, but with the same reflect halo the tiled path uses so
            # results are identical regardless of tile size (no edge artifacts).
            xp = np.pad(x, ((0, 0), (o, o), (o, o)), mode="reflect")
            y = m.predict_normalized(xp[None])[0]
            return y[:, o * s:(o + H) * s, o * s:(o + W) * s], 1
        core = T - 2 * o
        nty, ntx = math.ceil(H / core), math.ceil(W / core)
        ph, pw = nty * core - H, ntx * core - W
        xp = np.pad(x, ((0, 0), (o, o + ph), (o, o + pw)), mode="reflect")
        Co = len(m.card.outputs)
        out = np.zeros((Co, nty * core * s, ntx * core * s), np.float32)
        coords = [(i, j) for i in range(nty) for j in range(ntx)]
        B = max(1, self.s.tile_batch)
        for k in range(0, len(coords), B):
            chunk = coords[k:k + B]
            batch = np.stack([xp[:, i * core:i * core + T, j * core:j * core + T] for i, j in chunk])
            pred = m.predict_normalized(batch)
            for (i, j), p in zip(chunk, pred, strict=False):
                out[:, i * core * s:(i + 1) * core * s, j * core * s:(j + 1) * core * s] = \
                    p[:, o * s:(o + core) * s, o * s:(o + core) * s]
        return out[:, :H * s, :W * s], len(coords)

    # ----------------------------------------------------------------- remote
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.s.remote_token}"} if self.s.remote_token else {}

    def _remote_info(self) -> dict:
        import httpx

        try:
            r = httpx.get(f"{self.s.remote_url.rstrip('/')}/v1/info", headers=self._headers(), timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            return {"error": str(e)}

    def _run_remote(self, x: np.ndarray) -> tuple[np.ndarray, dict]:
        import httpx

        t0 = time.perf_counter()
        buf = io.BytesIO()
        np.save(buf, x.astype(np.float32), allow_pickle=False)
        r = httpx.post(
            f"{self.s.remote_url.rstrip('/')}/v1/infer",
            content=buf.getvalue(),
            headers={**self._headers(), "Content-Type": "application/octet-stream"},
            timeout=600,
        )
        r.raise_for_status()
        y = np.load(io.BytesIO(r.content), allow_pickle=False)
        timing = {"infer_s": round(time.perf_counter() - t0, 4), "remote": self.s.remote_url}
        if "x-ppfe-timing" in r.headers:
            timing["worker"] = r.headers["x-ppfe-timing"]
        return y, timing


_engine: InferenceEngine | None = None


def get_engine() -> InferenceEngine:
    global _engine
    if _engine is None:
        _engine = InferenceEngine()
    return _engine


def output_spec() -> tuple[list[str], int]:
    """Output names + scale factor without forcing a remote round-trip."""
    s = get_settings()
    if s.inference_backend == "remote":
        info = get_engine()._remote_info()
        card = info.get("card", {})
        return [o["name"] for o in card.get("outputs", [])], int(card.get("scale_factor", 4))
    m = get_engine().model
    return m.card.output_names, m.card.scale_factor


def input_spec() -> list[str]:
    s = get_settings()
    if s.inference_backend == "remote":
        card = get_engine()._remote_info().get("card", {})
        return [i["name"] for i in card.get("inputs", [])]
    return get_engine().model.card.input_names
