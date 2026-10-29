"use strict";

const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// Offline vector basemap: local Cuba PMTiles rendered by MapLibre GL (WebGL).
const DARK = {
  bg: "#14212F", water: "#0e1a26", land: "#182634",
  road: "#26384a", roadMajor: "#33485d", boundary: "#2a3a4c", label: "#8aa0b6",
};
const HAVANA_LL = [-82.366, 23.113];   // MapLibre is [lng, lat]

maplibregl.addProtocol("pmtiles", new pmtiles.Protocol().tile);

const map = new maplibregl.Map({
  container: "map",
  center: HAVANA_LL,
  zoom: 11,
  dragRotate: false,
  touchZoomRotate: false,
  attributionControl: { compact: false },
  style: {
    version: 8,
    // Vendored SDF glyph ranges — labels render offline, like everything else.
    glyphs: "/static/vendor/glyphs/{fontstack}/{range}.pbf",
    sources: {
      basemap: {
        type: "vector", url: "pmtiles://" + location.origin + "/tiles/cuba.pmtiles",
        attribution: '&copy; <a href="https://openstreetmap.org/copyright">OpenStreetMap</a> · datos OSM',
      },
    },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": DARK.bg } },
      { id: "water", type: "fill", source: "basemap", "source-layer": "water",
        paint: { "fill-color": DARK.water } },
      { id: "landuse", type: "fill", source: "basemap", "source-layer": "landuse",
        minzoom: 8, paint: { "fill-color": DARK.land, "fill-opacity": 0.6 } },
      { id: "boundaries", type: "line", source: "basemap", "source-layer": "boundaries",
        paint: { "line-color": DARK.boundary, "line-width": 0.8 } },
      { id: "roads", type: "line", source: "basemap", "source-layer": "roads",
        paint: {
          "line-color": DARK.road,
          "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.4, 14, 1.4],
        } },
      { id: "roads-major", type: "line", source: "basemap", "source-layer": "roads",
        filter: ["match", ["get", "kind"], ["motorway", "trunk", "primary", "secondary"], true, false],
        paint: {
          "line-color": DARK.roadMajor,
          "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.8, 14, 2.4],
        } },
      { id: "places", type: "symbol", source: "basemap", "source-layer": "places",
        layout: {
          "text-field": ["get", "name"],
          "text-font": ["Inter Medium"],
          "text-size": ["match", ["get", "kind"], "city", 15, "town", 13, 11],
          "text-max-width": 7,
          "text-padding": 4,
        },
        paint: {
          "text-color": DARK.label,
          "text-halo-color": DARK.bg,
          "text-halo-width": 1.4,
        } },
    ],
  },
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
document.getElementById("map").style.background = DARK.bg;

// ---- overlay for the current result (one GeoJSON source + styled layers + markers) ----
const EMPTY_FC = { type: "FeatureCollection", features: [] };
const markers = [];          // maplibregl.Marker instances for the current result

// Lazily create the overlay source + layers the first time they're needed (and the
// style is ready). Robust to the exact timing of the map 'load' event.
function overlaySource() {
  if (map.getSource("overlay")) return map.getSource("overlay");
  if (!map.isStyleLoaded()) return null;
  map.addSource("overlay", { type: "geojson", data: EMPTY_FC });
  map.addLayer({
    id: "overlay-fill", type: "fill", source: "overlay",
    filter: ["==", ["geometry-type"], "Polygon"],
    paint: { "fill-color": "#F2A65A", "fill-opacity": 0.12 },
  });
  map.addLayer({
    id: "overlay-line", type: "line", source: "overlay",
    paint: {
      "line-color": ["case", ["==", ["get", "role"], "route"], "#5AA9F2", "#F2A65A"],
      "line-width": ["case", ["==", ["get", "role"], "route"], 4, 1.6],
      "line-opacity": 0.95,
    },
  });
  return map.getSource("overlay");
}

function setOverlay(features) {
  const s = overlaySource();
  if (s) s.setData({ type: "FeatureCollection", features });
}
function clearOverlay() {
  const s = overlaySource();
  if (s) s.setData(EMPTY_FC);
  if (typeof clearIso === "function") clearIso();   // iso.js: bands belong to the old result
  markers.forEach((m) => m.remove());
  markers.length = 0;
}

// gentle intro seed once the map is interactive
map.on("load", () => {
  // A permalink wins over the demo seed: ?q=<query> or ?at=<lat>,<lon>.
  const params = new URLSearchParams(location.search);
  const q = params.get("q");
  const at = params.get("at");
  if (q) {
    input.value = q;
    resolve(q);
  } else if (at) {
    const [lat, lon] = at.split(",").map(Number);
    if (Number.isFinite(lat) && Number.isFinite(lon)) reverseAt(lat, lon);
  } else if (!reduceMotion) {
    setTimeout(() => {
      if (mode !== "point") return;   // the user already switched mode
      input.value = "Calle L e/ 23 y 25, Vedado"; resolve(input.value);
    }, 500);
  }
});

map.on("click", (e) => {
  // In Viaje mode a click adds a stop; Lote clicks open the row popup (lote.js);
  // everywhere else it describes the point.
  if (mode === "lote") {                      // Cobertura places centres (lote-coverage.js)
    if (typeof loteMapClick === "function") loteMapClick(e.lngLat);
    return;
  }
  if (isViaje()) addStop(e.lngLat.lat, e.lngLat.lng);
  else reverseAt(e.lngLat.lat, e.lngLat.lng);
});
map.getCanvas().style.cursor = "crosshair";
function addPin(lat, lon) {
  const elm = document.createElement("div");
  elm.className = "pin";
  markers.push(new maplibregl.Marker({ element: elm }).setLngLat([lon, lat]).addTo(map));
}
function addRingLabel(lat, lon, radius) {
  const elm = document.createElement("div");
  elm.className = "ring-label";
  elm.textContent = `± ${Math.round(radius)} m`;
  markers.push(new maplibregl.Marker({ element: elm, anchor: "left", offset: [10, 0] })
    .setLngLat([lon, lat]).addTo(map));
}

// A circle of radius_m around (lat,lon) as a GeoJSON Polygon — MapLibre
// circle-radius is in pixels, so we build a real metric circle for the "± N m" ring.
function circlePolygon(lat, lon, radius_m, n = 64) {
  const ring = [];
  const dLat = radius_m / 111320;
  const dLon = radius_m / (111320 * Math.cos((lat * Math.PI) / 180));
  for (let i = 0; i <= n; i++) {
    const a = (i / n) * 2 * Math.PI;
    ring.push([lon + dLon * Math.cos(a), lat + dLat * Math.sin(a)]);
  }
  return { type: "Polygon", coordinates: [ring] };
}
function boundsOf(geometry) {
  let w = 180, s = 90, e = -180, nn = -90;
  const walk = (c) => {
    if (typeof c[0] === "number") {
      w = Math.min(w, c[0]); e = Math.max(e, c[0]);
      s = Math.min(s, c[1]); nn = Math.max(nn, c[1]);
    } else c.forEach(walk);
  };
  walk(geometry.coordinates);
  return [[w, s], [e, nn]];
}
function boundsAround(lat, lon, span_m) {
  const dLat = span_m / 2 / 111320;
  const dLon = span_m / 2 / (111320 * Math.cos((lat * Math.PI) / 180));
  return [[lon - dLon, lat - dLat], [lon + dLon, lat + dLat]];
}
function frameBounds(bounds, opts = {}) {
  map.fitBounds(bounds, {
    padding: opts.padding ?? 40, maxZoom: opts.maxZoom ?? 17,
    animate: !reduceMotion, duration: 1100,
  });
}

// ---- elements ----
const form = document.getElementById("search");
const input = document.getElementById("q");
const result = document.getElementById("result");
const empty = document.getElementById("empty");
const el = (id) => document.getElementById(id);

const METHOD_ES = {
  exact_portal: "portal exacto",
  interpolated_entre: "interpolado entre calles",
  segment_midpoint: "punto medio del tramo",
  esquina: "esquina",
  poi: "lugar de interés",
  street: "calle",
  barrio: "barrio",
  municipio: "municipio",
  describe_segment: "entre esquinas",
  describe_street: "sobre la calle",
  describe_place: "cerca de un lugar",
};

const TOKENS = [
  ["calle", "street"],
  ["número", "number"],
  ["e/", "entre"],
  ["esquina", "esquina"],
  ["barrio", "barrio"],
  ["municipio", "municipio"],
];

function fmt(v) {
  return Array.isArray(v) ? v.join(" y ") : v;
}

function renderParse(resolved) {
  const box = el("r-parse");
  box.innerHTML = "";
  if (!resolved) return;
  for (const [label, key] of TOKENS) {
    const val = resolved[key];
    if (val === null || val === undefined || val === "" ) continue;
    const tok = document.createElement("span");
    tok.className = "tok";
    tok.innerHTML = `<b>${label}</b>${fmt(val)}`;
    box.appendChild(tok);
  }
}

function draw(r) {
  clearOverlay();
  if (r.geometry) {
    setOverlay([{ type: "Feature", properties: { role: "area" }, geometry: r.geometry }]);
    frameBounds(boundsOf(r.geometry), { maxZoom: 16 });
    return;
  }
  const { lat, lon } = r.coordinates;
  const radius = Math.max(r.radius_m || 15, 8);
  setOverlay([{ type: "Feature", properties: { role: "area" }, geometry: circlePolygon(lat, lon, radius) }]);
  addPin(lat, lon);
  addRingLabel(lat, lon, radius);
  frameBounds(boundsAround(lat, lon, radius * 4), { maxZoom: 18 });
}

function drawRegion(reg) {
  clearOverlay();
  // reg.vertices are [lat, lon]; GeoJSON wants [lon, lat] rings, closed.
  const ring = reg.vertices.map(([la, lo]) => [lo, la]);
  ring.push(ring[0]);
  const geom = { type: "Polygon", coordinates: [ring] };
  setOverlay([{ type: "Feature", properties: { role: "area" }, geometry: geom }]);
  frameBounds(boundsOf(geom), { maxZoom: 17 });
}

async function resolveRegion(streetsRaw) {
  if (busy) return;
  const streets = streetsRaw.split(",").map((s) => s.trim()).filter(Boolean);
  if (streets.length < 3) {
    showEmpty();
    empty.textContent = "Escribe al menos 3 calles separadas por comas.";
    return;
  }
  setBusy(true);
  try {
    const res = await fetch("/api/v1/region", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ streets }),
    });
    if (!res.ok) {
      showEmpty();
      empty.textContent = "Esas calles no encierran una región.";
      return;
    }
    const reg = await res.json();
    empty.hidden = true;
    el("candidates").hidden = true;
    el("r-name").textContent = `Región de ${reg.vertices.length} esquinas`;
    el("r-type").textContent = reg.method;
    el("r-place").textContent = [reg.municipio, reg.provincia].filter(Boolean).join(" · ");
    el("r-coords").textContent =
      `${reg.centroid.lat.toFixed(5)}, ${reg.centroid.lon.toFixed(5)}`;
    el("r-radius").textContent = `${reg.area_ha.toFixed(1)} ha`;
    el("r-method").textContent = "polígono";
    result.hidden = false;
    drawRegion(reg);
  } catch (e) {
    showEmpty();
    empty.textContent = "No pudimos consultar el servicio. Intenta de nuevo.";
  } finally {
    setBusy(false);
  }
}

