"use strict";

// ---- Lote "Cobertura": click centres on the map, assign every row to the one
// it reaches fastest by car, and show who is left beyond a threshold.
// Loaded after lote-views.js; reads `lote`, `loteView`, showLayers, lotePopup.

// Categorical, eight slots, stepped for the dark surface and validated against
// it (dataviz validate_palette.js --mode dark --surface #14212F: all checks
// pass, worst adjacent CVD ΔE 8.4). On a map any two can touch, which only the
// first three survive for colour-blind readers, so the centre number on the
// pin and the per-centre filter in the table carry identity too.
const COV_COLORS = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"];
const COV_OUT = "#4b5b6b";               // beyond the threshold: recedes, not a ninth hue
const COV_MAX_CENTERS = 8;
const COV_FETCH_MIN = 120;               // ask once for everything within 2 h; thresholds re-slice it

const cov = { centers: [], markers: [], result: null, rows: [], pos: new Map(), threshold: 15, only: null };

function covSource() {
  if (map.getSource("lote-cov")) return map.getSource("lote-cov");
  map.addSource("lote-cov", { type: "geojson", data: EMPTY_FC });
  const color = ["match", ["get", "c"]];
  COV_COLORS.forEach((c, i) => color.push(i, c));
  color.push(COV_OUT);
  map.addLayer({
    id: "lote-cov", type: "circle", source: "lote-cov",
    paint: {
      "circle-color": color,
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 6, 2.5, 12, 5, 16, 8],
      "circle-opacity": ["case", ["==", ["get", "c"], -1], 0.55, 0.9],
      "circle-stroke-color": "#14212F", "circle-stroke-width": 0.8,
    },
  });
  map.on("click", "lote-cov", (e) => lotePopup(e.features[0], e.lngLat));
  map.on("mouseenter", "lote-cov", () => { map.getCanvas().style.cursor = "pointer"; });
  map.on("mouseleave", "lote-cov", () => { map.getCanvas().style.cursor = "crosshair"; });
  LOTE_LAYERS.push("lote-cov");
  return map.getSource("lote-cov");
}

// Called by app.js for a map click while in Lote mode.
function loteMapClick(lngLat) {
  if (loteView !== "coverage") return;
  if (cov.centers.length >= COV_MAX_CENTERS) {
    el("cov-status").textContent = `Máximo ${COV_MAX_CENTERS} centros.`;
    return;
  }
  cov.centers.push({ lat: lngLat.lat, lon: lngLat.lng });
  covResetResult();
  renderCenters();
}

function renderCenters() {
  cov.markers.forEach((m) => m.remove());
  cov.markers = cov.centers.map((c, i) => {
    const d = document.createElement("div");
    d.className = "cov-pin";
    d.style.background = COV_COLORS[i];
    d.textContent = i + 1;
    return new maplibregl.Marker({ element: d }).setLngLat([c.lon, c.lat]).addTo(map);
  });
  const box = el("cov-centers");
  box.innerHTML = "";
  cov.centers.forEach((c, i) => {
    const li = document.createElement("li");
    li.innerHTML = `<i style="background:${COV_COLORS[i]}">${i + 1}</i>` +
      `<span class="mono">${c.lat.toFixed(4)}, ${c.lon.toFixed(4)}</span>`;
    const del = document.createElement("button");
    del.type = "button"; del.className = "stop__del"; del.textContent = "×";
    del.addEventListener("click", () => { cov.centers.splice(i, 1); covResetResult(); renderCenters(); });
    li.appendChild(del);
    box.appendChild(li);
  });
  el("cov-go").disabled = !cov.centers.length;
}

function covResetResult() {
  cov.result = null; cov.only = null;
  el("cov-table").innerHTML = "";
  el("cov-sum").textContent = "";
  if (map.getSource("lote-cov")) map.getSource("lote-cov").setData(EMPTY_FC);
}

async function covRun() {
  cov.rows = [];
  cov.pos = new Map();                   // row index -> position in the request
  const points = [];
  lote.out.forEach((o, i) => {
    if (o && o.hit) { cov.pos.set(i, cov.rows.length); cov.rows.push(i); points.push({ lat: o.hit.coordinates.lat, lon: o.hit.coordinates.lon }); }
  });
  if (!points.length || !cov.centers.length) return;
  el("cov-go").disabled = true;
  el("cov-status").textContent = "Calculando tiempos por la red de calles…";
  const t0 = performance.now();
  try {
    const r = await fetch("/api/v1/coverage", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ centers: cov.centers, points, max_minutes: COV_FETCH_MIN }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.status);
    cov.result = data;
    const off = data.centers.map((c, i) => (c.ok ? null : i + 1)).filter(Boolean);
    el("cov-status").textContent = `Listo en ${((performance.now() - t0) / 1000).toFixed(1)} s.` +
      (off.length ? ` Centro ${off.join(", ")} fuera de la red de calles.` : "");
    renderCoverage();
  } catch (e) {
    el("cov-status").textContent = `No se pudo calcular: ${e.message}`;
  } finally {
    el("cov-go").disabled = !cov.centers.length;
  }
}

