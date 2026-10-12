"""Precompute runs + context layers into a static folder for the GitHub Pages demo.

    python scripts/build_demo.py --out site/demo               # uses live HRRR/OSM/DEM if reachable
    PPFE_OFFLINE=1 PPFE_HRRR_SOURCE=synthetic python scripts/build_demo.py --out site/demo

Layout written (read by frontend/demo.js):
    config.json  model.json  aois.json
    runs/<id>/meta.json, *.png, arrays.json + <name>.u16   (quantized arrays for point query / trace)
    layers/<aoi_id>/{waterways,infrastructure}.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def quantize(arr, out: Path, name: str, log: bool = False) -> dict:
    import numpy as np

    a = arr.astype("float64")
    if log:
        a = np.log10(np.maximum(a, 1.0))
    lo, hi = float(np.nanmin(a)), float(np.nanmax(a))
    span = (hi - lo) or 1.0
    q = np.round((np.nan_to_num(a, nan=lo) - lo) / span * 65535).astype("<u2")
    (out / f"{name}.u16").write_bytes(q.tobytes())
    return {"file": f"{name}.u16", "min": lo, "max": hi, "log10": log, "shape": list(arr.shape)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site/demo")
    ap.add_argument("--runs", default=str(ROOT / "demo" / "demo_runs.json"))
    a = ap.parse_args()

    # Isolated data dir so the demo never mixes with a developer's local runs/accounts.
    os.environ.setdefault("PPFE_DATA_DIR", tempfile.mkdtemp(prefix="ppfe-demo-"))
    os.environ.setdefault("PPFE_MAX_WORKERS", "1")

    import numpy as np

    from ppfe import __version__, pipeline
    from ppfe.data import external
    from ppfe.data.aoi import list_aois
    from ppfe.models.registry import get_engine

    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    (out / "runs").mkdir(parents=True)

    aois = {x["id"]: x for x in list_aois()}
    (out / "aois.json").write_text(json.dumps(list(aois.values())))
    (out / "model.json").write_text(json.dumps(get_engine().info()))

    spec = json.loads(Path(a.runs).read_text())["runs"]
    index = []
    for i, r in enumerate(spec):
        rid = f"demo{i + 1:02d}-{r['aoi_id']}-{r['date'].replace('-', '')}t{r['cycle']:02d}f{r['fxx']:02d}"
        print(f"[{i + 1}/{len(spec)}] {rid} ...", flush=True)
        params = {k: r[k] for k in ("aoi_id", "date", "cycle", "fxx")}
        try:
            meta = pipeline.execute(rid, params)
        except Exception as e:
            print(f"   FAILED: {e}")
            continue
        meta["demo_label"] = r.get("label", "")
        src = pipeline.get_settings().runs_dir / rid
        dst = out / "runs" / rid
        dst.mkdir()
        for f in src.glob("*.png"):
            shutil.copy(f, dst / f.name)
        arr = np.load(src / "arrays.npz")
        arrays = {}
        for k in arr.files:
            if k.startswith("pred_"):
                arrays[k[5:]] = {**quantize(arr[k], dst, k[5:]), "grid": "fine"}
        arrays["hazard"] = {**quantize(arr["hazard"], dst, "hazard"), "grid": "analysis"}
        arrays["runoff_m3"] = {**quantize(arr["runoff"], dst, "runoff", log=True), "grid": "analysis"}
        d8 = arr["d8"].astype("int16") + 1  # -1..7 -> 0..8
        (dst / "d8.u8").write_bytes(d8.astype("u1").tobytes())
        arrays["d8"] = {"file": "d8.u8", "grid": "analysis", "shape": list(arr["d8"].shape), "offset": 1}
        (dst / "arrays.json").write_text(json.dumps(arrays))
        (dst / "meta.json").write_text(json.dumps(meta, default=str))
        index.append({"id": rid, **params, "label": meta["demo_label"], "source": meta["hrrr"]["source"],
                      "total_s": meta["timings"]["total_s"]})
        print(f"   ok: HRRR {meta['hrrr']['source']}, {meta['timings']['total_s']} s")

        lay = out / "layers" / r["aoi_id"]
        if not lay.exists():
            lay.mkdir(parents=True)
            for kind in ("waterways", "infrastructure"):
                gj = external.osm_layer(kind, aois[r["aoi_id"]]["bbox"])
                if kind == "infrastructure":
                    for f in gj["features"]:
                        c = external.infra_class(f["properties"])
                        f["properties"]["class"] = c
                        f["properties"]["label"] = external.INFRA_CLASSES.get(c, "Other")
                (lay / f"{kind}.json").write_text(json.dumps(gj))

    if not index:
        sys.exit("No demo runs succeeded")
    cfg = {"version": __version__, "demo": True, "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "offline": pipeline.get_settings().offline, "hrrr_source": "precomputed", "inference_backend": "precomputed",
           "allow_signup": False, "local_hrrr_dates": [], "runs": index}
    (out / "config.json").write_text(json.dumps(cfg, indent=1))
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"Demo written to {out} ({len(index)} runs, {size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