function drawRoute(rt) {
  clearOverlay();
  setOverlay([{ type: "Feature", properties: { role: "route" }, geometry: rt.geometry }]);
  addPin(rt.from.lat, rt.from.lon);
  addPin(rt.to.lat, rt.to.lon);
  frameBounds(boundsOf(rt.geometry), { maxZoom: 16, padding: 60 });
}

async function resolveRoute(fromRaw, toRaw) {
  if (busy) return;
  if (!fromRaw.trim() || !toRaw.trim()) {
    showEmpty();
    empty.textContent = "Escribe un origen y un destino.";
    return;
  }
  setBusy(true);
  try {
    const res = await fetch("/api/v1/route", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ from: fromRaw, to: toRaw }),
    });
    if (!res.ok) {
      showEmpty();
      empty.textContent = "No encontramos una ruta entre esos puntos.";
      return;
    }
    const rt = await res.json();
    empty.hidden = true;
    el("candidates").hidden = true;
    const km = (rt.distance_m / 1000).toFixed(2);
    el("r-name").textContent = `${km} km · ~${Math.round(rt.duration_min)} min`;
    el("r-type").textContent = "ruta";
    el("r-place").textContent = `${rt.from.nombre} → ${rt.to.nombre}`;
    renderParse(null);
    el("r-coords").textContent =
      `${rt.from.lat.toFixed(5)}, ${rt.from.lon.toFixed(5)}`;
    el("r-radius").textContent = `${km} km`;
    el("r-method").textContent = "camino más rápido";
    result.hidden = false;
    drawRoute(rt);
  } catch (e) {
    showEmpty();
    empty.textContent = "No pudimos consultar el servicio. Intenta de nuevo.";
  } finally {
    setBusy(false);
  }
}

