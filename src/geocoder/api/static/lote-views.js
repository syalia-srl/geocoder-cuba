"use strict";

// ---- Lote views: the same geocoded rows drawn as points, clusters, a heatmap,
// or counts per provincia / municipio / barrio. Loaded after lote.js; reads
// `lote`, loteFeatures(), loteSource() and writeXlsx() from it.
//
// Counting needs no server work: every result already carries its provincia,
// municipio and barrio. The server only ships the shapes, once per level.

// Sequential ramp, one hue (the brand amber), dark -> light because the map is
// dark: low counts recede toward the surface, high counts stand out. OKLab L
// .46 -> .87 in even steps; the lowest step clears 2:1 against the surface.
const ZONE_RAMP = ["#74502D", "#9A6533", "#C0803F", "#E6A052", "#FFCB8C"];
const SURFACE = "#14212F";
const zoneGeo = {};                      // level -> FeatureCollection (fetched once)
let loteView = "points";
let zoneLevel = "municipio";
let zoneRows = [];                       // current table: [{key, name, municipio, provincia, count, feature}]
let zonePopup = null;

const LOTE_LAYERS = ["lote-pts", "lote-heat", "lote-cl", "lote-cl-count", "lote-cl-pt",
                     "lote-zone-fill", "lote-zone-line", "lote-zone-dot"];

function showLayers(ids) {
  for (const id of LOTE_LAYERS) {
    if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", ids.includes(id) ? "visible" : "none");
  }
}

