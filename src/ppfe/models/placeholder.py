"""Placeholder downscaling foundation model.

Stand-in for Prithvi-WxC / the downscaling ensemble. It has the same
contract a real model will have (HRRR vars in -> 4x finer fields out) so the
platform, serving, and UI can be built and benchmarked now.

Architecture: bilinear 4x skip + ESPCN residual (convs at input res + pixel shuffle).
It is NOT a trained model -- outputs are smooth, plausible-looking fields only.

Execution, fastest available first:
  1. ONNX Runtime on models/placeholder_v2.onnx (exported on first use if torch present)
  2. Pure NumPy/SciPy fallback (no torch, no onnx file) -- still correct shape/units
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from .base import FoundationModel, ModelCard, VarSpec

log = logging.getLogger(__name__)

CARD = ModelCard(
    name="placeholder-downscaler",
    version="0.2.0",
    scale_factor=4,
    receptive_halo=8,
    description="Bilinear x4 + ESPCN residual placeholder. Untrained. Swap for Prithvi-WxC.",
    inputs=[
        VarSpec("apcp", "mm", 1.0, 3.0, "Total precipitation, 1 h accumulation"),
        VarSpec("prate", "mm/h", 1.0, 3.0, "Precipitation rate"),
        VarSpec("tmp2m", "K", 285.0, 12.0, "2 m temperature"),
        VarSpec("u10", "m/s", 0.0, 5.0, "10 m U wind"),
        VarSpec("v10", "m/s", 0.0, 5.0, "10 m V wind"),
        VarSpec("refc", "dBZ", 5.0, 15.0, "Composite reflectivity"),
    ],
    outputs=[
        VarSpec("apcp_ds", "mm", 1.0, 3.0, "Downscaled 1 h precipitation"),
        VarSpec("tmp2m_ds", "K", 285.0, 12.0, "Downscaled 2 m temperature"),
    ],
)

# Output channel k is a refinement of input channel _SKIP[k].
_SKIP = [0, 2]


def build_torch_module():
    """ESPCN-style: all convolutions run at INPUT resolution, then a pixel
    shuffle lifts the residual to output resolution (~s^2 = 16x cheaper than
    convolving at output resolution). Bilinear skip carries the large scale (ORT bicubic Resize is ~100x slower on CPU).
    Real downscaling models should follow the same rule for speed."""
    import torch
    from torch import nn

    s = CARD.scale_factor
    cin, cout, hidden = len(CARD.inputs), len(CARD.outputs), 32

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.body = nn.Sequential(
                nn.Conv2d(cin, hidden, 3, padding=1), nn.GELU(),
                nn.Conv2d(hidden, hidden, 3, padding=1), nn.GELU(),
                nn.Conv2d(hidden, cout * s * s, 3, padding=1),
                nn.PixelShuffle(s),
            )

        def forward(self, x):
            skip = torch.cat([x[:, i:i + 1] for i in _SKIP], dim=1)
            up = nn.functional.interpolate(skip, scale_factor=s, mode="bilinear", align_corners=False)
            return up + 0.05 * self.body(x)

    torch.manual_seed(0)
    return Net().eval()


def export_onnx(path: Path) -> Path:
    import torch

    net = build_torch_module()
    dummy = torch.zeros(1, len(CARD.inputs), 32, 32)
    path.parent.mkdir(parents=True, exist_ok=True)
    kw = dict(
        input_names=["x"], output_names=["y"], opset_version=17,
        dynamic_axes={"x": {0: "b", 2: "h", 3: "w"}, "y": {0: "b", 2: "H", 3: "W"}},
    )
    try:
        torch.onnx.export(net, dummy, str(path), dynamo=False, **kw)
    except TypeError:  # older torch without the `dynamo` kwarg
        torch.onnx.export(net, dummy, str(path), **kw)
    log.info("Exported placeholder model -> %s", path)
    return path


class PlaceholderModel(FoundationModel):
    card = CARD

    def __init__(self, model_dir: Path):
        self.path = Path(model_dir) / "placeholder_v2.onnx"
        self.session = None
        self.mode = "unloaded"
        self.providers: list[str] = []

    def load(self, device: str) -> None:
        if not self.path.exists():
            try:
                export_onnx(self.path)
            except Exception as e:  # torch not installed etc.
                log.warning("ONNX export unavailable (%s); using NumPy fallback", e)
        if self.path.exists():
            try:
                from .onnx_runtime import make_session

                self.session = make_session(self.path, device)
                self.providers = self.session.get_providers()
                self.mode = "onnxruntime"
                return
            except Exception as e:
                log.warning("onnxruntime unavailable (%s); using NumPy fallback", e)
        self.mode = "numpy"

    def predict_normalized(self, x: np.ndarray) -> np.ndarray:
        if self.session is not None:
            return self.session.run(["y"], {"x": np.ascontiguousarray(x, np.float32)})[0]
        from scipy.ndimage import zoom

        s = self.card.scale_factor
        return np.stack(
            [np.stack([zoom(x[b, i], s, order=1) for i in _SKIP]) for b in range(x.shape[0])]
        ).astype(np.float32)

    @property
    def runtime_info(self) -> dict:
        return {"mode": self.mode, "providers": self.providers, "weights": self.path.name}
