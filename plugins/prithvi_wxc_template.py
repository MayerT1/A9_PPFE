"""TEMPLATE: host Prithvi-WxC (or the downscaling ensemble) on the A9_PPFE platform.

Copy, fill in the TODOs, then run the portal with:

    PPFE_MODEL=plugins.prithvi_wxc_template:PrithviWxCDownscaler ppfe serve

(Run from the repo root so `plugins` is importable, or install it as a package.)

The platform handles HRRR fetch/regrid, normalization (from the ModelCard),
tiling with halo, batching, device placement, remote-worker serving, and all
decision layers. You implement load() and predict_normalized().

Speed checklist (what the platform expects of a hosted FM):
  * Export to ONNX (opset >= 17) and run with ONNX Runtime CUDA/TensorRT, or
    keep torch with: torch.inference_mode(), .half() on GPU, channels_last,
    torch.compile(mode="max-autotune") once at load().
  * Do heavy compute at INPUT resolution and upsample late (pixel shuffle /
    transposed conv) — 16x fewer FLOPs at x4.
  * Set ModelCard.receptive_halo to the model's real spatial context so tiles
    are seamless; set PPFE_TILE_SIZE / PPFE_TILE_BATCH to fill GPU memory.
  * Ensembles: stack members on the batch axis in one session call instead of
    looping members in Python, then reduce (mean/quantiles) as extra outputs.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ppfe.models.base import FoundationModel, ModelCard, VarSpec

CARD = ModelCard(
    name="prithvi-wxc-downscaler",
    version="0.0.1-dev",
    scale_factor=4,                 # TODO: real factor (e.g. 3 km HRRR -> 750 m)
    receptive_halo=32,              # TODO: real receptive field in input pixels
    description="Prithvi-WxC downscaling head on HRRR inputs (TODO)",
    inputs=[                        # TODO: must be names in ppfe.data.hrrr.HERBIE_SEARCH (add there if needed)
        VarSpec("apcp", "mm", 0.0, 1.0),
        VarSpec("prate", "mm/h", 0.0, 1.0),
        VarSpec("tmp2m", "K", 0.0, 1.0),
        VarSpec("u10", "m/s", 0.0, 1.0),
        VarSpec("v10", "m/s", 0.0, 1.0),
        VarSpec("refc", "dBZ", 0.0, 1.0),
    ],
    outputs=[                       # names ending in _ds get map layers; apcp_ds drives the flood layers
        VarSpec("apcp_ds", "mm", 0.0, 1.0),
        VarSpec("tmp2m_ds", "K", 0.0, 1.0),
    ],
)


class PrithviWxCDownscaler(FoundationModel):
    card = CARD

    def __init__(self, model_dir: Path):
        self.weights = Path(model_dir) / "prithvi_wxc_ds.onnx"   # TODO
        self.session = None

    def load(self, device: str) -> None:
        from ppfe.models.onnx_runtime import make_session

        # TODO: load normalization stats from your checkpoint into self.card.inputs/outputs
        self.session = make_session(self.weights, device)

    def predict_normalized(self, x: np.ndarray) -> np.ndarray:
        # x: [B, C_in, h, w] float32 normalized; return [B, C_out, h*s, w*s]
        return self.session.run(None, {self.session.get_inputs()[0].name: x})[0]

    @property
    def runtime_info(self) -> dict:
        return {"mode": "onnxruntime", "providers": self.session.get_providers() if self.session else []}
