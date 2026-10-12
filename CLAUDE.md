# CLAUDE.md: A9_PPFE

**Repo:** `C:\Users\tjmayer\Documents\GitHub\A9_PPFE` (renamed from `A9_Prithvi`)

**Purpose of this file:** instructions for a Claude Code instance working locally in this repo. Read it fully before changing anything, and keep it current. It is the source of truth for scope, phase and conventions.

---

## 1. Project summary

**P-PFE** is a decision-support web portal that **hosts a geospatial foundation model (FM) for fast inference**. A user picks an AOI in **New Jersey or Iowa** and an **HRRR date/cycle/forecast hour**. The platform fetches and stages HRRR, runs FM **downscaling**, and serves the prediction on a map. The map overlays OSM, FEMA flood zones, waterways and critical infrastructure, and offers **routing** for storm management: road routing that avoids hazards, and stormwater flow paths. Users **log in, annotate, save workspaces, and export a PDF report**.

The model today is a **placeholder** (untrained, with the correct input/output contract). Tim owns the real model: Prithvi-WxC now, a downscaling ensemble later. The platform's job is to make any `FoundationModel` plug in and run fast. **Do not build Prithvi-WxC model code here** unless asked; build the hosting around it.

Contract framing is unchanged:

| Element | Focus |
|---|---|
| 1 | Architecture/standards (VEDA/IMPACT alignment) |
| 2 | Core platform: ingest, inference, serving, interaction |
| 3 | Docs / knowledge transfer |

Deliverables D1–D5 are tracked in `docs/deliverables/STATUS.md`.

### Decisions (2026-10-12)

- **Pivot away from Geospatial Studio.** Hosting is now a lightweight FastAPI + ONNX Runtime platform. The old Studio runbook is archived in `docs/archive/STUDIO_SETUP.md`; revisit only if IMPACT integration requires it.
- **Local-first, cloud-optional.** Every external dependency is switchable by env var:
  - HRRR source: `aws | local | synthetic | auto`
  - inference: `local | remote` worker
  - offline mode for context layers
- **GitHub hosting** means:
  - Pages serves the static UI (`.github/workflows/pages.yml`).
  - GHCR holds the Docker image (`docker.yml`).
  - CI runs `ci.yml`.
  - The API itself runs locally or on a cloud/lab host; GitHub Actions cannot serve it.
- **Routing covers both:**
  - hazard-aware road routing on OSM
  - D8 stormwater flow tracing
- **Annotations are account-based.** Accounts use SQLite + JWT. Workspaces save AOI, run, annotations, routes, layers and view; they export as GeoJSON and PDF.

---

## 2. Current phase

> Update at the start and end of every session.

- [x] Phase 0: scaffold platform (backend, frontend, tests, Docker, workflows), done 2026-10-12
- [x] Phase 0.2: versioned `FACT_SHEET.md` (linked in the app header) and static GitHub Pages demo (`pages.yml` + `scripts/build_demo.py` + `frontend/demo.js`), done 2026-10-12
- [ ] Phase 1: verify live data paths on a networked Windows machine:
  - Herbie/AWS HRRR
  - Overpass
  - FEMA NFHL layer id
  - Terrarium DEM
- [ ] Phase 2: replace informal AOIs with official GeoJSONs in `aois/`
- [ ] Phase 3: plug in Prithvi-WxC via `plugins/` (Tim) and benchmark on GPU (`ppfe bench`)
- [ ] Phase 4: deployment:
  - Pages UI live
  - choose a shared backend host (AWS/lab), HTTPS, `PPFE_CORS_ORIGINS`
  - Postgres if multi-instance
- [ ] Phase 5: downscaling ensemble (batched members + uncertainty layers); learned hazard model
- [ ] Phase 6: D1 blueprint, docs suite, integration tests (`-m integration`)

**Working on right now:** _fill in_

---

## 3. Repo layout

