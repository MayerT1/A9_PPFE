"""AOI catalog: every *.geojson in PPFE_AOI_DIR (default ./aois).

Drop official AOI GeoJSONs in that folder (Polygon/MultiPolygon features,
EPSG:4326). `properties.id`, `name`, `region` are used if present; otherwise
derived from the file name and feature index. Files load on every request,
so no restart is needed after adding one.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import get_settings


def _coords(geom: dict):
    t = geom["type"]
    c = geom["coordinates"]
    if t == "Polygon":
        rings = c
    elif t == "MultiPolygon":
        rings = [r for p in c for r in p]
    else:
        raise ValueError(f"AOI geometry must be (Multi)Polygon, got {t}")
    for ring in rings:
        yield from ring


def bbox_of(geom: dict) -> list[float]:
    xs, ys = zip(*((p[0], p[1]) for p in _coords(geom)), strict=False)
    return [min(xs), min(ys), max(xs), max(ys)]


def list_aois(aoi_dir: Path | None = None) -> list[dict]:
    d = aoi_dir or get_settings().aoi_dir
    out = []
    for f in sorted(d.glob("*.geojson")):
        fc = json.loads(f.read_text())
        feats = fc["features"] if fc.get("type") == "FeatureCollection" else [fc]
        for i, ft in enumerate(feats):
            p = ft.get("properties") or {}
            out.append({
                "id": str(p.get("id") or f"{f.stem}_{i}"),
                "name": p.get("name") or f"{f.stem} #{i + 1}",
                "region": p.get("region") or f.stem,
                "informal": bool(p.get("informal", False)),
                "bbox": bbox_of(ft["geometry"]),
                "geometry": ft["geometry"],
                "source_file": f.name,
            })
    return out


def get_aoi(aoi_id: str) -> dict:
    for a in list_aois():
        if a["id"] == aoi_id:
            return a
    raise KeyError(aoi_id)
