"use strict";

// ---- Lote mode: an Excel/CSV of addresses -> /resolve/batch -> points on the map ----
// The spreadsheet is read in the browser (an .xlsx is a zip of XML, and
// DecompressionStream inflates it), so the server needs no upload endpoint and
// no spreadsheet library, and the UI keeps working offline.

const LOTE_CHUNK = 100;              // /resolve/batch accepts up to 100 queries
const LOTE_COLORS = {                // precision -> dot colour, tightest first
  exact_portal: "#4ADE80", esquina: "#4ADE80",
  segment_midpoint: "#5AA9F2", interpolated_entre: "#5AA9F2",
  poi: "#F2A65A", street: "#E8C547",
  barrio: "#E07A5F", municipio: "#C0506A", provincia: "#C0506A",
};
const LOTE_LEGEND = [
  ["#4ADE80", "portal / esquina"], ["#5AA9F2", "tramo e/"], ["#F2A65A", "lugar"],
  ["#E8C547", "calle"], ["#E07A5F", "barrio"], ["#C0506A", "municipio"],
];

const lote = { header: [], rows: [], out: [], running: false, stop: false };

// ---------- reading .xlsx / .csv ----------
async function inflate(bytes, method) {
  if (method === 0) return new TextDecoder().decode(bytes);
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
  return await new Response(stream).text();
}

async function unzip(buf) {
  const dv = new DataView(buf);
  const u8 = new Uint8Array(buf);
  let eocd = buf.byteLength - 22;
  while (eocd >= 0 && dv.getUint32(eocd, true) !== 0x06054b50) eocd--;
  if (eocd < 0) throw new Error("no es un .xlsx válido");
  const n = dv.getUint16(eocd + 10, true);
  let p = dv.getUint32(eocd + 16, true);
  const files = {};
  for (let i = 0; i < n; i++) {
    const method = dv.getUint16(p + 10, true);
    const size = dv.getUint32(p + 20, true);
    const nameLen = dv.getUint16(p + 28, true);
    const skip = nameLen + dv.getUint16(p + 30, true) + dv.getUint16(p + 32, true);
    const local = dv.getUint32(p + 42, true);
    const name = new TextDecoder().decode(u8.subarray(p + 46, p + 46 + nameLen));
    const start = local + 30 + dv.getUint16(local + 26, true) + dv.getUint16(local + 28, true);
    files[name] = () => inflate(u8.subarray(start, start + size), method);
    p += 46 + skip;
  }
  return files;
}

const xml = (s) => new DOMParser().parseFromString(s, "application/xml");
const colIndex = (ref) => {
  let n = 0;
  for (const ch of ref.replace(/\d+$/, "")) n = n * 26 + (ch.charCodeAt(0) - 64);
  return n - 1;
};

async function readXlsx(buf) {
  const files = await unzip(buf);
  const shared = files["xl/sharedStrings.xml"]
    ? [...xml(await files["xl/sharedStrings.xml"]()).getElementsByTagName("si")]
        .map((si) => [...si.getElementsByTagName("t")].map((t) => t.textContent).join(""))
    : [];
  // The first sheet in workbook order, through its relationship id.
  const wb = xml(await files["xl/workbook.xml"]());
  const rid = wb.getElementsByTagName("sheet")[0].getAttribute("r:id");
  const rels = xml(await files["xl/_rels/workbook.xml.rels"]());
  const target = [...rels.getElementsByTagName("Relationship")]
    .find((r) => r.getAttribute("Id") === rid).getAttribute("Target").replace(/^\/?(xl\/)?/, "");
  const sheet = xml(await files["xl/" + target]());
  const rows = [];
  for (const row of sheet.getElementsByTagName("row")) {
    const out = [];
    for (const c of row.getElementsByTagName("c")) {
      const t = c.getAttribute("t");
      const v = c.getElementsByTagName("v")[0]?.textContent ?? "";
      const val = t === "s" ? shared[+v]
        : t === "inlineStr" ? [...c.getElementsByTagName("t")].map((x) => x.textContent).join("")
        : v !== "" && (t === null || t === "n") ? Number(v)   // keep numbers numeric
        : v;
      out[colIndex(c.getAttribute("r"))] = val;
    }
    rows.push(Array.from(out, (x) => x ?? ""));
  }
  return rows;
}