// The single thing currently on screen. Every point path — typed query, map
// click, nearby-place click — produces one of these, and renderSelection is the
// only writer of the result panel. Multi-selection later becomes an array of
// these rather than a rewrite of how results reach the screen. (Region and
// route draw their own panels; they are not point selections.)
let selection = null;

function setSelection(sel) {
  selection = sel;
  renderSelection();
  syncUrl(sel);
}

// Reflect the current selection in the URL so it can be copied and shared.
// replaceState, not pushState: a map click is not a navigation, and filling the
// back stack with every click would make the browser back button useless.
function syncUrl(sel) {
  const u = new URL(location.href);
  u.searchParams.delete("q");
  u.searchParams.delete("at");
  if (sel.source === "query" && sel.query) {
    u.searchParams.set("q", sel.query);
  } else if (sel.point) {
    u.searchParams.set("at", `${sel.point.lat.toFixed(6)},${sel.point.lon.toFixed(6)}`);
  }
  history.replaceState(null, "", u);
}

function renderSelection() {
  const s = selection;
  if (!s) return;
  empty.hidden = true;
  el("r-name").textContent = s.nombre || "—";
  el("r-type").textContent = s.tipo || "";
  el("r-place").textContent = s.place || "";
  renderParse(s.parse);
  el("r-coords").textContent =
    `${s.point.lat.toFixed(5)}, ${s.point.lon.toFixed(5)}`;
  el("r-radius").textContent = `± ${Math.round(s.radius_m)} m`;
  el("r-method").textContent = s.methodLabel || "";
  result.hidden = false;
  renderNearby();
}

