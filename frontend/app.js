/* A9 PPFE portal — vanilla JS + MapLibre GL. No build step. */
"use strict";

const API = ((window.PPFE_CONFIG && window.PPFE_CONFIG.API_BASE) || "").replace(/\/$/, "");
const ABS = API || location.origin;
const DEMO = !!(window.PPFE_CONFIG && window.PPFE_CONFIG.DEMO && window.PPFE_DEMO);
const fileUrl = (id, name) => (DEMO ? window.PPFE_DEMO.fileUrl(id, name) : `${ABS}/api/runs/${id}/files/${name}`);
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const EMPTY = () => ({ type: "FeatureCollection", features: [] });
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ------------------------------------------------------------------ storage
const store = {
  get(k, d = null) { try { const v = localStorage.getItem("ppfe." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("ppfe." + k, JSON.stringify(v)); } catch { /* private mode */ } },
  del(k) { try { localStorage.removeItem("ppfe." + k); } catch { /* noop */ } },
};

// -------------------------------------------------------------------- state
const S = {
  token: store.get("token"),
  user: store.get("user"),
  config: null,
  aois: [],
  aoi: null,
  run: null,
  predVisible: {},
  predOpacity: {},
  annotations: store.get("annotations", EMPTY()),
  routes: null,
  traces: EMPTY(),
  routePts: [],
  mode: null,          // {kind:'route'|'trace'|'draw', geom?}
  draft: [],
  editingId: null,
  annColor: "#d62f2f",
  projectId: null,
  projectName: "",
  ctxOn: {},
  basemap: "osm",
};

// ---------------------------------------------------------------------- api
async function api(path, opts = {}) {
  if (DEMO) {
    if (opts.json !== undefined) opts = { ...opts, body: JSON.stringify(opts.json) };
    return window.PPFE_DEMO.api(path, opts);
  }
  const headers = { ...(opts.headers || {}) };
  if (S.token) headers.Authorization = "Bearer " + S.token;
  if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
  const r = await fetch(API + path, { ...opts, headers });
  if (r.status === 401 && S.token && !path.startsWith("/api/auth")) { logout(true); }
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* not json */ }
    throw new Error(msg || "Request failed (" + r.status + ")");
  }
  if (opts.raw) return r;
  return r.json();
}

let toastT;
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toastT);
  toastT = setTimeout(() => (t.className = "toast"), err ? 6000 : 3200);
}

// ---------------------------------------------------------------------- map
const BASEMAPS = {
  osm: { tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], attribution: "© OpenStreetMap contributors", maxzoom: 19 },
  topo: { tiles: ["https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}"], attribution: "USGS The National Map", maxzoom: 16 },
  imagery: { tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"], attribution: "Esri, Maxar, Earthstar Geographics", maxzoom: 19 },
};

const style = {
  version: 8,
  sources: Object.fromEntries(Object.entries(BASEMAPS).map(([k, v]) => ["bm-" + k, { type: "raster", tileSize: 256, ...v }])),
  layers: [
    { id: "bg", type: "background", paint: { "background-color": "#eef1f4" } },
    ...Object.keys(BASEMAPS).map((k) => ({ id: "bm-" + k, type: "raster", source: "bm-" + k, layout: { visibility: k === "osm" ? "visible" : "none" } })),
  ],
};

const map = new maplibregl.Map({
  container: "map", style, center: [-84.5, 41.2], zoom: 4.4,
  preserveDrawingBuffer: true, attributionControl: { compact: true }, maxPitch: 0,
});
map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), "top-right");
map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }), "bottom-left");
map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");

const INFRA_COLORS = {
  hospital: "#d62f2f", fire_station: "#e8590c", police: "#1c4fa0", school: "#7a3db8", shelter: "#2b8a3e",
  substation: "#c79a00", wastewater_plant: "#5c4033", water_works: "#1098ad", pumping_station: "#0b7285", other: "#666",
};

function addGeo(id) { map.addSource(id, { type: "geojson", data: EMPTY() }); }

