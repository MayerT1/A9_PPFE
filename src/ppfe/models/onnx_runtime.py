"""ONNX Runtime helpers: provider selection + session tuned for throughput.

Provider order (first available wins unless PPFE_DEVICE pins one):
  CUDA (NVIDIA)  ->  DirectML (any Windows GPU)  ->  CoreML (Apple)  ->  CPU
TensorRT is used if present and PPFE_DEVICE=trt.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

_PREF = {
    "trt": ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"],
    "cuda": ["CUDAExecutionProvider", "CPUExecutionProvider"],
    "dml": ["DmlExecutionProvider", "CPUExecutionProvider"],
    "coreml": ["CoreMLExecutionProvider", "CPUExecutionProvider"],
    "cpu": ["CPUExecutionProvider"],
    "auto": [
        "CUDAExecutionProvider",
        "DmlExecutionProvider",
        "CoreMLExecutionProvider",
        "CPUExecutionProvider",
    ],
}


def pick_providers(device: str) -> list[str]:
    import onnxruntime as ort

    avail = set(ort.get_available_providers())
    wanted = _PREF.get(device, _PREF["auto"])
    chosen = [p for p in wanted if p in avail] or ["CPUExecutionProvider"]
    return chosen


def make_session(path: Path, device: str = "auto"):
    import onnxruntime as ort

    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    so.intra_op_num_threads = int(os.environ.get("PPFE_ORT_THREADS", "0")) or (os.cpu_count() or 4)
    providers = pick_providers(device)
    if "DmlExecutionProvider" in providers:
        # DirectML requires these two settings.
        so.enable_mem_pattern = False
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    sess = ort.InferenceSession(str(path), sess_options=so, providers=providers)
    log.info("ONNX session %s on %s", path.name, sess.get_providers())
    return sess