// ---------- clusters ----------
function clusterSource() {
  if (map.getSource("lote-cl")) return map.getSource("lote-cl");
  map.addSource("lote-cl", { type: "geojson", data: EMPTY_FC, cluster: true, clusterRadius: 50, clusterMaxZoom: 14 });
  map.addLayer({
    id: "lote-cl", type: "circle", source: "lote-cl", filter: ["has", "point_count"],
    paint: {
      "circle-color": "#F2A65A", "circle-opacity": 0.85,
      "circle-radius": ["step", ["get", "point_count"], 12, 20, 16, 100, 22, 500, 30],
      "circle-stroke-color": SURFACE, "circle-stroke-width": 2,
    },
  });
  map.addLayer({
    id: "lote-cl-count", type: "symbol", source: "lote-cl", filter: ["has", "point_count"],
    layout: { "text-field": ["get", "point_count_abbreviated"], "text-font": ["Inter Medium"], "text-size": 12 },
    paint: { "text-color": "#0B1420" },
  });
  map.addLayer({
    id: "lote-cl-pt", type: "circle", source: "lote-cl", filter: ["!", ["has", "point_count"]],
    paint: map.getLayer("lote-pts") ? {
      "circle-color": map.getPaintProperty("lote-pts", "circle-color"),
      "circle-radius": 5, "circle-stroke-color": SURFACE, "circle-stroke-width": 0.8,
    } : { "circle-radius": 5 },
  });
  map.on("click", "lote-cl", async (e) => {
    const f = e.features[0];
    const zoom = await map.getSource("lote-cl").getClusterExpansionZoom(f.properties.cluster_id);
    map.easeTo({ center: f.geometry.coordinates, zoom, duration: reduceMotion ? 0 : 500 });
  });
  map.on("click", "lote-cl-pt", (e) => lotePopup(e.features[0], e.lngLat));
  for (const id of ["lote-cl", "lote-cl-pt"]) {
    map.on("mouseenter", id, () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", id, () => { map.getCanvas().style.cursor = "crosshair"; });
  }
  return map.getSource("lote-cl");
}

// ---------- heatmap ----------
function heatLayer() {
  if (map.getLayer("lote-heat")) return;
  loteSource();
  map.addLayer({
    id: "lote-heat", type: "heatmap", source: "lote", maxzoom: 15,
    paint: {
      "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 6, 0.6, 14, 2],
      "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 6, 6, 10, 14, 14, 26],
      "heatmap-color": ["interpolate", ["linear"], ["heatmap-density"],
        0, "rgba(116,80,45,0)", 0.15, ZONE_RAMP[0], 0.35, ZONE_RAMP[1],
        0.55, ZONE_RAMP[2], 0.8, ZONE_RAMP[3], 1, ZONE_RAMP[4]],
      // hand over to the individual dots when zoomed right in
      "heatmap-opacity": ["interpolate", ["linear"], ["zoom"], 12, 0.9, 15, 0],
    },
  }, "lote-pts");
}

// ---------- counts per zone ----------
async function zoneGeometries(level) {
  if (!zoneGeo[level]) {
    const r = await fetch(`/api/v1/admin/geometries?level=${level}`);
    zoneGeo[level] = await r.json();
  }
  return zoneGeo[level];
}

function zoneKeyOf(hit, level) {
  if (level === "provincia") return hit.provincia || "";
  if (level === "municipio") return hit.municipio || "";
  return hit.barrio ? `${hit.barrio}|${hit.municipio || ""}` : "";
}

// Quantile breaks over the non-zero counts -> class 0..4 (5 classes).
function breaksOf(counts) {
  const v = counts.filter((c) => c > 0).sort((a, b) => a - b);
  if (!v.length) return [];
  const b = [1, 2, 3, 4].map((k) => v[Math.min(v.length - 1, Math.floor((k * v.length) / 5))]);
  return [...new Set(b)];
}
const classOf = (c, br) => br.filter((x) => c > x).length;

async function buildZones() {
  const level = zoneLevel;
  const fc = await zoneGeometries(level);
  if (level !== zoneLevel) return;          // the user switched level mid-fetch
  const counts = new Map();             // key -> {count, hit: the first row's result}
  let placed = 0;
  lote.out.forEach((o) => {
    if (!o || !o.hit) return;
    placed++;
    const k = zoneKeyOf(o.hit, level);
    const c = counts.get(k);
    if (c) c.count++; else counts.set(k, { count: 1, hit: o.hit });
  });
  // Join counts to shapes. A barrio keys on (name, municipio) because names
  // repeat across Cuba; when the shape's municipio is unknown or differs, fall
  // back to the name alone if only one shape carries it.
  const byKey = new Map(), byName = new Map();
  for (const f of fc.features) {
    const p = f.properties;
    const key = level === "barrio" ? `${p.name}|${p.municipio || ""}` : p.name;
    byKey.set(key, f);
    byName.set(p.name, byName.has(p.name) ? null : f);
  }
  zoneRows = [...counts.entries()].map(([key, { count, hit }]) => {
    const name = level === "barrio" ? key.split("|")[0] : key;
    const feature = byKey.get(key) || (level === "barrio" && byName.get(name)) || null;
    return { key, name: name || `(sin ${level})`, count, feature, blank: !name,
             municipio: hit.municipio || "", provincia: hit.provincia || "" };
  // Rows with no zone at this level (most results outside a city have no
  // barrio) are counted but listed last, not ranked above every real zone.
  }).sort((a, b) => a.blank - b.blank || b.count - a.count);

  const br = breaksOf(zoneRows.filter((r) => !r.blank).map((r) => r.count));
  const counted = new Map(zoneRows.filter((r) => r.feature).map((r) => [r.feature, r]));
  const feats = fc.features.map((f) => {
    const r = counted.get(f);
    return { type: "Feature", geometry: f.geometry,
             properties: { name: f.properties.name, count: r ? r.count : 0,
                           cls: r ? classOf(r.count, br) : -1,
                           pct: r ? (100 * r.count) / placed : 0 } };
  });
  zoneSource().setData({ type: "FeatureCollection", features: feats });
  renderZoneTable(placed, br);
}

function zoneSource() {
  if (map.getSource("lote-zones")) return map.getSource("lote-zones");
  map.addSource("lote-zones", { type: "geojson", data: EMPTY_FC });
  const ramp = ["match", ["get", "cls"]];
  ZONE_RAMP.forEach((c, i) => ramp.push(i, c));
  ramp.push("rgba(0,0,0,0)");
  map.addLayer({
    id: "lote-zone-fill", type: "fill", source: "lote-zones",
    filter: ["==", ["geometry-type"], "Polygon"],
    // thins out when zoomed in, so the streets under a zone stay readable
    paint: { "fill-color": ramp, "fill-opacity": ["interpolate", ["linear"], ["zoom"], 8, 0.8, 13, 0.4] },
  }, "lote-pts");
  map.addLayer({
    id: "lote-zone-line", type: "line", source: "lote-zones",
    filter: ["==", ["geometry-type"], "Polygon"],
    // a surface-coloured gap between neighbouring fills; zero-count zones keep
    // only this outline, so "none here" reads apart from "few here"
    paint: { "line-color": ["case", [">", ["get", "count"], 0], SURFACE, "#2a3a4c"], "line-width": 1.2 },
  }, "lote-pts");
  map.addLayer({
    id: "lote-zone-dot", type: "circle", source: "lote-zones",
    filter: ["all", ["==", ["geometry-type"], "Point"], [">", ["get", "count"], 0]],
    paint: {
      "circle-color": ramp,
      "circle-radius": ["interpolate", ["linear"], ["sqrt", ["get", "count"]], 1, 4, 30, 26],
      "circle-stroke-color": SURFACE, "circle-stroke-width": 2, "circle-opacity": 0.9,
    },
  });
  for (const id of ["lote-zone-fill", "lote-zone-dot"]) {
    map.on("mousemove", id, (e) => {
      const p = e.features[0].properties;
      if (!zonePopup) zonePopup = new maplibregl.Popup({ className: "lote-pop", closeButton: false, closeOnClick: false });
      zonePopup.setLngLat(e.lngLat)
        .setHTML(`<strong>${esc(p.name)}</strong><br><span class="mono">${(+p.count).toLocaleString("es")} filas · ${(+p.pct).toFixed(1)} %</span>`)
        .addTo(map);
    });
    map.on("mouseleave", id, () => { if (zonePopup) zonePopup.remove(); });
  }
  return map.getSource("lote-zones");
}

function geomBounds(g) {
  const b = new maplibregl.LngLatBounds();
  const walk = (c) => (typeof c[0] === "number" ? b.extend(c) : c.forEach(walk));
  walk(g.coordinates);
  return b;
}

function renderZoneTable(placed, br) {
  const lo = [1, ...br.map((x) => x + 1)], hi = [...br, Infinity];
  el("zone-legend").innerHTML = ZONE_RAMP.slice(0, lo.length).map((c, i) =>
    `<li><i style="background:${c}"></i>${hi[i] === Infinity || hi[i] === lo[i] ? (hi[i] === Infinity ? `${lo[i]}+` : lo[i]) : `${lo[i]}–${hi[i]}`}</li>`
  ).join("") + `<li><i class="zone-none"></i>0</li>`;
  const missing = lote.out.filter((o) => o && !o.hit).length;
  el("zone-sum").textContent = `${zoneRows.length} zonas · ${placed.toLocaleString("es")} filas` +
    (missing ? ` · ${missing} sin resultado, fuera del conteo` : "");
  const max = Math.max(1, ...zoneRows.filter((r) => !r.blank).map((r) => r.count));
  const box = el("zone-table");
  box.innerHTML = "";
  zoneRows.forEach((r) => {
    const li = document.createElement("li");
    li.innerHTML = `<span class="zt-name">${esc(r.name)}${zoneLevel === "barrio" && r.municipio ? ` <small>${esc(r.municipio)}</small>` : ""}</span>` +
      `<span class="zt-bar"><i style="width:${(100 * r.count) / max}%"></i></span>` +
      `<span class="zt-n mono">${r.count.toLocaleString("es")}</span>` +
      `<span class="zt-p mono">${((100 * r.count) / placed).toFixed(1)}%</span>`;
    if (r.feature) {
      li.classList.add("zt-go");
      li.addEventListener("click", () => {
        const g = r.feature.geometry;
        if (g.type === "Point") map.easeTo({ center: g.coordinates, zoom: 14 });
        else map.fitBounds(geomBounds(g), { padding: { top: 60, bottom: 60, left: 480, right: 60 }, maxZoom: 15 });
      });
    }
    box.appendChild(li);
  });
}

function zoneDownload() {
  const placed = zoneRows.reduce((s, r) => s + r.count, 0);
  const rows = [["nivel", "zona", "municipio", "provincia", "filas", "porcentaje"]];
  zoneRows.forEach((r) => rows.push([zoneLevel, r.name,
    zoneLevel === "provincia" ? "" : r.municipio, r.provincia, r.count,
    Math.round((10000 * r.count) / placed) / 100]));
  const name = (el("lote-file").files[0]?.name || "lote").replace(/\.(xlsx|csv)$/i, "");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(writeXlsx(rows));
  a.download = `${name}-por-${zoneLevel}.xlsx`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ---------- switching ----------
async function setLoteView(view) {
  loteView = view;
  document.querySelectorAll("#lote-views .seg__btn").forEach((b) =>
    b.classList.toggle("seg__btn--on", b.dataset.view === view));
  el("lote-zone").hidden = view !== "zone";
  setCoverageActive(view === "coverage");
  setDbscanActive(view === "clusters");     // Grupos: Visual or Análisis (lote-dbscan.js)
  // precision colours only where dots wear them (not in Grupos → Análisis)
  el("lote-legend").hidden = !(view === "points" || (view === "clusters" && db.mode !== "analysis"));
  if (zonePopup) zonePopup.remove();
  if (view === "points") showLayers(["lote-pts"]);
  else if (view === "clusters") {
    if (db.mode !== "analysis") {            // Análisis is drawn by setDbscanActive
      clusterSource().setData({ type: "FeatureCollection", features: loteFeatures() });
      showLayers(["lote-cl", "lote-cl-count", "lote-cl-pt"]);
    }
  } else if (view === "heat") {
    heatLayer();
    showLayers(["lote-heat", "lote-pts"]);
    const fadeIn = ["interpolate", ["linear"], ["zoom"], 12, 0, 14, 1];
    map.setPaintProperty("lote-pts", "circle-opacity", fadeIn);
    map.setPaintProperty("lote-pts", "circle-stroke-opacity", fadeIn);   // else dark rings remain
  } else if (view === "coverage") {
    // setCoverageActive above already drew it
  } else if (view === "zone") {
    zoneSource();
    showLayers(["lote-zone-fill", "lote-zone-line", "lote-zone-dot"]);
    await buildZones();
  }
  if (view !== "heat" && map.getLayer("lote-pts")) {
    map.setPaintProperty("lote-pts", "circle-opacity", 0.9);
    map.setPaintProperty("lote-pts", "circle-stroke-opacity", 1);
  }
}

// Called by lote.js when a run finishes: the views work on the finished set.
function loteViewsReady() {
  el("lote-views").hidden = false;
  setLoteView(loteView);
}

el("lote-views").addEventListener("click", (e) => {
  const b = e.target.closest(".seg__btn");
  if (b) setLoteView(b.dataset.view);
});
el("zone-level").addEventListener("click", (e) => {
  const b = e.target.closest(".seg__btn");
  if (!b) return;
  zoneLevel = b.dataset.level;
  document.querySelectorAll("#zone-level .seg__btn").forEach((x) =>
    x.classList.toggle("seg__btn--on", x === b));
  buildZones();
});
el("zone-dl").addEventListener("click", zoneDownload);