map.on("load", () => {
  // Order matters: prediction rasters are inserted below "anchor-pred".
  addGeo("aoi");
  map.addLayer({ id: "aoi-fill", type: "fill", source: "aoi", paint: { "fill-color": "#0b6e99", "fill-opacity": 0.04 } });

  map.addSource("fema", { type: "raster", tiles: [DEMO
    // Static demo has no tile proxy: request FEMA's export endpoint directly.
    ? "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/export?bbox={bbox-epsg-3857}&bboxSR=3857&imageSR=3857&size=256,256&format=png32&transparent=true&layers=show:28&f=image"
    : ABS + "/api/tiles/fema/{z}/{x}/{y}.png"], tileSize: 256, minzoom: 8, maxzoom: 16, attribution: "FEMA NFHL" });
  map.addLayer({ id: "fema", type: "raster", source: "fema", minzoom: 8, layout: { visibility: "none" }, paint: { "raster-opacity": 0.6 } });

  addGeo("waterways");
  map.addLayer({ id: "waterways", type: "line", source: "waterways", layout: { visibility: "none", "line-cap": "round" },
    paint: { "line-color": "#2f7fd6", "line-width": ["match", ["get", "waterway"], "river", 2.2, "canal", 1.6, 1], "line-opacity": 0.85 } });
  addGeo("roads");
  map.addLayer({ id: "roads", type: "line", source: "roads", layout: { visibility: "none" },
    paint: { "line-color": "#e8a33a", "line-width": ["match", ["get", "highway"], ["motorway", "trunk"], 3, ["primary", "secondary"], 2, 1], "line-opacity": 0.8 } });

  map.addLayer({ id: "aoi-line", type: "line", source: "aoi", paint: { "line-color": "#0b6e99", "line-width": 2, "line-dasharray": [3, 2] } });

  addGeo("routes");
  map.addLayer({ id: "route-base", type: "line", source: "routes", filter: ["==", ["get", "kind"], "baseline"],
    paint: { "line-color": "#6b7c8c", "line-width": 4, "line-dasharray": [2, 1.5], "line-opacity": 0.9 } });
  map.addLayer({ id: "route-safe-casing", type: "line", source: "routes", filter: ["==", ["get", "kind"], "safe"], layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#fff", "line-width": 8 } });
  map.addLayer({ id: "route-safe", type: "line", source: "routes", filter: ["==", ["get", "kind"], "safe"], layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#18a058", "line-width": 5 } });
  addGeo("traces");
  map.addLayer({ id: "traces", type: "line", source: "traces", layout: { "line-cap": "round" },
    paint: { "line-color": "#1f6fd1", "line-width": 3.5, "line-dasharray": [1, 0.6] } });
  addGeo("pts");
  map.addLayer({ id: "pts", type: "circle", source: "pts",
    paint: { "circle-radius": 7, "circle-color": ["match", ["get", "role"], "start", "#18a058", "end", "#b3261e", "#1f6fd1"], "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });

  addGeo("infra");
  map.addLayer({ id: "infra", type: "circle", source: "infra", layout: { visibility: "none" },
    paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 6, 2.5, 12, 6], "circle-color": ["match", ["get", "class"], ...Object.entries(INFRA_COLORS).flat(), "#666"],
      "circle-stroke-color": "#fff", "circle-stroke-width": 1 } });
  addGeo("exposed");
  map.addLayer({ id: "exposed", type: "circle", source: "exposed",
    paint: { "circle-radius": 9, "circle-color": "rgba(0,0,0,0)", "circle-stroke-width": 2.5,
      "circle-stroke-color": ["match", ["get", "level"], "Severe", "#8a0f2b", "High", "#d9480f", "#e8a33a"] } });

  addGeo("ann");
  map.addLayer({ id: "ann-fill", type: "fill", source: "ann", filter: ["==", ["geometry-type"], "Polygon"], paint: { "fill-color": ["get", "color"], "fill-opacity": 0.18 } });
  map.addLayer({ id: "ann-line", type: "line", source: "ann", filter: ["!=", ["geometry-type"], "Point"], paint: { "line-color": ["get", "color"], "line-width": 2.5 } });
  map.addLayer({ id: "ann-pt", type: "circle", source: "ann", filter: ["==", ["geometry-type"], "Point"], paint: { "circle-radius": 5, "circle-color": ["get", "color"], "circle-stroke-color": "#fff", "circle-stroke-width": 1.5 } });
  addGeo("draft");
  map.addLayer({ id: "draft-fill", type: "fill", source: "draft", filter: ["==", ["geometry-type"], "Polygon"], paint: { "fill-color": "#0b6e99", "fill-opacity": 0.12 } });
  map.addLayer({ id: "draft-line", type: "line", source: "draft", filter: ["!=", ["geometry-type"], "Point"], paint: { "line-color": "#0b6e99", "line-width": 2, "line-dasharray": [2, 1] } });
  map.addLayer({ id: "draft-pt", type: "circle", source: "draft", filter: ["==", ["geometry-type"], "Point"], paint: { "circle-radius": 4, "circle-color": "#fff", "circle-stroke-color": "#0b6e99", "circle-stroke-width": 2 } });

  for (const id of ["infra", "exposed"]) {
    map.on("mouseenter", id, () => { if (!S.mode) map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", id, () => { if (!S.mode) map.getCanvas().style.cursor = ""; });
  }
  boot();
});

// ------------------------------------------------------------------- boot
async function boot() {
  initForm();
  renderAccount();
  try {
    S.config = await api("/api/config");
    $("#verTag").textContent = "v" + S.config.version;
    if (DEMO) setupDemo();
    const m = await api("/api/model");
    const rt = m.runtime || {};
    const prov = (rt.providers || [])[0] || rt.mode || "";
    $("#pillBackend").className = "pill ok";
    $("#pillBackend").textContent = DEMO ? "Static demo" : m.backend === "remote" ? "Remote inference" : "Local inference";
    $("#pillBackend").title = m.backend === "remote" ? m.url : "Model runs on this server";
    $("#pillModel").textContent = m.backend === "remote" ? ((m.card || {}).name || "remote model") :
      `${m.card.name} · ${prov.replace("ExecutionProvider", "")}`;
    if (S.config.offline) $("#ctxNote").textContent = "Server is in offline mode: context layers are synthetic stand-ins.";
  } catch (e) {
    $("#pillBackend").className = "pill warn";
    $("#pillBackend").textContent = "Backend unreachable";
    toast("Can't reach the backend at " + (API || location.origin) + ": " + e.message, true);
  }
  try {
    S.aois = await api("/api/aois");
    fillAois();
  } catch (e) { toast("AOIs: " + e.message, true); }
  renderAnnotations();
  renderWorkspace();
  // restore last browser session (AOI, run, annotations, view)
  const st = store.get("lastState");
  if (st && st.aoi) restoreState(st).catch((e) => console.warn("restore failed", e));
}

// ------------------------------------------------------------------- demo
function demoRunsFor(aoiId) { return ((S.config && S.config.runs) || []).filter((r) => r.aoi_id === aoiId); }

function setupDemo() {
  document.body.classList.add("demo");
  const b = $("#demoBanner");
  b.hidden = false;
  b.innerHTML = `<b>Static demo</b> · precomputed runs (generated ${esc((S.config.generated || "").slice(0, 10))}). Map, layers, point query, flow tracing and annotations work here; new HRRR runs, road routing, accounts and PDF reports need the live backend. <a href="factsheet.html" target="_blank" rel="noopener">How to run it →</a>`;
  $("#btnAccount").style.display = "none";
  $("#source").previousElementSibling.style.display = "none";
  $("#source").style.display = "none";
  $("#btnRun").textContent = "Load demo run";
  $("#btnRoute").textContent = "Road routing (live backend only)";
  $("#btnReport").textContent = "PDF report (live backend only)";
  const box = document.createElement("div");
  box.id = "demoRuns";
  $("#btnRun").before(box);
}

function renderDemoRuns() {
  const box = $("#demoRuns");
  if (!box) return;
  const runs = S.aoi ? demoRunsFor(S.aoi.id) : [];
  box.innerHTML = runs.length ? `<label class="f">Demo runs</label><div class="list">${runs.map((r, i) => `
    <button class="btn sm" style="justify-content:flex-start;text-align:left" data-dr="${i}">${esc(r.date)} ${String(r.cycle).padStart(2, "0")}z +${r.fxx}h · ${esc(r.label || "")}${r.source === "synthetic" ? " (synthetic)" : ""}</button>`).join("")}</div>`
    : (S.aoi ? '<div class="note warn">No precomputed run for this AOI in the demo.</div>' : "");
  box.querySelectorAll("[data-dr]").forEach((btn) => btn.addEventListener("click", () => {
    const r = runs[+btn.dataset.dr];
    $("#date").value = r.date; $("#cycle").value = r.cycle; $("#fxx").value = r.fxx; startRun();
  }));
  if (runs.length) { $("#date").value = runs[0].date; $("#cycle").value = runs[0].cycle; $("#fxx").value = runs[0].fxx; }
}

function initForm() {
  const d = new Date(Date.now() - 864e5);
  $("#date").value = d.toISOString().slice(0, 10);
  $("#date").max = new Date().toISOString().slice(0, 10);
  $("#date").min = "2014-07-30"; // HRRR archive on AWS starts mid-2014
  $("#cycle").innerHTML = Array.from({ length: 24 }, (_, h) => `<option value="${h}" ${h === 12 ? "selected" : ""}>${String(h).padStart(2, "0")}z</option>`).join("");
  $("#fxx").innerHTML = Array.from({ length: 18 }, (_, i) => `<option value="${i + 1}">+${i + 1} h</option>`).join("");
}

// -------------------------------------------------------------------- tabs
$$("#tabs button").forEach((b) => b.addEventListener("click", () => selectTab(b.dataset.tab)));
function selectTab(t) {
  $$("#tabs button").forEach((x) => x.classList.toggle("active", x.dataset.tab === t));
  $$(".pane").forEach((p) => p.classList.toggle("active", p.id === "pane-" + t));
}

// -------------------------------------------------------------------- AOIs
function fillAois() {
  const groups = {};
  S.aois.forEach((a) => (groups[a.region] = groups[a.region] || []).push(a));
  $("#aoiSelect").innerHTML = `<option value="">Select an AOI…</option>` + Object.entries(groups).map(([g, arr]) =>
    `<optgroup label="${esc(g)}">${arr.map((a) => `<option value="${esc(a.id)}">${esc(a.name)}</option>`).join("")}</optgroup>`).join("");
  map.getSource("aoi").setData({ type: "FeatureCollection", features: S.aois.map((a) => ({ type: "Feature", geometry: a.geometry, properties: { id: a.id } })) });
}

$("#aoiSelect").addEventListener("change", (e) => setAoi(e.target.value, true));
$$("[data-region]").forEach((b) => b.addEventListener("click", () => {
  const first = S.aois.find((a) => a.region === b.dataset.region);
  if (first) { $("#aoiSelect").value = first.id; setAoi(first.id, true); }
}));

function setAoi(id, fly) {
  S.aoi = S.aois.find((a) => a.id === id) || null;
  const feats = S.aoi ? [{ type: "Feature", geometry: S.aoi.geometry, properties: {} }] : S.aois.map((a) => ({ type: "Feature", geometry: a.geometry, properties: {} }));
  map.getSource("aoi").setData({ type: "FeatureCollection", features: feats });
  if (S.aoi) {
    $("#aoiHint").textContent = `${S.aoi.name} · ${S.aoi.informal ? "informal extent, " : ""}from ${S.aoi.source_file}`;
    if (fly) { const b = S.aoi.bbox; map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 40, duration: 900 }); }
    refreshContext();
  }
  if (DEMO) renderDemoRuns();
}

// --------------------------------------------------------------------- run
$("#btnRun").addEventListener("click", startRun);

async function startRun() {
  if (!S.aoi) { toast("Pick an AOI first", true); return; }
  const btn = $("#btnRun");
  btn.disabled = true;
  showProgress(2, "Submitting…");
  try {
    const body = { aoi_id: S.aoi.id, date: $("#date").value, cycle: +$("#cycle").value, fxx: +$("#fxx").value };
    if ($("#source").value) body.source = $("#source").value;
    const { id } = await api("/api/runs", { method: "POST", json: body });
    const meta = await poll(id);
    await showRun(meta);
    toast(`Done in ${meta.timings.total_s}s (inference ${meta.timings.inference_s}s)`);
  } catch (e) {
    toast("Run failed: " + e.message, true);
    showProgress(0, "Failed: " + e.message);
  } finally { btn.disabled = false; }
}

function showProgress(pct, lbl) {
  $("#progress").classList.add("show");
  $("#progFill").style.width = pct + "%";
  $("#progLbl").textContent = lbl;
}

async function poll(id) {
  for (;;) {
    const st = await api("/api/runs/" + id);
    if (st.status === "done") { showProgress(100, "Complete"); return st; }
    if (st.status === "error") throw new Error(st.error);
    showProgress(st.progress || 5, st.step || st.status);
    await new Promise((r) => setTimeout(r, 500));
  }
}

const DEFAULT_ON = { apcp_ds: true, hazard: false };

async function showRun(meta) {
  // remove previous prediction layers
  if (S.run) for (const k of Object.keys(S.run.layers)) {
    if (map.getLayer("pred-" + k)) map.removeLayer("pred-" + k);
    if (map.getSource("pred-" + k)) map.removeSource("pred-" + k);
  }
  S.run = meta;
  for (const [k, L] of Object.entries(meta.layers)) {
    if (S.predVisible[k] === undefined) S.predVisible[k] = !!DEFAULT_ON[k];
    if (S.predOpacity[k] === undefined) S.predOpacity[k] = 0.8;
    map.addSource("pred-" + k, { type: "image", url: fileUrl(meta.id, L.png), coordinates: L.corners });
    map.addLayer({ id: "pred-" + k, type: "raster", source: "pred-" + k,
      layout: { visibility: S.predVisible[k] ? "visible" : "none" },
      paint: { "raster-opacity": S.predOpacity[k], "raster-fade-duration": 0, "raster-resampling": "linear" } }, "fema");
  }
  renderRunResult();
  renderPredLayers();
  renderLegend();
  renderExposed();
  $("#routeNeedRun").style.display = "none";
  autosave();
}

function fmtT(k) { return k.replace(/_s$/, "").replace(/_/g, " "); }

function renderRunResult() {
  const m = S.run;
  const hz = m.hazard.percent_area;
  const rt = (m.model && m.model.runtime) || {};
  const card = (m.model && m.model.card) || {};
  const sf = card.scale_factor || "";
  const stats = Object.entries(m.stats).map(([k, v]) => {
    const t = k.includes("tmp"); const o = t ? -273.15 : 0; const u = t ? "°C" : "mm";
    return `<tr><td>${esc(k)}</td><td>${(v.mean + o).toFixed(1)} / ${(v.max + o).toFixed(1)} ${u}</td></tr>`;
  }).join("");
  const fb = m.hrrr.fallback_errors && Object.keys(m.hrrr.fallback_errors).length
    ? `<div class="note warn">HRRR source fallback: ${esc(Object.entries(m.hrrr.fallback_errors).map(([k, v]) => k + ": " + v).join("; "))}</div>` : "";
  $("#runResult").innerHTML = `
    ${m.demo_label ? `<div class="note">Demo run: ${esc(m.demo_label)}</div>` : ""}
    ${m.hrrr.source === "synthetic" ? '<div class="note warn">Using <b>synthetic</b> HRRR fields (offline/test).</div>' : ""}${fb}
    <h3>Run ${esc(m.id)}</h3>
    <table class="kv">
      <tr><td>AOI</td><td>${esc(m.aoi.name)}</td></tr>
      <tr><td>Init → valid</td><td>${esc(m.init_time)} → ${esc(m.valid_time)}</td></tr>
      <tr><td>HRRR source</td><td>${esc(m.hrrr.source)}${m.hrrr.cached ? " (cached)" : ""}</td></tr>
      <tr><td>Model</td><td>${esc(card.name || "remote")} ×${sf}</td></tr>
      <tr><td>Runtime</td><td>${esc(rt.mode || "")} ${esc(((rt.providers || [])[0] || "").replace("ExecutionProvider", ""))}</td></tr>
      <tr><td>Grid in → out</td><td>${m.hrrr.staging_grid.width}×${m.hrrr.staging_grid.height} → ${m.grids.fine.width}×${m.grids.fine.height}</td></tr>
    </table>
    <h3>Timing (s)</h3>
    <table class="kv">${Object.entries(m.timings).map(([k, v]) => `<tr><td>${fmtT(k)}</td><td>${v}</td></tr>`).join("")}</table>
    <h3>Prediction (mean / max)</h3>
    <table class="kv">${stats}<tr><td>Hazard index</td><td>${m.hazard.mean} / ${m.hazard.max}</td></tr></table>
    <h3>Hazard class, % of AOI</h3>
    <table class="kv">${Object.entries(hz).map(([k, v]) => `<tr><td>${k}</td><td>${v}%</td></tr>`).join("")}</table>
    <p class="hint">Context: DEM ${esc(m.context_sources.dem)} · FEMA ${esc(m.context_sources.fema)}</p>
    <p class="hint"><a href="${fileUrl(m.id, "meta.json")}" target="_blank" rel="noopener">Run metadata (JSON)</a> · Click the map to query values.</p>`;
}

function renderPredLayers() {
  const box = $("#predLayers");
  box.innerHTML = Object.entries(S.run.layers).map(([k, L]) => {
    const st = L.legend.stops;
    const grad = `linear-gradient(90deg, ${st.map((s) => s.color).join(",")})`;
    return `<div class="layer"><label><input type="checkbox" data-pred="${k}" ${S.predVisible[k] ? "checked" : ""}/>
      <span class="sw" style="background:${grad}"></span><span>${esc(L.title)}<span class="src">${L.grid.width}×${L.grid.height} @ ${(L.grid.res * 111).toFixed(2)} km</span></span></label>
      <input type="range" min="0" max="1" step="0.05" value="${S.predOpacity[k]}" data-predop="${k}" /></div>`;
  }).join("");
  box.querySelectorAll("[data-pred]").forEach((c) => c.addEventListener("change", () => {
    S.predVisible[c.dataset.pred] = c.checked;
    map.setLayoutProperty("pred-" + c.dataset.pred, "visibility", c.checked ? "visible" : "none");
    renderLegend();
  }));
  box.querySelectorAll("[data-predop]").forEach((r) => r.addEventListener("input", () => {
    S.predOpacity[r.dataset.predop] = +r.value;
    map.setPaintProperty("pred-" + r.dataset.predop, "raster-opacity", +r.value);
  }));
}

function visiblePred() { return S.run ? Object.keys(S.run.layers).filter((k) => S.predVisible[k]) : []; }

function renderLegend() {
  const vis = visiblePred().slice(-2);
  const el = $("#legend");
  if (!vis.length) { el.classList.remove("show"); return; }
  el.innerHTML = vis.map((k) => {
    const L = S.run.layers[k].legend;
    const st = L.stops;
    return `<div style="margin-bottom:6px"><div class="ttl">${esc(L.label)}</div>
      <div class="ramp" style="background:linear-gradient(90deg,${st.map((s) => s.color).join(",")})"></div>
      <div class="ticks">${st.map((s) => `<span>${s.value}</span>`).join("")}</div></div>`;
  }).join("");
  el.classList.add("show");
}

// -------------------------------------------------------------- context
$$("[data-ctx]").forEach((c) => c.addEventListener("change", () => {
  S.ctxOn[c.dataset.ctx] = c.checked;
  const id = c.dataset.ctx === "infrastructure" ? "infra" : c.dataset.ctx;
  map.setLayoutProperty(id, "visibility", c.checked ? "visible" : "none");
  if (c.checked) refreshContext(c.dataset.ctx);
  if (c.dataset.ctx === "fema" && c.checked && map.getZoom() < 8) toast("FEMA flood zones draw from zoom 8; zoom in to see them.");
  autosave();
}));
$$("[data-op]").forEach((r) => r.addEventListener("input", () => map.setPaintProperty(r.dataset.op, "raster-opacity", +r.value)));

const ctxLoaded = {};
async function refreshContext(only) {
  const kinds = only ? [only] : Object.keys(S.ctxOn).filter((k) => S.ctxOn[k]);
  for (const kind of kinds) {
    if (kind === "fema") continue; // raster tiles
    let bbox;
    if (kind === "roads") {
      if (map.getZoom() < 11) { $("#ctxNote").textContent = "Roads load at zoom 11+."; continue; }
      const b = map.getBounds(); bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
    } else if (S.aoi) bbox = S.aoi.bbox;
    else { const b = map.getBounds(); bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]; }
    const key = kind + bbox.map((v) => v.toFixed(3)).join(",");
    if (ctxLoaded[kind] === key) continue;
    ctxLoaded[kind] = key;
    try {
      $("#ctxNote").textContent = `Loading ${kind}…`;
      const gj = await api(`/api/layers/${kind}?bbox=${bbox.map((v) => v.toFixed(5)).join(",")}`);
      map.getSource(kind === "infrastructure" ? "infra" : kind).setData(gj);
      $("#ctxNote").textContent = `${kind}: ${gj.features.length} features · ${gj.source || ""}${gj.error ? " (fallback: " + gj.error.slice(0, 80) + ")" : ""}`;
    } catch (e) { ctxLoaded[kind] = null; $("#ctxNote").textContent = `${kind}: ${e.message}`; }
  }
}
let moveT;
map.on("moveend", () => { clearTimeout(moveT); moveT = setTimeout(() => { if (S.ctxOn.roads) refreshContext("roads"); }, 400); });

$$("#basemapSeg button").forEach((b) => b.addEventListener("click", () => setBasemap(b.dataset.bm)));
function setBasemap(bm) {
  S.basemap = bm;
  $$("#basemapSeg button").forEach((x) => x.classList.toggle("on", x.dataset.bm === bm));
  for (const k of Object.keys(BASEMAPS)) map.setLayoutProperty("bm-" + k, "visibility", k === bm ? "visible" : "none");
}

// ------------------------------------------------------------- modes
function setMode(mode, text) {
  S.mode = mode;
  S.draft = [];
  map.getSource("draft").setData(EMPTY());
  $("#modebar").classList.toggle("show", !!mode);
  $("#modeText").textContent = text || "";
  $("#modeFinish").style.display = mode && mode.kind === "draw" && mode.geom !== "Point" ? "" : "none";
  map.getCanvas().style.cursor = mode ? "crosshair" : "";
  if (mode && mode.kind === "draw") map.doubleClickZoom.disable(); else map.doubleClickZoom.enable();
  $$("#drawSeg button").forEach((x) => x.classList.toggle("on", !!(mode && mode.kind === "draw" && mode.geom === x.dataset.draw)));
}
$("#modeCancel").addEventListener("click", () => { setMode(null); $("#annForm").style.display = "none"; });
$("#modeFinish").addEventListener("click", finishDraft);
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && S.mode) { setMode(null); $("#annForm").style.display = "none"; } });

map.on("click", async (e) => {
  const p = [+e.lngLat.lng.toFixed(6), +e.lngLat.lat.toFixed(6)];
  if (!S.mode) return queryPoint(e);
  if (S.mode.kind === "route") {
    S.routePts.push(p);
    drawPts();
    if (S.routePts.length === 1) $("#modeText").textContent = "Now click the destination";
    else { setMode(null); await doRoute(); }
  } else if (S.mode.kind === "trace") {
    setMode(null); await doTrace(p);
  } else if (S.mode.kind === "draw") {
    S.draft.push(p);
    if (S.mode.geom === "Point") return finishDraft();
    renderDraft();
  }
});
map.on("dblclick", (e) => { if (S.mode && S.mode.kind === "draw") { e.preventDefault(); S.draft.pop(); finishDraft(); } });

async function queryPoint(e) {
  const f = map.queryRenderedFeatures(e.point, { layers: ["infra", "exposed"] })[0];
  if (f) {
    const p = f.properties;
    new maplibregl.Popup().setLngLat(e.lngLat).setHTML(`<b>${esc(p.name || "(unnamed)")}</b><br>${esc(p.label || p.class || "")}${p.level ? `<br><span class="lvl ${esc(p.level)}">${esc(p.level)} ${(+p.hazard).toFixed(2)}</span>` : ""}`).addTo(map);
    return;
  }
  if (!S.run) return;
  try {
    const v = await api(`/api/runs/${S.run.id}/sample?lon=${e.lngLat.lng}&lat=${e.lngLat.lat}`);
    if (!v.inside) return;
    const rows = Object.entries(v).filter(([k]) => !["inside", "lon", "lat", "hazard_class"].includes(k)).map(([k, val]) => {
      let s = val; if (k.includes("tmp")) s = (val - 273.15).toFixed(1) + " °C"; else if (k.includes("apcp")) s = val.toFixed(2) + " mm"; else if (k === "runoff_m3") s = Math.round(val).toLocaleString() + " m³";
      return `<tr><td>${esc(k)}</td><td>${esc(s)}</td></tr>`;
    }).join("");
    new maplibregl.Popup().setLngLat(e.lngLat).setHTML(`<b>${v.lat.toFixed(4)}, ${v.lon.toFixed(4)}</b> <span class="lvl ${esc(v.hazard_class)}">${esc(v.hazard_class)}</span><table class="kv">${rows}</table>`).addTo(map);
  } catch (err) { toast(err.message, true); }
}

// ---------------------------------------------------------------- routing
$("#thr").addEventListener("input", () => ($("#thrVal").textContent = (+$("#thr").value).toFixed(2)));
$("#btnRoute").addEventListener("click", () => {
  if (DEMO) { $("#routeResult").innerHTML = '<div class="note warn">Road routing needs the live backend (OSM road graph). Run the portal locally with <code>ppfe serve</code>; see the <a href="factsheet.html" target="_blank" rel="noopener">fact sheet</a>.</div>'; return; }
  if (!S.run) return toast("Run the model first", true);
  S.routePts = []; drawPts(); setMode({ kind: "route" }, "Click the start point");
});
$("#btnTrace").addEventListener("click", () => {
  if (!S.run) return toast("Run the model first", true);
  setMode({ kind: "trace" }, "Click where the runoff starts");
});
$("#btnClearRoutes").addEventListener("click", () => {
  S.routes = null; S.traces = EMPTY(); S.routePts = [];
  map.getSource("routes").setData(EMPTY()); map.getSource("traces").setData(EMPTY()); drawPts();
  $("#routeResult").innerHTML = ""; $("#traceResult").innerHTML = ""; autosave();
});

function drawPts() {
  const roles = ["start", "end"];
  map.getSource("pts").setData({ type: "FeatureCollection", features: S.routePts.map((c, i) => ({ type: "Feature", geometry: { type: "Point", coordinates: c }, properties: { role: roles[i] } })) });
}

async function doRoute() {
  $("#routeResult").innerHTML = '<p class="hint">Building road graph and routing…</p>';
  try {
    const r = await api(`/api/runs/${S.run.id}/route`, { method: "POST", json: { start: S.routePts[0], end: S.routePts[1], block_threshold: +$("#thr").value } });
    S.routes = { ...r, start: S.routePts[0], end: S.routePts[1], threshold: +$("#thr").value };
    drawRoutes();
    autosave();
  } catch (e) { $("#routeResult").innerHTML = `<div class="note warn">${esc(e.message)}</div>`; }
}

function drawRoutes() {
  const r = S.routes;
  if (!r) return;
  const feats = ["baseline", "safe"].filter((k) => r[k]).map((k) => ({ type: "Feature", geometry: { type: "LineString", coordinates: r[k].coordinates }, properties: { kind: k } }));
  map.getSource("routes").setData({ type: "FeatureCollection", features: feats });
  S.routePts = [r.start, r.end]; drawPts();
  const row = (lbl, x, color) => x ? `<tr><td><span class="sw" style="display:inline-block;width:10px;height:10px;background:${color};margin-right:6px"></span>${lbl}</td><td>${x.length_km} km · ${x.minutes} min · max hz ${x.max_hazard}</td></tr>` : `<tr><td>${lbl}</td><td>no path</td></tr>`;
  const delay = r.baseline && r.safe ? (r.safe.minutes - r.baseline.minutes).toFixed(1) : null;
  $("#routeResult").innerHTML = `<table class="kv" style="margin-top:10px">${row("Fastest", r.baseline, "#6b7c8c")}${row("Hazard-aware", r.safe, "#18a058")}</table>
    <p class="hint">${r.blocked_edges} road segments closed · ${delay !== null ? "detour cost " + delay + " min · " : ""}${esc(r.network_source || "")}</p>
    ${r.safe ? "" : '<div class="note warn">No hazard-free path at this threshold. Raise the threshold or choose other points.</div>'}`;
}

async function doTrace(p) {
  try {
    const f = await api(`/api/runs/${S.run.id}/trace?lon=${p[0]}&lat=${p[1]}`);
    S.traces.features.push(f);
    map.getSource("traces").setData(S.traces);
    const pr = f.properties;
    $("#traceResult").innerHTML = `<table class="kv" style="margin-top:10px"><tr><td>Path length</td><td>~${pr.approx_km} km</td></tr>
      <tr><td>Max hazard on path</td><td>${pr.max_hazard}</td></tr><tr><td>Runoff at end</td><td>${Math.round(pr.outlet_runoff_m3).toLocaleString()} m³</td></tr>
      <tr><td>Traces</td><td>${S.traces.features.length}</td></tr></table>`;
    autosave();
  } catch (e) { toast(e.message, true); }
}

function renderExposed() {
  const list = S.run.exposed_infrastructure || [];
  map.getSource("exposed").setData({ type: "FeatureCollection", features: list.map((x) => ({ type: "Feature", geometry: { type: "Point", coordinates: [x.lon, x.lat] }, properties: { ...x, label: x.class } })) });
  $("#exposedList").innerHTML = list.length ? `<div class="list">${list.slice(0, 40).map((x, i) => `
    <div class="item"><div class="t"><span>${esc(x.name)}</span><span class="lvl ${esc(x.level)}">${esc(x.level)} ${x.hazard.toFixed(2)}</span></div>
    <div class="d">${esc(x.class)}</div><div class="acts"><button class="btn sm" data-zoomx="${i}">Zoom</button></div></div>`).join("")}</div>`
    : '<div class="empty">No facilities in moderate-or-worse hazard.</div>';
  $$("[data-zoomx]").forEach((b) => b.addEventListener("click", () => { const x = list[+b.dataset.zoomx]; map.flyTo({ center: [x.lon, x.lat], zoom: 13 }); }));
}

// ------------------------------------------------------------- annotate
$$("#drawSeg button").forEach((b) => b.addEventListener("click", () => {
  S.editingId = null;
  const g = b.dataset.draw;
  setMode({ kind: "draw", geom: g }, g === "Point" ? "Click to place a point" : g === "LineString" ? "Click vertices · double-click to finish" : "Click corners · double-click to finish");
}));
$$("#annColor button").forEach((b) => b.addEventListener("click", () => {
  S.annColor = b.dataset.c; $$("#annColor button").forEach((x) => x.classList.toggle("on", x === b));
}));

function draftGeom() {
  const g = S.mode && S.mode.geom;
  if (g === "Point") return { type: "Point", coordinates: S.draft[0] };
  if (g === "LineString") return { type: "LineString", coordinates: S.draft };
  return { type: "Polygon", coordinates: [[...S.draft, S.draft[0]]] };
}
function renderDraft() {
  const feats = S.draft.map((c) => ({ type: "Feature", geometry: { type: "Point", coordinates: c }, properties: {} }));
  if (S.draft.length >= 2) feats.push({ type: "Feature", geometry: S.mode.geom === "Polygon" && S.draft.length >= 3 ? draftGeom() : { type: "LineString", coordinates: S.draft }, properties: {} });
  map.getSource("draft").setData({ type: "FeatureCollection", features: feats });
}
let pendingGeom = null;
function finishDraft() {
  const g = S.mode && S.mode.geom;
  const need = g === "Point" ? 1 : g === "LineString" ? 2 : 3;
  if (S.draft.length < need) { toast(`Need at least ${need} points`, true); return; }
  pendingGeom = draftGeom();
  const keep = { type: "FeatureCollection", features: [{ type: "Feature", geometry: pendingGeom, properties: {} }] };
  setMode(null);
  map.getSource("draft").setData(keep);
  openAnnForm({ title: "", note: "", color: S.annColor });
}
function openAnnForm(p) {
  selectTab("annotate");
  $("#annForm").style.display = "";
  $("#annTitle").value = p.title || ""; $("#annNote").value = p.note || "";
  S.annColor = p.color || "#d62f2f";
  $$("#annColor button").forEach((x) => x.classList.toggle("on", x.dataset.c === S.annColor));
  $("#annTitle").focus();
}
$("#annCancel").addEventListener("click", () => { $("#annForm").style.display = "none"; pendingGeom = null; S.editingId = null; map.getSource("draft").setData(EMPTY()); });
$("#annSave").addEventListener("click", () => {
  const props = { title: $("#annTitle").value.trim() || "Untitled", note: $("#annNote").value.trim(), color: S.annColor,
    author: (S.user && (S.user.name || S.user.email)) || "", updated: new Date().toISOString() };
  if (S.editingId) {
    const f = S.annotations.features.find((x) => x.id === S.editingId);
    Object.assign(f.properties, props);
  } else if (pendingGeom) {
    const id = "a" + Date.now().toString(36);
    S.annotations.features.push({ type: "Feature", id, geometry: pendingGeom, properties: { id, created: props.updated, run_id: S.run && S.run.id, ...props } });
  }
  pendingGeom = null; S.editingId = null;
  $("#annForm").style.display = "none";
  map.getSource("draft").setData(EMPTY());
  renderAnnotations(); autosave();
});

function labelPoint(g) {
  if (g.type === "Point") return g.coordinates;
  if (g.type === "LineString") return g.coordinates[Math.floor(g.coordinates.length / 2)];
  const r = g.coordinates[0].slice(0, -1);
  return [r.reduce((s, c) => s + c[0], 0) / r.length, r.reduce((s, c) => s + c[1], 0) / r.length];
}
let annMarkers = [];
function renderAnnotations() {
  if (!map.getSource("ann")) return;
  const fc = S.annotations;
  fc.features.forEach((f) => { if (!f.id) f.id = (f.properties && f.properties.id) || "a" + Math.random().toString(36).slice(2, 9); });
  map.getSource("ann").setData(fc);
  annMarkers.forEach((m) => m.remove());
  annMarkers = fc.features.map((f, i) => {
    const el = document.createElement("div");
    el.className = "ann-label"; el.textContent = i + 1; el.style.background = f.properties.color || "#d62f2f";
    el.title = f.properties.title || "";
    el.addEventListener("click", (ev) => { ev.stopPropagation(); new maplibregl.Popup({ offset: 14 }).setLngLat(labelPoint(f.geometry)).setHTML(`<b>${i + 1}. ${esc(f.properties.title)}</b><div style="white-space:pre-wrap;margin-top:4px">${esc(f.properties.note)}</div>`).addTo(map); });
    return new maplibregl.Marker({ element: el, offset: f.geometry.type === "Point" ? [0, -16] : [0, 0] }).setLngLat(labelPoint(f.geometry)).addTo(map);
  });
  $("#annCount").textContent = fc.features.length ? `(${fc.features.length})` : "";
  $("#annList").innerHTML = fc.features.length ? fc.features.map((f, i) => `
    <div class="item"><div class="t"><span class="num" style="background:${esc(f.properties.color)}">${i + 1}</span>${esc(f.properties.title)}</div>
    ${f.properties.note ? `<div class="d">${esc(f.properties.note)}</div>` : ""}
    <div class="acts"><button class="btn sm" data-az="${i}">Zoom</button><button class="btn sm" data-ae="${i}">Edit</button><button class="btn sm danger" data-ad="${i}">Delete</button></div></div>`).join("")
    : '<div class="empty">No annotations yet.</div>';
  $$("[data-az]").forEach((b) => b.addEventListener("click", () => zoomTo(fc.features[+b.dataset.az].geometry)));
  $$("[data-ae]").forEach((b) => b.addEventListener("click", () => { const f = fc.features[+b.dataset.ae]; S.editingId = f.id; openAnnForm(f.properties); }));
  $$("[data-ad]").forEach((b) => b.addEventListener("click", () => { fc.features.splice(+b.dataset.ad, 1); renderAnnotations(); autosave(); }));
}
function zoomTo(g) {
  const cs = g.type === "Point" ? [g.coordinates] : g.type === "LineString" ? g.coordinates : g.coordinates[0];
  if (cs.length === 1) return map.flyTo({ center: cs[0], zoom: Math.max(map.getZoom(), 13) });
  const xs = cs.map((c) => c[0]); const ys = cs.map((c) => c[1]);
  map.fitBounds([[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]], { padding: 80, maxZoom: 15 });
}

$("#annExport").addEventListener("click", () => {
  download(new Blob([JSON.stringify({ ...S.annotations, properties: { run_id: S.run && S.run.id, exported: new Date().toISOString() } }, null, 1)], { type: "application/geo+json" }),
    (S.projectName || "annotations").replace(/\W+/g, "_") + ".geojson");
});
$("#annImport").addEventListener("change", async (e) => {
  const f = e.target.files[0]; if (!f) return;
  try {
    const gj = JSON.parse(await f.text());
    const feats = (gj.type === "FeatureCollection" ? gj.features : [gj]).filter((x) => x.geometry && ["Point", "LineString", "Polygon"].includes(x.geometry.type));
    feats.forEach((x) => { x.properties = { title: "Imported", note: "", color: "#6b2fb3", ...(x.properties || {}) }; });
    S.annotations.features.push(...feats); renderAnnotations(); autosave(); toast(`Imported ${feats.length} features`);
  } catch (err) { toast("Import failed: " + err.message, true); }
  e.target.value = "";
});

function download(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

// -------------------------------------------------------------- workspace
function snapshotState() {
  const c = map.getCenter();
  return {
    aoi: S.aoi ? { id: S.aoi.id, name: S.aoi.name } : null,
    run_id: S.run ? S.run.id : null,
    hrrr: { date: $("#date").value, cycle: +$("#cycle").value, fxx: +$("#fxx").value },
    annotations: S.annotations, routes: S.routes, traces: S.traces,
    layers: { pred: S.predVisible, opacity: S.predOpacity, ctx: S.ctxOn, basemap: S.basemap },
    view: { center: [c.lng, c.lat], zoom: map.getZoom() },
  };
}
let saveT;
function autosave() {
  clearTimeout(saveT);
  saveT = setTimeout(() => { store.set("annotations", S.annotations); store.set("lastState", snapshotState()); }, 300);
}

async function restoreState(st) {
  if (st.hrrr) { $("#date").value = st.hrrr.date; $("#cycle").value = st.hrrr.cycle; $("#fxx").value = st.hrrr.fxx; }
  if (st.aoi) { $("#aoiSelect").value = st.aoi.id; setAoi(st.aoi.id, false); }
  S.annotations = st.annotations || EMPTY();
  S.traces = st.traces || EMPTY(); map.getSource("traces").setData(S.traces);
  if (st.layers) {
    S.predVisible = st.layers.pred || {}; S.predOpacity = st.layers.opacity || {};
    setBasemap(st.layers.basemap || "osm");
    S.ctxOn = st.layers.ctx || {};
    $$("[data-ctx]").forEach((c) => { c.checked = !!S.ctxOn[c.dataset.ctx]; map.setLayoutProperty(c.dataset.ctx === "infrastructure" ? "infra" : c.dataset.ctx, "visibility", c.checked ? "visible" : "none"); });
  }
  if (st.view) map.jumpTo({ center: st.view.center, zoom: st.view.zoom });
  if (st.run_id) {
    try { const meta = await api("/api/runs/" + st.run_id); if (meta.status === "done") await showRun(meta); }
    catch { toast("Saved run " + st.run_id + " is no longer on this server; re-run to regenerate.", true); }
  }
  S.routes = st.routes || null;
  if (S.routes) drawRoutes(); else map.getSource("routes").setData(EMPTY());
  renderAnnotations(); refreshContext();
}

function renderWorkspace() {
  const box = $("#wsBox");
  if (DEMO) {
    box.innerHTML = '<p class="hint">In the demo, annotations stay in this browser. Use Export GeoJSON to keep them. Saved accounts and workspaces need the live backend.</p>';
    return;
  }
  if (!S.user) {
    box.innerHTML = `<p class="hint">Annotations are kept in this browser. Log in to save workspaces to your account and reopen them anywhere.</p>
      <button class="btn" id="wsLogin">Log in / create account</button>`;
    $("#wsLogin").addEventListener("click", () => openAuth(false));
    return;
  }
  box.innerHTML = `<label class="f" for="wsName">Workspace name</label>
    <input type="text" id="wsName" value="${esc(S.projectName)}" placeholder="e.g. Passaic event 2026-09-01" />
    <div class="row" style="margin-top:8px"><button class="btn primary" id="wsSave">${S.projectId ? "Save" : "Save workspace"}</button>${S.projectId ? '<button class="btn" id="wsSaveNew">Save as new</button>' : ""}</div>
    <h3>My workspaces</h3><div class="list" id="wsList"><div class="empty">Loading…</div></div>`;
  $("#wsSave").addEventListener("click", () => saveWorkspace(false));
  if ($("#wsSaveNew")) $("#wsSaveNew").addEventListener("click", () => saveWorkspace(true));
  loadWorkspaces();
}
async function saveWorkspace(asNew) {
  const name = $("#wsName").value.trim() || (S.aoi ? S.aoi.name : "Workspace") + " " + new Date().toISOString().slice(0, 10);
  try {
    const body = { name, state: snapshotState() };
    const p = S.projectId && !asNew ? await api("/api/projects/" + S.projectId, { method: "PUT", json: body }) : await api("/api/projects", { method: "POST", json: body });
    S.projectId = p.id; S.projectName = p.name; toast("Saved “" + p.name + "”"); renderWorkspace();
  } catch (e) { toast("Save failed: " + e.message, true); }
}
async function loadWorkspaces() {
  try {
    const list = await api("/api/projects");
    $("#wsList").innerHTML = list.length ? list.map((p) => `<div class="item"><div class="t">${esc(p.name)}${p.id === S.projectId ? ' <span class="lvl Moderate">open</span>' : ""}</div>
      <div class="d">${esc(p.aoi || "no AOI")} · ${p.n_annotations} annotations · ${esc(p.updated.replace("T", " ").slice(0, 16))}</div>
      <div class="acts"><button class="btn sm" data-po="${p.id}">Open</button><button class="btn sm" data-pe="${p.id}">GeoJSON</button><button class="btn sm danger" data-pd="${p.id}">Delete</button></div></div>`).join("")
      : '<div class="empty">No saved workspaces yet.</div>';
    $$("[data-po]").forEach((b) => b.addEventListener("click", async () => {
      const p = await api("/api/projects/" + b.dataset.po); S.projectId = p.id; S.projectName = p.name; await restoreState(p.state); renderWorkspace(); toast("Opened “" + p.name + "”");
    }));
    $$("[data-pe]").forEach((b) => b.addEventListener("click", async () => {
      const r = await api(`/api/projects/${b.dataset.pe}/export.geojson`, { raw: true }); download(await r.blob(), `workspace_${b.dataset.pe}.geojson`);
    }));
    $$("[data-pd]").forEach((b) => b.addEventListener("click", async () => {
      if (!confirm("Delete this workspace? This can't be undone.")) return;
      await api("/api/projects/" + b.dataset.pd, { method: "DELETE" }); if (+b.dataset.pd === S.projectId) { S.projectId = null; S.projectName = ""; } renderWorkspace();
    }));
  } catch (e) { $("#wsList").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

// ----------------------------------------------------------------- auth
let authSignup = false;
function renderAccount() {
  $("#btnAccount").textContent = S.user ? (S.user.name || S.user.email) + " · Log out" : "Log in";
}
$("#btnAccount").addEventListener("click", () => (S.user ? logout() : openAuth(false)));
function openAuth(signup) {
  authSignup = signup;
  $("#authTitle").textContent = signup ? "Create account" : "Log in";
  $("#authGo").textContent = signup ? "Create account" : "Log in";
  $("#authSwitch").textContent = signup ? "I have an account" : "Create an account";
  $("#authNameRow").style.display = signup ? "" : "none";
  $("#authPw").autocomplete = signup ? "new-password" : "current-password";
  $("#authErr").textContent = "";
  $("#authModal").classList.add("show");
  $("#authEmail").focus();
}
$("#authSwitch").addEventListener("click", () => openAuth(!authSignup));
$("#authCancel").addEventListener("click", () => $("#authModal").classList.remove("show"));
$("#authPw").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#authGo").click(); });
$("#authGo").addEventListener("click", async () => {
  const body = { email: $("#authEmail").value.trim(), password: $("#authPw").value };
  if (authSignup) body.name = $("#authName").value.trim();
  try {
    const r = await api(authSignup ? "/api/auth/signup" : "/api/auth/login", { method: "POST", json: body });
    S.token = r.token; S.user = r.user; store.set("token", r.token); store.set("user", r.user);
    $("#authModal").classList.remove("show"); $("#authPw").value = "";
    renderAccount(); renderWorkspace(); toast("Welcome, " + (r.user.name || r.user.email));
  } catch (e) { $("#authErr").textContent = e.message; }
});
function logout(expired) {
  S.token = null; S.user = null; S.projectId = null; S.projectName = "";
  store.del("token"); store.del("user"); renderAccount(); renderWorkspace();
  if (expired) toast("Session expired, please log in again", true);
}

// --------------------------------------------------------------- report
function composeMapImage() {
  const src = map.getCanvas();
  const dpr = src.width / src.clientWidth;
  const c = document.createElement("canvas");
  c.width = src.width; c.height = src.height;
  const g = c.getContext("2d");
  g.drawImage(src, 0, 0);
  // numbered annotation badges
  S.annotations.features.forEach((f, i) => {
    const p = map.project(labelPoint(f.geometry));
    const x = p.x * dpr; const y = (p.y - (f.geometry.type === "Point" ? 16 : 0)) * dpr; const r = 11 * dpr;
    g.beginPath(); g.arc(x, y, r, 0, Math.PI * 2); g.fillStyle = f.properties.color || "#d62f2f"; g.fill();
    g.lineWidth = 2 * dpr; g.strokeStyle = "#fff"; g.stroke();
    g.fillStyle = "#fff"; g.font = `bold ${11.5 * dpr}px system-ui, sans-serif`; g.textAlign = "center"; g.textBaseline = "middle";
    g.fillText(String(i + 1), x, y + 0.5 * dpr);
  });
  // legends
  const vis = visiblePred().slice(-2);
  let ly = c.height - 12 * dpr;
  vis.reverse().forEach((k) => {
    const L = S.run.layers[k].legend; const w = 230 * dpr; const h = 46 * dpr; const x = c.width - w - 12 * dpr; ly -= h;
    g.fillStyle = "rgba(255,255,255,.94)"; g.fillRect(x, ly, w, h);
    g.fillStyle = "#17232f"; g.font = `600 ${11 * dpr}px system-ui, sans-serif`; g.textAlign = "left"; g.textBaseline = "top";
    g.fillText(L.label, x + 8 * dpr, ly + 6 * dpr);
    const grad = g.createLinearGradient(x + 8 * dpr, 0, x + w - 8 * dpr, 0);
    L.stops.forEach((s, i) => grad.addColorStop(i / (L.stops.length - 1), s.color));
    g.fillStyle = grad; g.fillRect(x + 8 * dpr, ly + 21 * dpr, w - 16 * dpr, 8 * dpr);
    g.fillStyle = "#6b7c8c"; g.font = `${9.5 * dpr}px system-ui, sans-serif`;
    g.textAlign = "left"; g.fillText(String(L.stops[0].value), x + 8 * dpr, ly + 32 * dpr);
    g.textAlign = "right"; g.fillText(String(L.stops[L.stops.length - 1].value), x + w - 8 * dpr, ly + 32 * dpr);
    ly -= 6 * dpr;
  });
  // attribution
  const attr = "© OpenStreetMap contributors" + (S.ctxOn.fema ? " · FEMA NFHL" : "") + (S.basemap === "imagery" ? " · Esri" : S.basemap === "topo" ? " · USGS" : "");
  g.font = `${10 * dpr}px system-ui, sans-serif`; g.textAlign = "left"; g.textBaseline = "bottom";
  const tw = g.measureText(attr).width;
  g.fillStyle = "rgba(255,255,255,.85)"; g.fillRect(0, c.height - 16 * dpr, tw + 12 * dpr, 16 * dpr);
  g.fillStyle = "#333"; g.fillText(attr, 6 * dpr, c.height - 3 * dpr);
  return c.toDataURL("image/png");
}

$("#btnReport").addEventListener("click", async () => {
  const btn = $("#btnReport"); btn.disabled = true; btn.textContent = "Building PDF…";
  try {
    map.triggerRepaint();
    await new Promise((r) => map.once("idle", r));
    const routes = S.routes ? { baseline: S.routes.baseline, safe: S.routes.safe, blocked_edges: S.routes.blocked_edges } : (S.traces.features.length ? {} : null);
    if (routes && S.traces.features.length) routes.trace = S.traces.features[S.traces.features.length - 1].geometry.coordinates;
    const layersOn = [...visiblePred().map((k) => S.run.layers[k].title), ...Object.keys(S.ctxOn).filter((k) => S.ctxOn[k])];
    const body = { title: $("#repTitle").value, notes: $("#repNotes").value, run_id: S.run && S.run.id,
      map_png: composeMapImage(), annotations: S.annotations, routes, layers_on: layersOn };
    const r = await api("/api/report", { method: "POST", json: body, raw: true });
    download(await r.blob(), ($("#repTitle").value || "report").replace(/\W+/g, "_") + ".pdf");
    toast("Report downloaded");
  } catch (e) { toast("Report failed: " + e.message, true); }
  finally { btn.disabled = false; btn.textContent = "Generate PDF"; }
});

