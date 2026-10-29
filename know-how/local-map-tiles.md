# Local map tiles (offline basemap)

*Reach for this when touching the web UI's basemap, rebuilding the vector tiles,
or debugging why the map renders blank.*

The UI renders **entirely offline**: MapLibre GL, pmtiles.js, the web fonts, and
the map's SDF label glyphs are vendored under `src/geocoder/api/static/vendor/`,
and the basemap is a local vector-tile archive served from the app — no CDN, no
external tiles, no external glyph server.

## The tile archive

`data/tiles/cuba.pmtiles` (~72 MB, z0-14) is a **PMTiles** vector archive built
from `data/cuba-latest.osm.pbf` with **tilemaker** (C++, non-Java). It is a build
artifact — **gitignored** like the geo DB. Regenerate it with:

```bash
bin/build-tiles                 # data/cuba-latest.osm.pbf -> data/tiles/cuba.pmtiles
```

The schema is deliberately minimal — `deploy/tiles/config.json` +
`deploy/tiles/process.lua` emit five layers: `water`, `landuse`, `roads`,
`boundaries`, `places`. The dark UI background stands in for the sea, so no
coastline/ocean polygons are generated (those would need a separate
water-polygons shapefile). `node_keys = { "place" }` in the Lua is required — without
it tilemaker processes no nodes and the `places` (label) layer is empty.

Note: tilemaker's `Generated points/lines/polygons` summary counts only
relation-derived geometry, **not** per-way features — a low `lines:` count there is
normal and does not mean roads are missing. Verify by decoding a tile
(`zcat data/tiles/.../z/x/y.pbf | strings | grep roads`) or just load the UI.

## Serving

`main.py` mounts `data/tiles/` at `/tiles` **before** the `/` catch-all. Starlette's
`StaticFiles` serves HTTP **Range** (206) natively, which is what pmtiles.js needs to
read tiles out of the single archive. If the map is blank, check that
`data/tiles/cuba.pmtiles` exists and `GET /tiles/cuba.pmtiles` with a `Range` header
returns 206.

## Rendering

`app.js` builds a **MapLibre GL** map (WebGL) from an inline style over the local
archive, read through the pmtiles protocol:

```js
maplibregl.addProtocol("pmtiles", new pmtiles.Protocol().tile);
new maplibregl.Map({ style: { version: 8, sources: { basemap: {
  type: "vector", url: "pmtiles://" + location.origin + "/tiles/cuba.pmtiles" } }, ... } });
```

Style layers are keyed by the tilemaker **layer names** above (`source-layer`) — add
a layer to `process.lua` and you must add a matching style layer. The dark palette
lives in the `DARK` object at the top of the basemap block. Overlays (result ring,
route line, region polygon) are a separate GeoJSON source + fill/line layers plus
DOM `maplibregl.Marker`s; see the overlay block further down `app.js`.

Pins pinned by the swap (don't "upgrade" blindly): **maplibre-gl 5.24.0** is the last
UMD build (6.x is ESM-only, no `<script>` global), and **pmtiles 3.2.1** is the
version that pairs with maplibre 5.x.

### Label glyphs

MapLibre renders text from **SDF glyph PBFs**, which protomaps-leaflet did not need.
They are vendored per Unicode range under
`src/geocoder/api/static/vendor/glyphs/Inter Medium/` and referenced by the style as
`glyphs: "/static/vendor/glyphs/{fontstack}/{range}.pbf"`; the `places` symbol layer
asks for `"text-font": ["Inter Medium"]` — **that string must equal the directory
name**, or every label silently disappears.

Only three ranges ship (0-255, 256-511, 8192-8447 — Latin, Latin Extended-A,
punctuation): enough for Cuban place names, ~264 KB total. A label containing a
codepoint outside them 404s and is dropped. Regenerate with:

```bash
bin/build-glyphs                # fetches Inter 4.1 + fontnik, writes the .pbf ranges
```

The generated `.pbf` files **are committed** (the served asset); the source TTF under
`data/fonts/` is gitignored. Range list lives in `deploy/glyphs/build-glyphs.mjs`.

### Debugging a blank map

`saidkick` opens tabs in the background, and Chrome freezes `requestAnimationFrame`
there — MapLibre then never paints and `queryRenderedFeatures` returns `[]`, with no
error. Always pass `--activate`. A blank canvas in a *foreground* tab is a real bug;
a blank canvas in a background tab is this.

tile pipeline) and `2026-07-22-maplibre-webgl-basemap*` (the engine swap).