function readCsv(text) {
  const first = text.split("\n", 1)[0];
  const sep = (first.match(/;/g) || []).length > (first.match(/,/g) || []).length ? ";" : ",";
  const rows = [];
  let row = [], cell = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (q) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (ch === '"') q = false;
      else cell += ch;
    } else if (ch === '"') q = true;
    else if (ch === sep) { row.push(cell); cell = ""; }
    else if (ch === "\n") { row.push(cell.replace(/\r$/, "")); rows.push(row); row = []; cell = ""; }
    else cell += ch;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  return rows;
}

// ---------- writing .xlsx (stored zip, inline strings) ----------
const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
function crc32(u8) {
  let c = 0xffffffff;
  for (const b of u8) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function zip(entries) {                   // [[name, string]] -> Blob, no compression
  const enc = new TextEncoder();
  const parts = [], central = [];
  let offset = 0;
  for (const [name, text] of entries) {
    const data = enc.encode(text), nm = enc.encode(name), crc = crc32(data);
    const head = new DataView(new ArrayBuffer(30));
    head.setUint32(0, 0x04034b50, true); head.setUint16(4, 20, true);
    head.setUint16(6, 0x0800, true);     // UTF-8 names
    head.setUint32(14, crc, true); head.setUint32(18, data.length, true);
    head.setUint32(22, data.length, true); head.setUint16(26, nm.length, true);
    parts.push(head, nm, data);
    const cd = new DataView(new ArrayBuffer(46));
    cd.setUint32(0, 0x02014b50, true); cd.setUint16(4, 20, true); cd.setUint16(6, 20, true);
    cd.setUint16(8, 0x0800, true);
    cd.setUint32(16, crc, true); cd.setUint32(20, data.length, true);
    cd.setUint32(24, data.length, true); cd.setUint16(28, nm.length, true);
    cd.setUint32(42, offset, true);
    central.push(cd, nm);
    offset += 30 + nm.length + data.length;
  }
  const cdSize = central.reduce((s, x) => s + x.byteLength, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true);
  end.setUint16(8, entries.length, true); end.setUint16(10, entries.length, true);
  end.setUint32(12, cdSize, true); end.setUint32(16, offset, true);
  return new Blob([...parts, ...central, end],
    { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
}

const esc = (s) => String(s).replace(/[<>&"]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" })[c]);
const colName = (i) => (i >= 26 ? colName(Math.floor(i / 26) - 1) : "") + String.fromCharCode(65 + (i % 26));

function writeXlsx(rows) {
  const body = rows.map((r, i) => `<row r="${i + 1}">` + r.map((v, j) => {
    const ref = colName(j) + (i + 1);
    return typeof v === "number" && Number.isFinite(v)
      ? `<c r="${ref}"><v>${v}</v></c>`
      : `<c r="${ref}" t="inlineStr"><is><t>${esc(v ?? "")}</t></is></c>`;
  }).join("") + "</row>").join("");
  const NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"';
  const R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
  return zip([
    ["[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'],
    ["_rels/.rels", `<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="${R}/officeDocument" Target="xl/workbook.xml"/></Relationships>`],
    ["xl/workbook.xml", `<?xml version="1.0" encoding="UTF-8"?><workbook ${NS} xmlns:r="${R}"><sheets><sheet name="Geocodificado" sheetId="1" r:id="rId1"/></sheets></workbook>`],
    ["xl/_rels/workbook.xml.rels", `<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="${R}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>`],
    ["xl/worksheets/sheet1.xml", `<?xml version="1.0" encoding="UTF-8"?><worksheet ${NS}><sheetData>${body}</sheetData></worksheet>`],
  ]);
}

// ---------- UI ----------
const loteFile = el("lote-file");
const loteCol = el("lote-col");
const loteMuni = el("lote-muni");
const loteGo = el("lote-go");
const loteDl = el("lote-dl");
const loteStatus = el("lote-status");
const loteBar = el("lote-bar");

const fold = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

function fillSelect(sel, guess, allowNone) {
  sel.innerHTML = "";
  if (allowNone) sel.add(new Option("— ninguna —", "-1"));
  lote.header.forEach((h, i) => sel.add(new Option(h || `Columna ${colName(i)}`, String(i))));
  sel.value = String(guess);
}

loteFile.addEventListener("change", async () => {
  const f = loteFile.files[0];
  if (!f) return;
  el("lote-fname").textContent = f.name;
  loteStatus.textContent = "Leyendo…";
  try {
    const rows = /\.csv$/i.test(f.name) ? readCsv(await f.text()) : await readXlsx(await f.arrayBuffer());
    const data = rows.filter((r) => r.some((c) => String(c).trim()));
    lote.header = data[0] || [];
    lote.rows = data.slice(1);
  } catch (e) {
    loteStatus.textContent = `No pude leer el archivo: ${e.message}`;
    return;
  }
  const H = lote.header.map(fold);
  const addr = H.findIndex((h) => /direcc|domicilio|address|ubicaci/.test(h));
  const muni = H.findIndex((h) => /municip/.test(h));
  fillSelect(loteCol, addr >= 0 ? addr : 0, false);
  fillSelect(loteMuni, muni, true);
  el("lote-cols").hidden = false;
  loteGo.disabled = !lote.rows.length;
  loteStatus.textContent = `${lote.rows.length.toLocaleString("es")} filas en «${f.name}».`;
});

function loteQuery(row) {
  const a = String(row[+loteCol.value] ?? "").trim();
  const m = +loteMuni.value >= 0 ? String(row[+loteMuni.value] ?? "").trim() : "";
  return m && !fold(a).includes(fold(m)) ? `${a}, ${m}` : a;
}

function loteSource() {
  if (map.getSource("lote")) return map.getSource("lote");
  map.addSource("lote", { type: "geojson", data: EMPTY_FC });
  const color = ["match", ["get", "precision"]];
  for (const [k, v] of Object.entries(LOTE_COLORS)) color.push(k, v);
  color.push("#7C93A8");
  map.addLayer({
    id: "lote-pts", type: "circle", source: "lote",
    paint: {
      "circle-color": color,
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 6, 2.5, 12, 5, 16, 8],
      "circle-stroke-color": "#0B1420", "circle-stroke-width": 0.8, "circle-opacity": 0.9,
    },
  });
  map.on("mouseenter", "lote-pts", () => { map.getCanvas().style.cursor = "pointer"; });
  map.on("mouseleave", "lote-pts", () => { map.getCanvas().style.cursor = "crosshair"; });
  map.on("click", "lote-pts", (e) => lotePopup(e.features[0], e.lngLat));
  return map.getSource("lote");
}

function loteFeatures() {
  const feats = [];
  lote.out.forEach((o, i) => {
    if (o && o.hit) feats.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: [o.hit.coordinates.lon, o.hit.coordinates.lat] },
      properties: { i, precision: o.hit.precision },
    });
  });
  return feats;
}