// A forward resolve result -> a query selection, then render + draw.
function showResult(r) {
  setSelection({
    source: "query",
    query: input.value,
    nombre: r.nombre || "—",
    tipo: r.tipo || "",
    place: [r.barrio, r.municipio, r.provincia].filter(Boolean).join(" · "),
    parse: r.resolved,
    point: { lat: r.coordinates.lat, lon: r.coordinates.lon },
    radius_m: r.radius_m,
    methodLabel: METHOD_ES[r.precision] || r.precision,
    nearby: [],
  });
  draw(r);
}

// Nearby places under the result (reverse/click path). Each is clickable and
// becomes the selection, so the user can step between neighbours without
// re-querying. Empty on the forward path.
function renderNearby() {
  const box = el("nearby");
  if (!box) return;
  const items = (selection && selection.nearby) || [];
  box.innerHTML = "";
  box.hidden = items.length === 0;
  items.forEach((p) => {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    const name = document.createElement("span");
    name.className = "nb-name";
    name.textContent = p.nombre;
    const dist = document.createElement("span");
    dist.className = "dist";
    dist.textContent = `${Math.round(p.distance_meters)} m`;
    btn.append(name, dist);
    btn.addEventListener("click", () => selectNearby(p, items));
    li.appendChild(btn);
    box.appendChild(li);
  });
}

// ---- Viaje mode: collect stops, then optimise the visiting order ----
const stops = [];
const TOUR_MAX = 12;

function isViaje() {
  return document.querySelector(".mode__btn--on")?.dataset.mode === "viaje";
}