```
A9_PPFE/
├── CLAUDE.md  README.md  pyproject.toml  Dockerfile  Dockerfile.gpu  docker-compose.yml  .env.example
├── src/ppfe/
│   ├── config.py            # all env settings (PPFE_*)
│   ├── cli.py               # ppfe serve | worker | bench | export-model
│   ├── pipeline.py          # run orchestration: fetch -> infer -> decision -> render
│   ├── render.py            # PNG overlays (Mercator rows), legends, optional GeoTIFF
│   ├── report.py            # PDF report (reportlab)
│   ├── store.py             # SQLite users + workspaces
│   ├── api/app.py           # FastAPI: /api/* portal, /v1/* worker, static UI
│   ├── models/              # base.py contract, placeholder.py, registry.py (tiling engine), onnx_runtime.py
│   ├── data/                # grid, aoi, hrrr (Herbie/local/synthetic), synthetic, external (OSM/FEMA/DEM)
│   └── decision/            # flow.py (D8), hazard.py, roads.py (networkx routing)
├── frontend/                # index.html, app.js, style.css, config.js, vendor/maplibre (no build step)
├── aois/                    # *.geojson AOIs (informal NJ/IA boxes for now)
├── models/                  # placeholder_v2.onnx (committed); real weights gitignored
├── plugins/                 # prithvi_wxc_template.py: how Tim's model plugs in
├── scripts/stage_hrrr.py    # pre-stage HRRR to local NetCDF
├── tests/unit/              # offline tests (core + API)
├── docs/                    # architecture/, developer_guide/, deliverables/, archive/
└── .github/workflows/       # ci.yml, pages.yml, docker.yml
```

---

## 4. Working agreements

1. **Check Section 2 first.** Don't jump phases without saying so.
2. **The platform must stay model-agnostic.**
   - Anything a model needs goes through `ModelCard` / `FoundationModel`.
   - Never special-case model names in pipeline or UI code.
3. **Speed is a feature.**
   - Keep the model warm.
   - Do compute at input resolution.
   - Batch tiles.
   - Prefer ONNX Runtime.
   - Measure with `ppfe bench` before and after changes, and record numbers in STATUS.md.
4. **Local and offline must always work.** `ppfe serve --offline` and `pytest` must pass with no network. New external sources need a cache and a labeled synthetic fallback.
5. **No credentials in the repo.** Use `.env` (gitignored) and `data/.jwt_secret` (auto-generated).
6. **Every new module gets a test** in `tests/unit/`. Network tests go in `tests/integration/`, marked `@pytest.mark.integration`.
7. **Update docs in the same session.** Touch `docs/developer_guide/` and `docs/architecture/overview.md` when behavior changes.
8. **Windows/PowerShell is the primary dev environment.** Give PowerShell commands. Docker and WSL are optional, not required.
9. **Don't fabricate NASA/IMPACT/VEDA compatibility claims.** Flag them for verification (https://nasa-impact.github.io/veda-docs/). Outputs are PNG overlays plus optional GeoTIFF; COG/STAC publishing is a planned VEDA-alignment step, not done.
10. **Verify-not-assume items** are listed in `docs/architecture/overview.md` § Known limits.
11. **Every patch or release updates the fact sheet.** Run `python scripts/update_factsheet.py --bump patch|minor|major -m "what changed"`. This bumps the version in `__init__.py` and `pyproject.toml`, regenerates the AUTO blocks and adds a Version-history line. Edit the hand-written sections whenever behavior, methods, data or limits change. CI fails if `--check` finds the fact sheet stale.

---

## 5. Session startup checklist

1. Read this file and `docs/deliverables/STATUS.md`.
2. Run `git status` and `git log -5 --oneline`.
3. Run `pytest` (it should be green in about 5 s).
4. State the phase and proposed next action before writing code.
5. If this file is stale relative to the repo, propose an update.

## 6. Open questions

- Official AOI GeoJSONs for NJ and IA, and who the end users are (county OEM, DOT, utilities?).
- Prithvi-WxC input mapping from HRRR:
  - variables
  - normalization stats
  - target resolution / scale factor
  - receptive field
- Shared backend host for Pages: AWS (which service?) or a UAH lab server. This needs HTTPS and auth policy, including whether open sign-up is allowed.
- Hazard index weights and thresholds: currently placeholders; need a domain review or a learned replacement.
