"use strict";

// ---- Isochrones: how far you get by car from the selected point ----
// Loaded after app.js; reads `selection`, `map`, `el` and frameBounds from it.

const ISO_MINUTES = [5, 10, 15];
// One hue (the route blue), ordinal: the nearest band is the brightest. On the
// dark map no step is darker than 550, which still clears 2:1 on the surface.
const ISO_COLORS = { 5: "#86b6ef", 10: "#3987e5", 15: "#1c5cab" };

function isoSource() {
  if (map.getSource("iso")) return map.getSource("iso");
  map.addSource("iso", { type: "geojson", data: EMPTY_FC });
  const color = ["match", ["get", "minutes"]];
  for (const [m, c] of Object.entries(ISO_COLORS)) color.push(+m, c);
  color.push("#3987e5");
  const below = map.getLayer("overlay-fill") ? "overlay-fill" : undefined;
  // Bands arrive widest first, so the nearer ones stack on top of the farther.
  map.addLayer({ id: "iso-fill", type: "fill", source: "iso",
                 paint: { "fill-color": color, "fill-opacity": 0.35 } }, below);
  map.addLayer({ id: "iso-line", type: "line", source: "iso",
                 paint: { "line-color": color, "line-width": 1.2, "line-opacity": 0.9 } }, below);
  return map.getSource("iso");
}

// Called from clearOverlay(): any new result, route or region replaces the bands.
function clearIso() {
  if (map.getSource("iso")) map.getSource("iso").setData(EMPTY_FC);
  const list = el("iso-bands");
  if (list) { list.hidden = true; list.innerHTML = ""; }
}

async function showIsochrone() {
  if (!selection || !selection.point) return;
  const btn = el("iso-go"), list = el("iso-bands");
  btn.disabled = true;
  list.hidden = false;
  list.innerHTML = "<li>Calculando… la primera vez carga el grafo de calles de toda Cuba.</li>";
  try {
    const r = await fetch("/api/v1/isochrone", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: selection.point.lat, lon: selection.point.lon, minutes: ISO_MINUTES }),
    });
    const data = await r.json();
    if (!r.ok) {
      list.innerHTML = `<li>${String(data.detail || "").startsWith("off_network")
        ? "Este punto no está cerca de ninguna calle." : "No se pudo calcular."}</li>`;
      return;
    }
    isoSource().setData(data);
    list.innerHTML = [...data.features].reverse().map((f) =>
      `<li><i style="background:${ISO_COLORS[f.properties.minutes]}"></i>${f.properties.minutes} min en coche` +
      `<span>${f.properties.area_km2.toLocaleString("es")} km²</span></li>`).join("");
    if (data.features.length) frameBounds(boundsOf(data.features[0].geometry),
      { maxZoom: 15, padding: { top: 40, bottom: 40, left: 460, right: 40 } });
  } catch (_) {
    list.innerHTML = "<li>No se pudo consultar el servicio.</li>";
  } finally {
    btn.disabled = false;
  }
}

el("iso-go").addEventListener("click", showIsochrone);