// Row index -> {center, minutes} for the download; null when never computed.
function coverageOf(rowIndex) {
  if (!cov.result) return null;
  const k = cov.pos.get(rowIndex);
  return k === undefined ? { center: null, minutes: null }
    : { center: cov.result.center[k], minutes: cov.result.minutes[k] };
}

function renderCoverage() {
  const res = cov.result;
  if (!res) return;
  const T = cov.threshold;
  const per = cov.centers.map(() => ({ n: 0, sum: 0, max: 0 }));
  let beyond = 0, unreachable = 0;
  const feats = [];
  cov.rows.forEach((row, k) => {
    const c = res.center[k], m = res.minutes[k];
    const inside = c !== null && m <= T;
    if (inside) { per[c].n++; per[c].sum += m; per[c].max = Math.max(per[c].max, m); }
    else if (c === null) unreachable++; else beyond++;
    if (cov.only !== null && c !== cov.only) return;
    const hit = lote.out[row].hit;
    feats.push({ type: "Feature",
      geometry: { type: "Point", coordinates: [hit.coordinates.lon, hit.coordinates.lat] },
      properties: { i: row, c: inside ? c : -1 } });
  });
  covSource().setData({ type: "FeatureCollection", features: feats });

  const total = cov.rows.length;
  const pct = (n) => `${((100 * n) / total).toFixed(1)}%`;
  const covered = total - beyond - unreachable;
  el("cov-sum").textContent = `${covered.toLocaleString("es")} de ${total.toLocaleString("es")} filas ` +
    `(${pct(covered)}) a ${T} min o menos de algún centro.`;
  const box = el("cov-table");
  box.innerHTML = "";
  per.forEach((p, i) => {
    const li = document.createElement("li");
    li.className = "zt-go" + (cov.only === i ? " cov-on" : "");
    li.innerHTML = `<span class="zt-name"><i class="cov-dot" style="background:${COV_COLORS[i]}">${i + 1}</i> Centro ${i + 1}</span>` +
      `<span class="zt-n mono">${p.n.toLocaleString("es")}</span>` +
      `<span class="zt-p mono">${p.n ? `${(p.sum / p.n).toFixed(1)} min` : "—"}</span>` +
      `<span class="zt-p mono">${p.n ? `máx ${p.max.toFixed(0)}` : ""}</span>`;
    li.title = "Mostrar solo las filas de este centro";
    li.addEventListener("click", () => { cov.only = cov.only === i ? null : i; renderCoverage(); });
    box.appendChild(li);
  });
  const out = document.createElement("li");
  out.innerHTML = `<span class="zt-name"><i class="cov-dot" style="background:${COV_OUT}"></i> A más de ${T} min</span>` +
    `<span class="zt-n mono">${beyond.toLocaleString("es")}</span><span class="zt-p mono">${pct(beyond)}</span><span></span>`;
  box.appendChild(out);
  if (unreachable) {
    const un = document.createElement("li");
    un.innerHTML = `<span class="zt-name">Sin ruta en 2 h o fuera de la red</span>` +
      `<span class="zt-n mono">${unreachable.toLocaleString("es")}</span><span class="zt-p mono">${pct(unreachable)}</span><span></span>`;
    box.appendChild(un);
  }
}

function setCoverageActive(on) {
  el("lote-cov").hidden = !on;
  cov.markers.forEach((m) => { m.getElement().style.display = on ? "" : "none"; });
  if (on) {
    covSource();
    showLayers(["lote-cov"]);
    renderCoverage();
  }
}

el("cov-go").addEventListener("click", covRun);
el("cov-clear").addEventListener("click", () => { cov.centers = []; covResetResult(); renderCenters(); });
el("cov-threshold").addEventListener("click", (e) => {
  const b = e.target.closest(".seg__btn");
  if (!b) return;
  cov.threshold = +b.dataset.min;
  document.querySelectorAll("#cov-threshold .seg__btn").forEach((x) => x.classList.toggle("seg__btn--on", x === b));
  renderCoverage();
});