function lotePopup(f, lngLat) {
  const i = f.properties.i, row = lote.rows[i], o = lote.out[i];
  const cells = lote.header.map((h, j) => row[j] ? `<dt>${esc(h)}</dt><dd>${esc(row[j])}</dd>` : "").join("");
  const h = o.hit;
  new maplibregl.Popup({ className: "lote-pop", maxWidth: "320px" })
    .setLngLat(lngLat)
    .setHTML(`<dl>${cells}</dl><p class="lote-pop__res">→ ${esc(h.nombre)} · ${esc(h.municipio)}<br>` +
      `<span>${esc(METHOD_ES[h.precision] || h.precision)} · ±${Math.round(h.radius_m)} m</span></p>`)
    .addTo(map);
}

function renderMisses() {
  const misses = lote.out.map((o, i) => (o && !o.hit ? i : -1)).filter((i) => i >= 0);
  const box = el("lote-miss");
  box.hidden = !misses.length;
  box.innerHTML = `<summary>${misses.length} sin resultado</summary>` +
    misses.slice(0, 200).map((i) => `<li><span class="mono">${i + 2}</span> ${esc(loteQuery(lote.rows[i]))}</li>`).join("");
}

async function loteRun() {
  if (lote.running) { lote.stop = true; return; }
  lote.running = true; lote.stop = false;
  lote.out = new Array(lote.rows.length);
  el("lote-views").hidden = true;          // views work on a finished run
  covResetResult();
  dbResetResult();
  if (map.getLayer("lote-pts")) showLayers(["lote-pts"]);
  loteGo.textContent = "Detener";
  loteDl.disabled = true;
  el("lote-legend").hidden = false;
  const src = loteSource();
  const t0 = performance.now();
  let done = 0, hits = 0, framed = false;
  for (let i = 0; i < lote.rows.length && !lote.stop; i += LOTE_CHUNK) {
    const idx = [], queries = [];
    for (let j = i; j < Math.min(i + LOTE_CHUNK, lote.rows.length); j++) {
      const q = loteQuery(lote.rows[j]);
      if (q) { idx.push(j); queries.push(q); } else lote.out[j] = { hit: null };
    }
    if (queries.length) {
      try {
        const r = await fetch("/api/v1/resolve/batch?limit=1", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ queries }),
        });
        const data = await r.json();
        data.results.forEach((res, k) => { lote.out[idx[k]] = { hit: res.results[0] || null }; });
      } catch (_) {
        idx.forEach((j) => { lote.out[j] = { hit: null }; });
      }
    }
    done = Math.min(i + LOTE_CHUNK, lote.rows.length);
    const feats = loteFeatures();
    hits = feats.length;
    src.setData({ type: "FeatureCollection", features: feats });
    if (!framed && feats.length) { frameLote(feats); framed = true; }
    const secs = (performance.now() - t0) / 1000;
    loteBar.style.width = `${(100 * done) / lote.rows.length}%`;
    loteStatus.textContent = `${done.toLocaleString("es")} / ${lote.rows.length.toLocaleString("es")} · ` +
      `${hits.toLocaleString("es")} en el mapa · ${(done / secs).toFixed(0)} dir/s · ${secs.toFixed(1)} s`;
  }
  const feats = loteFeatures();
  if (feats.length) frameLote(feats);
  renderMisses();
  lote.running = false;
  loteGo.textContent = "Geocodificar";
  loteDl.disabled = !done;
  if (done) loteViewsReady();
}