async function addStop(lat, lon) {
  if (stops.length >= TOUR_MAX) {
    el("trip-total").textContent = `Máximo ${TOUR_MAX} paradas.`;
    return;
  }
  // Reuse reverse geocoding so each stop is born with its address.
  let nombre = `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
  try {
    const r = await fetch(`/api/v1/reverse?lat=${lat}&lon=${lon}&radius=200&limit=1`);
    if (r.ok) {
      const d = await r.json();
      if (d.address && d.address.texto) nombre = d.address.texto;
    }
  } catch (_) { /* keep the coordinate fallback */ }
  stops.push({ lat, lon, nombre });
  renderStops();
}

function renderStops() {
  const box = el("stops");
  box.innerHTML = "";
  stops.forEach((s, i) => {
    const li = document.createElement("li");
    const tag = document.createElement("span");
    tag.className = "stop__tag";
    tag.textContent = i === 0 ? "origen" : `${i}`;
    const name = document.createElement("span");
    name.className = "stop__name";
    name.textContent = s.nombre;
    const del = document.createElement("button");
    del.type = "button";
    del.className = "stop__del";
    del.textContent = "×";
    del.addEventListener("click", () => { stops.splice(i, 1); renderStops(); });
    li.append(tag, name, del);
    box.appendChild(li);
  });
  el("trip-go").disabled = stops.length < 3;
  clearOverlay();
  stops.forEach((s) => addPin(s.lat, s.lon));
}

function clearStops() {
  stops.length = 0;
  el("trip-total").textContent = "";
  renderStops();
}

async function optimizeTour() {
  if (stops.length < 3) return;
  const btn = el("trip-go");
  btn.disabled = true;
  el("trip-total").textContent = "Calculando…";
  try {
    const res = await fetch("/api/v1/tour", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        points: stops.map((s) => ({ lat: s.lat, lon: s.lon, nombre: s.nombre })),
        return_to_start: el("trip-return").checked,
      }),
    });
    const d = await res.json();
    if (!res.ok || d.reason !== "ok") {
      el("trip-total").textContent =
        d.reason === "unreachable" ? "Una parada no se puede alcanzar por calle."
                                   : (d.detail || "No se pudo optimizar.");
      return;
    }
    // Reorder the stops to the optimal order so the list renumbers.
    const reordered = d.order.map((i) => stops[i]);
    stops.length = 0;
    stops.push(...reordered);
    renderStops();
    if (d.geometry) {
      setOverlay([{ type: "Feature", properties: { role: "route" }, geometry: d.geometry }]);
      frameBounds(boundsOf(d.geometry), { maxZoom: 16, padding: 60 });
    }
    el("trip-total").textContent =
      `${(d.distance_m / 1000).toFixed(2)} km · ~${Math.round(d.duration_min)} min`;
  } catch (_) {
    el("trip-total").textContent = "No se pudo consultar el servicio.";
  } finally {
    btn.disabled = stops.length < 3;
  }
}

el("trip-go").addEventListener("click", optimizeTour);
el("trip-clear").addEventListener("click", clearStops);

// A nearby place becomes the selection; keep the list so the user can move on.
function selectNearby(p, list) {
  setSelection({
    source: "poi",
    query: null,
    nombre: p.nombre,
    tipo: p.tipo || "poi",
    place: [p.barrio, p.municipio, p.provincia].filter(Boolean).join(" · "),
    parse: null,
    point: { lat: p.coordinates.lat, lon: p.coordinates.lon },
    radius_m: 25,
    methodLabel: "lugar",
    nearby: list,
  });
  const { lat, lon } = p.coordinates;
  clearOverlay();
  addPin(lat, lon);
  frameBounds(boundsAround(lat, lon, 200), { maxZoom: 18 });
}

// Reverse-geocode a clicked point: fill the panel from the address, list the
// nearby places beneath it.
async function reverseAt(lat, lon) {
  let d;
  try {
    const res = await fetch(`/api/v1/reverse?lat=${lat}&lon=${lon}&radius=300&limit=6`);
    if (!res.ok) return;
    d = await res.json();
  } catch (_) {
    return;
  }
  const a = d.address;
  setSelection({
    source: "click",
    query: null,
    nombre: a ? a.texto : "Punto en el mapa",
    tipo: a ? (a.calle ? "dirección" : "") : "",
    place: a ? [a.barrio, a.municipio, a.provincia].filter(Boolean).join(" · ") : "",
    parse: null,
    point: { lat, lon },
    radius_m: a ? a.radius_m : 25,
    methodLabel: a ? (METHOD_ES[a.method] || a.method) : "sin dirección cercana",
    nearby: d.results || [],
  });
  clearOverlay();
  const radius = Math.max((selection.radius_m) || 15, 8);
  setOverlay([{ type: "Feature", properties: { role: "area" },
               geometry: circlePolygon(lat, lon, radius) }]);
  addPin(lat, lon);
  frameBounds(boundsAround(lat, lon, radius * 4), { maxZoom: 18 });
}

function showEmpty() {
  result.hidden = true;
  empty.hidden = false;
  el("candidates").hidden = true;
  el("candidates").innerHTML = "";
  clearOverlay();
}

let candidates = [];

function renderCandidates(selIdx) {
  const box = el("candidates");
  if (candidates.length <= 1) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  box.hidden = false;
  box.innerHTML = "";
  candidates.forEach((c, i) => {
    const li = document.createElement("li");
    li.className = "cand" + (i === selIdx ? " cand--sel" : "");
    const place = [c.barrio, c.municipio].filter(Boolean).join(" · ");
    li.innerHTML =
      `<span class="cand__n">${i + 1}</span>` +
      `<span class="cand__name"></span>` +
      `<span class="cand__meta"></span>`;
    li.querySelector(".cand__name").textContent = c.nombre;
    li.querySelector(".cand__meta").textContent = place;
    li.addEventListener("click", () => select(i));
    box.appendChild(li);
  });
}

function select(i) {
  renderCandidates(i);
  showResult(candidates[i]);
}

function showResults(results) {
  candidates = results;
  select(0);  // auto-select the best; click another to compare
}

let busy = false;
const goBtn = document.getElementById("go");
function setBusy(on) {
  busy = on;
  goBtn.disabled = on;
  goBtn.classList.toggle("busy", on);
}

async function resolve(q) {
  if (busy || !q.trim()) return;
  setBusy(true);
  try {
    const res = await fetch(`/api/v1/resolve?q=${encodeURIComponent(q)}&limit=5`);
    const data = await res.json();
    if (data.results && data.results.length) showResults(data.results);
    else showEmpty();
  } catch (e) {
    showEmpty();
    empty.textContent = "No pudimos consultar el servicio. Intenta de nuevo.";
  } finally {
    setBusy(false);
  }
}

let mode = "point";
const streetsInput = document.getElementById("streets");
const rutaFrom = document.getElementById("ruta-from");
const rutaTo = document.getElementById("ruta-to");
document.getElementById("mode").addEventListener("click", (e) => {
  const btn = e.target.closest(".mode__btn");
  if (!btn) return;
  mode = btn.dataset.mode;
  document.querySelectorAll(".mode__btn").forEach((b) =>
    b.classList.toggle("mode__btn--on", b === btn));
  const region = mode === "region";
  const ruta = mode === "ruta";
  const viaje = mode === "viaje";
  const lote = mode === "lote";
  form.hidden = lote;                       // Lote takes a file, not a query
  el("examples").hidden = lote;
  el("click-hint").hidden = lote;
  el("lote").hidden = !lote;
  setLoteVisible(lote);
  if (lote) { result.hidden = true; empty.hidden = true; clearOverlay(); }
  streetsInput.hidden = !region;
  rutaFrom.hidden = !ruta;
  rutaTo.hidden = !ruta;
  form.classList.toggle("search--ruta", ruta);   // two-row layout, see style.css
  input.hidden = region || ruta || viaje;   // Viaje takes its stops from clicks
  el("trip").hidden = !viaje;
  if (viaje) { result.hidden = true; empty.hidden = true; }
});

form.addEventListener("submit", (e) => {
  e.preventDefault();
  if (mode === "viaje") return;          // Viaje has no text input; stops come from clicks
  if (mode === "region") resolveRegion(streetsInput.value);
  else if (mode === "ruta") resolveRoute(rutaFrom.value, rutaTo.value);
  else resolve(input.value);
});

document.getElementById("examples").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  input.value = btn.textContent;
  resolve(btn.textContent);
});
