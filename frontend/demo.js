/* Static demo backend for GitHub Pages.
 * Active when PPFE_CONFIG.DEMO is true. Serves precomputed runs from ./demo/
 * (built by scripts/build_demo.py in .github/workflows/pages.yml) and answers
 * point queries and flow traces in the browser. Features that need a live
 * server (new HRRR fetches, road routing, accounts, PDF) raise a clear message.
 */
"use strict";

window.PPFE_DEMO = (() => {
  const BASE = "demo/";
  const NEEDS_BACKEND = "needs the live backend. Run the portal locally with `ppfe serve` (see the fact sheet).";
  const cache = {};
  const json = (p) => (cache[p] = cache[p] || fetch(BASE + p).then((r) => { if (!r.ok) throw new Error("Demo file missing: " + p); return r.json(); }));
  const DR = [0, 1, 1, 1, 0, -1, -1, -1];
  const DC = [1, 1, 0, -1, -1, -1, 0, 1];

  async function arrays(id) {
    if (cache["arr:" + id]) return cache["arr:" + id];
    cache["arr:" + id] = (async () => {
      const [idx, meta] = await Promise.all([json(`runs/${id}/arrays.json`), json(`runs/${id}/meta.json`)]);
      const out = { meta };
      await Promise.all(Object.entries(idx).map(async ([k, a]) => {
        const buf = await (await fetch(`${BASE}runs/${id}/${a.file}`)).arrayBuffer();
        out[k] = { ...a, data: a.file.endsWith(".u8") ? new Uint8Array(buf) : new Uint16Array(buf), g: meta.grids[a.grid] };
      }));
      return out;
    })();
    return cache["arr:" + id];
  }

  const rc = (g, lon, lat) => [
    Math.min(g.height - 1, Math.max(0, Math.floor((g.north - lat) / g.res))),
    Math.min(g.width - 1, Math.max(0, Math.floor((lon - g.west) / g.res))),
  ];
  function value(a, lon, lat) {
    const [r, c] = rc(a.g, lon, lat);
    const q = a.data[r * a.g.width + c];
    const v = a.min + (q / 65535) * (a.max - a.min);
    return a.log10 ? Math.pow(10, v) : v;
  }
  const classify = (h) => (h >= 0.75 ? "Severe" : h >= 0.55 ? "High" : h >= 0.35 ? "Moderate" : "Low");
  const r3 = (x) => Math.round(x * 1000) / 1000;

  function qs(path) { return Object.fromEntries(new URL(path, "http://x").searchParams); }

  async function api(path, opts = {}) {
    const method = (opts.method || "GET").toUpperCase();
    const p = path.split("?")[0];
    const body = opts.body ? JSON.parse(opts.body) : null;

    if (p === "/api/config") return json("config.json");
    if (p === "/api/model") return json("model.json");
    if (p === "/api/aois") return json("aois.json");

    if (p === "/api/runs" && method === "POST") {
      const cfg = await json("config.json");
      const forAoi = cfg.runs.filter((r) => r.aoi_id === body.aoi_id);
      if (!forAoi.length) throw new Error("No precomputed demo run for this AOI. Choose one listed under 'Demo runs', or run locally.");
      const exact = forAoi.find((r) => r.date === body.date && r.cycle === body.cycle && r.fxx === body.fxx);
      return { id: (exact || forAoi[0]).id, substituted: !exact };
    }
    let m;
    if ((m = p.match(/^\/api\/runs\/([^/]+)$/))) {
      const meta = await json(`runs/${m[1]}/meta.json`);
      return { ...meta, demo: true };
    }
    if ((m = p.match(/^\/api\/runs\/([^/]+)\/sample$/))) {
      const { lon, lat } = qs(path); const x = +lon; const y = +lat;
      const A = await arrays(m[1]); const g = A.meta.grids.analysis;
      if (x < g.west || x > g.east || y < g.south || y > g.north) return { inside: false };
      const out = { inside: true, lon: x, lat: y };
      for (const [k, a] of Object.entries(A)) if (k !== "meta" && k !== "d8") out[k] = r3(value(a, x, y));
      out.hazard_class = classify(out.hazard);
      return out;
    }
    if ((m = p.match(/^\/api\/runs\/([^/]+)\/trace$/))) {
      const { lon, lat } = qs(path);
      const A = await arrays(m[1]); const d = A.d8; const g = d.g;
      let [r, c] = rc(g, +lon, +lat);
      const center = (r2, c2) => [g.west + (c2 + 0.5) * g.res, g.north - (r2 + 0.5) * g.res];
      const path_ = [center(r, c)]; const seen = new Set();
      for (let i = 0; i < 20000; i++) {
        const k = d.data[r * g.width + c] - d.offset;
        if (k < 0 || seen.has(r * g.width + c)) break;
        seen.add(r * g.width + c); r += DR[k]; c += DC[k];
        if (r < 0 || r >= g.height || c < 0 || c >= g.width) break;
        path_.push(center(r, c));
      }
      const hz = path_.map(([a, b]) => value(A.hazard, a, b));
      const end = path_[path_.length - 1];
      return { type: "Feature", geometry: { type: "LineString", coordinates: path_ },
        properties: { cells: path_.length, approx_km: Math.round(path_.length * g.res * 111.32 * Math.cos(+lat * Math.PI / 180) * 10) / 10,
          max_hazard: r3(Math.max(...hz)), outlet_runoff_m3: Math.round(value(A.runoff_m3, end[0], end[1])) } };
    }
    if ((m = p.match(/^\/api\/layers\/(\w+)$/))) {
      const kind = m[1];
      if (kind === "roads" || kind === "fema") throw new Error(`${kind} layer ${NEEDS_BACKEND}`);
      const bbox = qs(path).bbox.split(",").map(Number);
      const aois = await json("aois.json");
      const a = aois.find((x) => x.bbox.every((v, i) => Math.abs(v - bbox[i]) < 1e-3));
      if (!a) throw new Error("In the demo, context layers load for the selected AOI only.");
      try { return await json(`layers/${a.id}/${kind}.json`); }
      catch { throw new Error(`No precomputed ${kind} for ${a.name} in this demo.`); }
    }
    if (p.includes("/route")) throw new Error("Road routing " + NEEDS_BACKEND);
    if (p.startsWith("/api/auth") || p.startsWith("/api/projects")) throw new Error("Accounts and saved workspaces " + NEEDS_BACKEND);
    if (p === "/api/report") throw new Error("PDF reports " + NEEDS_BACKEND);
    throw new Error("Not available in the static demo: " + p);
  }

  return { api, fileUrl: (id, name) => `${BASE}runs/${id}/${name}`, json };
})();
