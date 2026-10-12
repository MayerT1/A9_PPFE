> **Superseded 2026-10-12.** The project pivoted from Geospatial Studio to the lightweight A9_PPFE hosting platform (see CLAUDE.md §1). Kept for reference only.

# STUDIO_SETUP.md — Standing up Geospatial Studio Locally (Windows)

**Read this together with `CLAUDE.md`.** This file is the concrete runbook for Phase 2 (Geospatial Studio integration). It exists because the upstream repos assume macOS/Linux/cluster environments in places — this file adapts that to your Windows machine and to a "no real model, no GPU yet" starting point.

---

## 1. The Four Repos and What Each One Is

| Repo | What it is | Do we clone it into A9_Prithvi? |
|---|---|---|
| [`geospatial-studio`](https://github.com/terrastackai/geospatial-studio) | Helm charts + deployment scripts (OCP / Lima / Kind). This is what you run to stand the platform up. | Clone as a **sibling** repo, not nested inside A9_Prithvi. |
| `geospatial-studio-core` | Studio Gateway API source, tuning/inference image build scripts, automated model deployment scripts. | Only clone if we need to inspect/modify backend behavior. Not needed to just run the platform (it ships as container images pulled by the Helm charts). |
| [`geospatial-studio-ui`](https://github.com/terrastackai/geospatial-studio-ui) | The web UI source. | Only clone if we need to inspect/modify the UI. Same as above — it's pulled as a container image for normal deployment. |
| [`geospatial-studio-toolkit`](https://github.com/terrastackai/geospatial-studio-toolkit) | Python SDK (`geostudio` on PyPI) + example notebooks + QGIS plugin. **This is what A9_Prithvi's `ppfe.inference.client` wraps.** | Clone as a sibling repo so Claude Code can read the example notebooks directly (don't reinvent SDK call patterns — copy them). |

**Recommended local folder layout** (all siblings under your existing GitHub folder):

```
C:\Users\tjmayer\Documents\GitHub\
├── A9_Prithvi\                    # this project — the P-PFE tool
├── geospatial-studio\              # cloned: deployment scripts/Helm charts
├── geospatial-studio-toolkit\      # cloned: SDK + notebooks (reference only)
└── geospatial-studio-ui\           # cloned only if/when UI work is needed
```

A9_Prithvi does **not** vendor or commit copies of these repos. Record their local paths in `docs/architecture/external_repos.md` (path, commit hash last synced, why we clone it) so anyone picking this up later knows where things live.

---

## 2. Why Kind, Not Lima

The `geospatial-studio` README documents three deployment paths: OpenShift (OCP), Lima VM (local), and a plain Kubernetes/Kind cluster. **Lima VM is macOS/Linux-only** — it is not an option on Windows. The correct path here is the **Kind cluster deployment ("Kind Cluster Deployment without GPUs")**, run from inside **WSL2** (the shell scripts are bash, not PowerShell — don't try to run `.sh` files directly from PowerShell).

This also conveniently matches our actual need: we're deploying without GPU access and starting from dummy/precomputed outputs, not live model tuning.

---

## 3. Prerequisites (run inside WSL2 Ubuntu, not native PowerShell)

Confirm WSL2 + a Linux distro is installed first (`wsl --install` from an elevated PowerShell if not). Then inside the WSL2 shell:

| Tool | Purpose | Notes |
|---|---|---|
| Docker Desktop (Windows) with WSL2 integration enabled | Container runtime backing Kind | Install on Windows side, enable WSL2 integration in Docker Desktop settings for your distro |
| `kind` | Runs the K8s cluster in Docker | Install inside WSL2 |
| `helm` v3.19+ | K8s package manager | Version matters — older Helm breaks this deployment per upstream docs |
| `oc` (OpenShift CLI) | Bundles `kubectl` | |
| `jq`, `yq` | JSON/YAML processors used by the deploy scripts | |
| Python 3.8+ | Deployment scripts + SDK | Use a venv |

**Verification checklist before proceeding** (Claude Code should run these and confirm output, not assume):
```bash
docker info            # confirms Docker Desktop + WSL2 integration is live
kind version
helm version            # must show v3.19+
oc version --client
kubectl version --client
jq --version
yq --version
python3 --version
```

---

## 4. Deployment Steps

Run from inside the cloned `geospatial-studio` repo, in WSL2:

```bash
# 1. Create the Kind cluster (control-plane + worker node)
cat << EOF | kind create cluster --name=studio --config=-
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
- role: worker
EOF

# 2. Point kubectl at it and sanity check
kubectl cluster-info --context kind-studio

# 3. Python deps for the deploy scripts
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 4. Deploy (this pulls container images — expect ~10+ min on first run)
./deploy_studio_k8s.sh
```

This provisions MinIO, PostgreSQL, and Keycloak, then deploys the Studio gateway API, UI, MLflow, and Geoserver on top.

**Do not proceed to Section 5 until these all check out:**
```bash
kubectl get pods -A          # everything should reach Running/Completed, no CrashLoopBackOff
```

If pods are stuck, capture `kubectl describe pod <name> -n <namespace>` output before troubleshooting — don't guess at fixes.

### Access points once healthy
| Service | URL | Auth |
|---|---|---|
| Studio UI | https://localhost:4180 | `testuser` / `testpass123` |
| Studio API | https://localhost:4181 | via API key generated in UI |
| Geoserver | http://localhost:3000/geoserver | `admin` / `geoserver` |
| MLflow | http://localhost:5000 | — |
| Keycloak | http://localhost:8080 | `admin` / `admin` |
| Minio | console https://localhost:9001 / API https://localhost:9000 | `minioadmin` / `minioadmin` |

If port-forwards drop, re-run the `kubectl port-forward` commands documented in the `geospatial-studio` README (one per service, backgrounded with `&`).

---

## 5. Getting an API Key (needed before any SDK/API calls)

1. Open the UI (`https://localhost:4180`), find **Manage your API keys**, generate one.
2. In WSL2:
   ```bash
   export STUDIO_API_KEY="<key from UI>"
   export UI_ROUTE_URL="https://localhost:4180"
   ```
3. For the `geostudio` Python SDK (used from `ppfe.inference.client`), write a local credentials file (never commit it):
   ```bash
   echo "GEOSTUDIO_API_KEY=$STUDIO_API_KEY" > .geostudio_config_file
   echo "BASE_STUDIO_UI_URL=$UI_ROUTE_URL" >> .geostudio_config_file
   ```

---

## 6. Getting a Dummy Prithvi-Like Output Into the Studio (the actual near-term goal)

We are **deliberately avoiding** the full fine-tune → GPU-inference path for now. The kind-cluster deployment is explicitly GPU-free, and running real TerraTorch/Prithvi tuning or inference on CPU inside Kind is not a realistic near-term goal. Two supported-by-upstream shortcuts get us to "a user can view/interact with a Prithvi-WxC-like output" much faster:

### Option A — Onboard an existing example inference (fastest sanity check)
Confirms the whole stack (UI, gateway, Minio, Geoserver) works end-to-end before we touch our own data:
```bash
python populate-studio/populate-studio.py inferences
# select "AGB Data - Karen, Nairobi, Kenya" (or similar) when prompted
```
Check it renders in the UI's inference page. This validates plumbing, not our model.

### Option B — Upload our own dummy output as a "precomputed example" (the real target)
This is the path that matches "dummy Prithvi-WxC output, user can inference/view it" without needing a live model run:
1. In `A9_Prithvi`, Phase 1's `dummy_wxc.py` produces a synthetic forecast (see `CLAUDE.md` Section 5) — output it as a GeoTIFF/COG stack, since that's what Geoserver/the UI expect for layer visualization (confirm exact expected structure against the toolkit's example notebook below before finalizing the writer).
2. Follow the toolkit's **"Uploading pre-computed examples"** notebook pattern (`geospatial-studio-toolkit/examples/inference/002-Add-Precomputed-Examples.ipynb`) to register our dummy output as an inference example via the SDK — this onboards a layer for visualization without running any model inside the Studio.
3. Verify it appears in the UI's inference/layers view and can be inspected via Geoserver.

**Do Option A first.** If Option A doesn't work, don't attempt Option B — fix the deployment first, since Option B failures would otherwise be ambiguous (is it our data, or the stack?).

### Later (not now): real inference
Once Options A/B work, the documented path for an actual tuned-model run is:
```bash
python populate-studio/populate-studio.py templates   # onboard tuning task templates
python populate-studio/populate-studio.py tunes        # e.g. select "prithvi-eo-flood"
# then POST to /studio-gateway/v2/tunes/<tune_id>/try-out with a spatial/temporal domain payload
```
This requires either GPU-backed cluster access or accepting slow CPU inference — treat as a Phase 3+ decision, not now.

---

## 7. Session Checklist Addition

Add to the `CLAUDE.md` Section 9 startup checklist when working on Studio integration:
```bash
kind get clusters                 # confirm "studio" cluster still exists
kubectl get pods -A --context kind-studio   # confirm nothing crash-looped since last session
```
If the cluster is gone (e.g. Docker Desktop was restarted and volumes weren't persisted correctly), re-run Section 4 rather than assuming state carried over.

---

## 8. Known Gaps / Things to Verify, Not Assume

- Exact expected raster format/structure for a "precomputed example" upload — read the notebook in Section 6 Option B before writing `dummy_wxc.py`'s output format, don't guess.
- `geospatial-studio-core` internals are unread as of this writing — if the gateway API behaves unexpectedly, clone it rather than reverse-engineering from the outside.
- Resource requirements for Kind on Docker Desktop (CPU/RAM) aren't documented in the upstream README — if `deploy_studio_k8s.sh` fails partway, check Docker Desktop's resource allocation before assuming a scripting bug.
- Whether Windows Defender / corporate endpoint tooling interferes with Kind's Docker-in-Docker networking is untested — flag early if port-forwards behave strangely.
