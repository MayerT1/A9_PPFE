# Status

## 2026-10-12: platform scaffold (Phase 0 done)

**Built**

- FastAPI portal with a MapLibre UI covering all 9 requested features.
- Placeholder FM (ONNX, ESPCN-style ×4).
- HRRR fetch from AWS/Herbie, local NetCDF or synthetic.
- Decision layers: flow routing, FEMA, hazard, OSM infrastructure.
- Road and flow routing.
- Accounts, workspaces, annotations, PDF reports.
- Remote GPU worker mode.
- Docker images (CPU/GPU) and CI / Pages / GHCR workflows.

**Verified** (offline sandbox, 2 CPU cores, CPUExecutionProvider)

- `pytest`: 15 passed. Covers seamless tiling, routing around hazard, auth isolation, PDF output and the remote-worker round trip.
- Playwright E2E run through the whole UI flow: run → layers → route → trace → annotate → sign up → save → PDF → reload restores state.
- `ppfe bench`:

  | Input → output | Median time |
  |---|---|
  | 512² → 2048² | 353 ms |
  | 1024² → 4096² | 1.11 s |

- Full runs:

  | AOI | Total time |
  |---|---|
  | Passaic basin | 0.56 s |
  | Iowa statewide | 1.84 s |

**Not yet verified** (no outbound network in the build sandbox)

- Herbie/AWS HRRR decoding on Windows
- Overpass queries
- FEMA NFHL layer id 28 and tile proxy
- Terrarium DEM
- Basemap tiles

These are the Phase 1 tasks.

| Deliverable | State |
|---|---|
| D1 blueprint | `docs/architecture/overview.md` (draft) |
| D2 core build | Scaffold complete; awaiting real model and data verification |
| D3 docs | README, model plugin guide, architecture overview |
| D4 integration/validation | Unit/API tests done; integration tests TODO |
| D5 sustainability | TODO |
