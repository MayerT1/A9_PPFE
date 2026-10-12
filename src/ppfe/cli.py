"""`ppfe` command line.

    ppfe serve                 # portal on http://127.0.0.1:8000 (local inference)
    ppfe serve --offline       # no network: synthetic HRRR + synthetic context layers
    ppfe worker --port 8100    # inference-only worker for a GPU box / cloud
    ppfe export-model          # (re)export the placeholder ONNX
    ppfe bench --size 512      # time the inference engine
"""
from __future__ import annotations

import argparse
import os
import time


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ppfe")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("serve", "worker"):
        p = sub.add_parser(name)
        p.add_argument("--host", default="127.0.0.1")
        p.add_argument("--port", type=int, default=8000 if name == "serve" else 8100)
        p.add_argument("--offline", action="store_true", help="synthetic HRRR + context layers, no network")
        p.add_argument("--reload", action="store_true")
    sub.add_parser("export-model")
    b = sub.add_parser("bench")
    b.add_argument("--size", type=int, default=512)
    b.add_argument("--repeat", type=int, default=5)
    a = ap.parse_args(argv)

    if a.cmd in ("serve", "worker"):
        if a.offline:
            os.environ["PPFE_OFFLINE"] = "1"
            os.environ.setdefault("PPFE_HRRR_SOURCE", "synthetic")
        if a.cmd == "worker":
            os.environ["PPFE_ROLE"] = "worker"
            os.environ["PPFE_INFERENCE_BACKEND"] = "local"
        import uvicorn

        uvicorn.run("ppfe.api.app:app", host=a.host, port=a.port, reload=a.reload, log_level="info")
    elif a.cmd == "export-model":
        from .config import get_settings
        from .models.placeholder import export_onnx

        print(export_onnx(get_settings().model_dir / "placeholder_v2.onnx"))
    elif a.cmd == "bench":
        import numpy as np

        from .models.registry import get_engine

        eng = get_engine()
        m = eng.model
        x = np.random.default_rng(0).normal(size=(len(m.card.inputs), a.size, a.size)).astype("float32")
        for v, i in zip(m.card.inputs, range(x.shape[0]), strict=False):
            x[i] = x[i] * v.std + v.mean
        eng.run_local(x)  # warm-up
        ts = []
        for _ in range(a.repeat):
            t = time.perf_counter()
            _, info = eng.run_local(x)
            ts.append(time.perf_counter() - t)
        s = m.card.scale_factor
        print(f"model={m.card.name} runtime={m.runtime_info}")
        print(f"input {a.size}x{a.size} -> output {a.size * s}x{a.size * s}, tiles={info['tiles']}")
        print(f"median {np.median(ts) * 1000:.1f} ms  min {min(ts) * 1000:.1f} ms  "
              f"({a.size * a.size * s * s / np.median(ts) / 1e6:.1f} Mpx/s out)")


if __name__ == "__main__":
    main()
