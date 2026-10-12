"""Rule-based flood-hazard index on top of the model prediction.

hazard in [0, 1] = w_rain * rain_score + w_flow * flow_score + w_fema * in_SFHA

Deliberately simple and transparent; this is the extension point for a
learned decision model later (swap `hazard_index`).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

CLASSES = [(0.0, "Low"), (0.35, "Moderate"), (0.55, "High"), (0.75, "Severe")]


@dataclass
class HazardWeights:
    w_rain: float = 0.45
    w_flow: float = 0.40
    w_fema: float = 0.15
    rain_ref_mm: float = 25.0     # 1 h rain giving rain_score = 1
    flow_ref_m3: float = 2.0e6    # upstream runoff volume giving flow_score = 1

    def to_dict(self):
        return asdict(self)


def hazard_index(rain_mm: np.ndarray, runoff_m3: np.ndarray, fema_mask: np.ndarray | None,
                 w: HazardWeights = HazardWeights()) -> np.ndarray:
    rain = np.clip(rain_mm / w.rain_ref_mm, 0, 1)
    flow = np.clip(np.log1p(runoff_m3) / np.log1p(w.flow_ref_m3), 0, 1)
    fema = fema_mask.astype(np.float32) if fema_mask is not None else 0.0
    tot = w.w_rain + w.w_flow + (w.w_fema if fema_mask is not None else 0.0)
    h = (w.w_rain * rain + w.w_flow * flow * np.sqrt(rain + 0.05)
         + (w.w_fema * fema if fema_mask is not None else 0.0)) / tot
    return np.clip(h, 0, 1).astype(np.float32)


def classify(v: float) -> str:
    label = CLASSES[0][1]
    for t, name in CLASSES:
        if v >= t:
            label = name
    return label


def summarize(h: np.ndarray) -> dict:
    total = h.size
    out = {}
    for i, (t, name) in enumerate(CLASSES):
        hi = CLASSES[i + 1][0] if i + 1 < len(CLASSES) else 1.01
        out[name] = round(float(((h >= t) & (h < hi)).sum()) / total * 100, 2)
    return {"percent_area": out, "max": round(float(h.max()), 3), "mean": round(float(h.mean()), 3)}