function frameLote(feats) {
  const b = new maplibregl.LngLatBounds();
  feats.forEach((f) => b.extend(f.geometry.coordinates));
  map.fitBounds(b, { padding: { top: 60, bottom: 60, left: 480, right: 60 }, maxZoom: 15, duration: reduceMotion ? 0 : 800 });
}

function loteDownload() {
  const withCov = !!cov.result;             // Cobertura computed: add its two columns
  const withDb = !!db.result;               // Grupos → Análisis computed: add `cluster`
  const extra = ["lat", "lon", "precision", "radio_m", "resultado", "municipio_resultado",
                 ...(withCov ? ["centro", "minutos_al_centro"] : []), ...(withDb ? ["cluster"] : [])];
  const rows = [[...lote.header, ...extra]];
  lote.rows.forEach((r, i) => {
    const h = lote.out[i]?.hit;
    const pad = Array.from({ length: lote.header.length }, (_, j) => r[j] ?? "");
    const c = withCov ? coverageOf(i) : null;
    const covCols = [...(withCov ? [c.center === null ? "" : c.center + 1, c.minutes ?? ""] : []),
                     ...(withDb ? [clusterOf(i) === null ? "" : clusterOf(i) + 1] : [])];
    rows.push(h ? [...pad, h.coordinates.lat, h.coordinates.lon, METHOD_ES[h.precision] || h.precision,
      Math.round(h.radius_m), h.nombre, h.municipio, ...covCols]
      : [...pad, "", "", "sin resultado", "", "", "", ...covCols]);
  });
  const name = (loteFile.files[0]?.name || "lote").replace(/\.(xlsx|csv)$/i, "");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(writeXlsx(rows));
  a.download = `${name}-geocodificado.xlsx`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

el("lote-legend").innerHTML = LOTE_LEGEND
  .map(([c, t]) => `<li><i style="background:${c}"></i>${t}</li>`).join("");
loteGo.addEventListener("click", loteRun);
loteDl.addEventListener("click", loteDownload);

// Called by the mode switch in app.js: the batch layer only shows in Lote.
function setLoteVisible(on) {
  if (!on) { showLayers([]); setCoverageActive(false); }
  else if (!el("lote-views").hidden) setLoteView(loteView);
  else if (map.getLayer("lote-pts")) showLayers(["lote-pts"]);
}
