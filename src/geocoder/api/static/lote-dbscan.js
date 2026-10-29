"use strict";

// ---- Lote "Grupos → Análisis": DBSCAN clusters as a result, not a drawing.
// Every row gets a cluster number (or none) that downloads with the sheet.
// Loaded after lote-views.js; reads `lote`, showLayers, lotePopup, geomBounds.
//
// Clusters can number in the dozens, so identity is the number on the map and
// in the table, not a colour per cluster: one amber for "in a cluster", grey
// for rows left alone.

const db = { mode: "visual", radius: 500, min: 5, result: null, rows: [], pos: new Map(), timer: null };
const DB_IN = "#F2A65A", DB_NOISE = "#4b5b6b";

function dbSource() {
  if (map.getSource("lote-db")) return map.getSource("lote-db");
  map.addSource("lote-db", { type: "geojson", data: EMPTY_FC });
  map.addSource("lote-db-pts", { type: "geojson", data: EMPTY_FC });
  map.addLayer({ id: "lote-db-hull", type: "fill", source: "lote-db",
                 paint: { "fill-color": DB_IN, "fill-opacity": 0.12 } });
  map.addLayer({ id: "lote-db-line", type: "line", source: "lote-db",
                 paint: { "line-color": DB_IN, "line-width": 1.2, "line-opacity": 0.8 } });
  map.addLayer({
    id: "lote-db-pts", type: "circle", source: "lote-db-pts",
    paint: {
      "circle-color": ["case", ["==", ["get", "c"], -1], DB_NOISE, DB_IN],
      "circle-opacity": ["case", ["==", ["get", "c"], -1], 0.55, 0.9],
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 6, 2.5, 12, 5, 16, 8],
      "circle-stroke-color": "#14212F", "circle-stroke-width": 0.8,
    },
  });
  map.addLayer({
    id: "lote-db-label", type: "symbol", source: "lote-db",
    layout: { "text-field": ["concat", "#", ["to-string", ["+", ["get", "id"], 1]], " · ", ["to-string", ["get", "n"]]],
              "text-font": ["Inter Medium"], "text-size": 11, "text-allow-overlap": false },
    paint: { "text-color": "#E8EDF2", "text-halo-color": "#14212F", "text-halo-width": 1.4 },
  });
  map.on("click", "lote-db-pts", (e) => lotePopup(e.features[0], e.lngLat));
  map.on("mouseenter", "lote-db-pts", () => { map.getCanvas().style.cursor = "pointer"; });
  map.on("mouseleave", "lote-db-pts", () => { map.getCanvas().style.cursor = "crosshair"; });
  LOTE_LAYERS.push("lote-db-hull", "lote-db-line", "lote-db-pts", "lote-db-label");
  return map.getSource("lote-db");
}

function dbResetResult() {
  db.result = null;
  el("db-table").innerHTML = "";
  el("db-sum").textContent = "";
}

async function dbRun() {
  db.rows = []; db.pos = new Map();
  const points = [];
  lote.out.forEach((o, i) => {
    if (o && o.hit) { db.pos.set(i, db.rows.length); db.rows.push(i); points.push({ lat: o.hit.coordinates.lat, lon: o.hit.coordinates.lon }); }
  });
  if (!points.length) return;
  el("db-sum").textContent = "Calculando…";
  try {
    const r = await fetch("/api/v1/cluster", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ points, radius_m: db.radius, min_points: db.min }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.status);
    db.result = data;
    renderDbscan();
  } catch (e) {
    el("db-sum").textContent = `No se pudo calcular: ${e.message}`;
  }
}

// Row index -> cluster id (0-based) or null; undefined when never computed.
function clusterOf(rowIndex) {
  if (!db.result) return undefined;
  const k = db.pos.get(rowIndex);
  if (k === undefined) return null;
  const c = db.result.labels[k];
  return c === -1 ? null : c;
}

function mainMunicipio(clusterId) {
  const n = new Map();
  db.result.labels.forEach((c, k) => {
    if (c !== clusterId) return;
    const m = lote.out[db.rows[k]].hit.municipio || "";
    n.set(m, (n.get(m) || 0) + 1);
  });
  return [...n.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] || "";
}

function renderDbscan() {
  const res = db.result;
  if (!res) return;
  dbSource().setData({ type: "FeatureCollection", features: res.clusters.map((c) => ({
    type: "Feature", geometry: c.hull, properties: { id: c.id, n: c.n } })) });
  map.getSource("lote-db-pts").setData({ type: "FeatureCollection", features: db.rows.map((row, k) => {
    const hit = lote.out[row].hit;
    return { type: "Feature", geometry: { type: "Point", coordinates: [hit.coordinates.lon, hit.coordinates.lat] },
             properties: { i: row, c: res.labels[k] } };
  }) });
  const inside = res.labels.filter((c) => c !== -1).length;
  el("db-sum").textContent = `${res.clusters.length} clusters · ${inside.toLocaleString("es")} filas agrupadas · ` +
    `${(res.labels.length - inside).toLocaleString("es")} sueltas`;
  const box = el("db-table");
  box.innerHTML = "";
  res.clusters.forEach((c) => {
    const li = document.createElement("li");
    li.className = "zt-go";
    li.innerHTML = `<span class="zt-name">#${c.id + 1} <small>${esc(mainMunicipio(c.id))}</small></span>` +
      `<span class="zt-n mono">${c.n.toLocaleString("es")}</span>` +
      `<span class="zt-p mono">${c.area_km2.toLocaleString("es")} km²</span><span></span>`;
    li.addEventListener("click", () => map.fitBounds(geomBounds(c.hull),
      { padding: { top: 60, bottom: 60, left: 480, right: 60 }, maxZoom: 16 }));
    box.appendChild(li);
  });
}

// Called by setLoteView for the Grupos view (on) and for every other view (off).
function setDbscanActive(on) {
  el("lote-db").hidden = !on;
  if (!on) return;
  if (db.mode === "analysis") {
    dbSource();
    showLayers(["lote-db-hull", "lote-db-line", "lote-db-pts", "lote-db-label"]);
    el("lote-legend").hidden = true;
    if (!db.result) dbRun(); else renderDbscan();
  }
}

function dbSchedule() {
  clearTimeout(db.timer);
  db.timer = setTimeout(() => { dbResetResult(); dbRun(); }, 350);
}

el("db-mode").addEventListener("click", (e) => {
  const b = e.target.closest(".seg__btn");
  if (!b) return;
  db.mode = b.dataset.mode;
  document.querySelectorAll("#db-mode .seg__btn").forEach((x) => x.classList.toggle("seg__btn--on", x === b));
  el("db-params").hidden = db.mode !== "analysis";
  setLoteView("clusters");
});
el("db-radius").addEventListener("input", (e) => {
  db.radius = +e.target.value;
  el("db-radius-v").textContent = db.radius >= 1000 ? `${(db.radius / 1000).toFixed(1)} km` : `${db.radius} m`;
  dbSchedule();
});
el("db-min").addEventListener("input", (e) => {
  db.min = +e.target.value;
  el("db-min-v").textContent = db.min;
  dbSchedule();
});
