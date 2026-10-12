"""Foundation-model plug-in contract.

Any model the platform hosts (the placeholder today, Prithvi-WxC or the
downscaling ensemble later) implements `FoundationModel`. The platform owns
data staging, normalization bookkeeping, tiling, batching, device placement,
and serving; the model only maps a normalized input tensor to an output tensor.

    x: float32 [B, C_in, H, W]         (HRRR vars on the staging grid)
    y: float32 [B, C_out, H*s, W*s]    (downscaled fields, s = scale_factor)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np


@dataclass
class VarSpec:
    name: str            # platform name, e.g. "apcp"
    units: str = ""
    mean: float = 0.0    # normalization stats (x - mean) / std
    std: float = 1.0
    long_name: str = ""


@dataclass
class ModelCard:
    name: str
    version: str
    inputs: list[VarSpec]
    outputs: list[VarSpec]
    scale_factor: int = 4
    # Spatial context the model needs on each side of a tile (input pixels).
    # Tiles are cut with at least this much overlap and the halo is discarded.
    receptive_halo: int = 8
    description: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def input_names(self) -> list[str]:
        return [v.name for v in self.inputs]

    @property
    def output_names(self) -> list[str]:
        return [v.name for v in self.outputs]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "scale_factor": self.scale_factor,
            "receptive_halo": self.receptive_halo,
            "description": self.description,
            "inputs": [v.__dict__ for v in self.inputs],
            "outputs": [v.__dict__ for v in self.outputs],
        }


class FoundationModel(ABC):
    """Subclass this to host a new model. See docs/developer_guide/model_plugin.md."""

    card: ModelCard

    @abstractmethod
    def load(self, device: str) -> None:
        """Load weights / build the inference session. Called once, kept warm."""

    @abstractmethod
    def predict_normalized(self, x: np.ndarray) -> np.ndarray:
        """[B, C_in, h, w] normalized -> [B, C_out, h*s, w*s] normalized."""

    @property
    def runtime_info(self) -> dict:
        return {}

    # Normalization lives on the platform side so every model gets it for free.
    def normalize(self, x: np.ndarray) -> np.ndarray:
        m = np.array([v.mean for v in self.card.inputs], np.float32)[None, :, None, None]
        s = np.array([v.std for v in self.card.inputs], np.float32)[None, :, None, None]
        return (x - m) / s

    def denormalize(self, y: np.ndarray) -> np.ndarray:
        m = np.array([v.mean for v in self.card.outputs], np.float32)[None, :, None, None]
        s = np.array([v.std for v in self.card.outputs], np.float32)[None, :, None, None]
        return y * s + m
