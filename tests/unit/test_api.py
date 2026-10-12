import io
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from ppfe.api.app import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def run(client):
    rid = client.post("/api/runs", json={"aoi_id": "ia_des_moines", "date": "2026-09-03", "cycle": 18, "fxx": 2}).json()["id"]
    for _ in range(120):
        st = client.get(f"/api/runs/{rid}").json()
        if st["status"] in ("done", "error"):
            break
        time.sleep(0.25)
    assert st["status"] == "done", st
    return st


def test_aois_and_config(client):
    aois = client.get("/api/aois").json()
    regions = {a["region"] for a in aois}
    assert {"New Jersey", "Iowa"} <= regions
    assert client.get("/api/config").json()["offline"] is True


def test_run_outputs(client, run):
    assert {"apcp_ds", "hazard", "runoff"} <= set(run["layers"])
    r = client.get(f"/api/runs/{run['id']}/files/hazard.png")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert run["grids"]["fine"]["width"] == run["hrrr"]["staging_grid"]["width"] * 4
    assert client.get(f"/api/runs/{run['id']}/files/..%2Fppfe.sqlite3").status_code in (400, 404)


def test_sample_trace_route(client, run):
    b = run["aoi"]["bbox"]
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    s = client.get(f"/api/runs/{run['id']}/sample", params={"lon": cx, "lat": cy}).json()
    assert s["inside"] and 0 <= s["hazard"] <= 1 and "apcp_ds" in s
    t = client.get(f"/api/runs/{run['id']}/trace", params={"lon": cx, "lat": cy}).json()
    assert t["geometry"]["type"] == "LineString" and len(t["geometry"]["coordinates"]) >= 1
    r = client.post(f"/api/runs/{run['id']}/route", json={"start": [b[0] + 0.05, cy], "end": [b[2] - 0.05, cy]})
    assert r.status_code == 200 and r.json()["baseline"]


def test_auth_projects_export(client, run):
    r = client.post("/api/auth/signup", json={"email": "a@b.org", "name": "A", "password": "longenough1"})
    assert r.status_code == 200
    assert client.post("/api/auth/signup", json={"email": "a@b.org", "password": "longenough1"}).status_code == 409
    assert client.post("/api/auth/login", json={"email": "a@b.org", "password": "nope"}).status_code == 401
    tok = client.post("/api/auth/login", json={"email": "a@b.org", "password": "longenough1"}).json()["token"]
    H = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/projects").status_code == 401
    ann = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [-93.6, 41.6]},
                                                     "properties": {"title": "Pump <here>", "note": "line1\nline2"}}]}
    p = client.post("/api/projects", json={"name": "t1", "state": {"run_id": run["id"], "annotations": ann}}, headers=H).json()
    assert client.get("/api/projects", headers=H).json()[0]["n_annotations"] == 1
    p2 = client.put(f"/api/projects/{p['id']}", json={"name": "t1b", "state": {"annotations": ann}}, headers=H).json()
    assert p2["name"] == "t1b"
    ex = client.get(f"/api/projects/{p['id']}/export.geojson", headers=H)
    assert ex.json()["features"][0]["properties"]["title"] == "Pump <here>"
    # other users can't see it
    tok2 = client.post("/api/auth/signup", json={"email": "c@d.org", "password": "longenough2"}).json()["token"]
    assert client.get(f"/api/projects/{p['id']}", headers={"Authorization": f"Bearer {tok2}"}).status_code == 404
    assert client.delete(f"/api/projects/{p['id']}", headers=H).status_code == 200


def test_report_pdf(client, run):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (400, 250), (200, 220, 240)).save(buf, "PNG")
    import base64

    png = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    ann = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
                                                     "properties": {"title": "A & <B>", "note": "x\ny"}}]}
    r = client.post("/api/report", json={"title": "T <1>", "notes": "n & m", "run_id": run["id"], "map_png": png, "annotations": ann,
                                         "routes": {"baseline": {"length_km": 1, "minutes": 2, "max_hazard": 0.1}, "safe": None, "blocked_edges": 3}})
    assert r.status_code == 200 and r.content[:4] == b"%PDF"


def test_worker_endpoint_roundtrip(client):
    from ppfe.models.registry import get_engine

    card = get_engine().model.card
    x = np.random.default_rng(0).normal(size=(len(card.inputs), 20, 24)).astype(np.float32)
    buf = io.BytesIO()
    np.save(buf, x)
    r = client.post("/v1/infer", content=buf.getvalue(), headers={"Content-Type": "application/octet-stream"})
    y = np.load(io.BytesIO(r.content))
    assert y.shape == (len(card.outputs), 80, 96)
    local, _ = get_engine().run_local(x)
    np.testing.assert_allclose(y, local, atol=1e-5)


def test_remote_backend_uses_worker(client, monkeypatch):
    """Point the engine at the worker API (served by the same TestClient)."""
    from ppfe.models.registry import get_engine

    eng = get_engine()
    monkeypatch.setattr(eng.s, "inference_backend", "remote")
    monkeypatch.setattr(eng.s, "remote_url", "http://worker")
    import httpx

    monkeypatch.setattr(httpx, "post", lambda url, **kw: client.post(url.replace("http://worker", ""), **kw))
    x = np.ones((len(eng.model.card.inputs), 10, 10), np.float32)
    y, t = eng.run(x)
    assert y.shape[1:] == (40, 40) and t["remote"] == "http://worker"


def test_fact_sheet_served_and_current(client):
    import subprocess
    import sys
    from pathlib import Path

    from ppfe import __version__

    r = client.get("/FACT_SHEET.md")
    assert r.status_code == 200 and f"**Version {__version__}**" in r.text
    assert client.get("/factsheet.html").status_code == 200
    root = Path(__file__).resolve().parents[2]
    out = subprocess.run([sys.executable, str(root / "scripts" / "update_factsheet.py"), "--check"], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
