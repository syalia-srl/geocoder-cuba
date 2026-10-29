# AGENTS.md — geocoder

Offline geocoding service for Cuba built on OpenStreetMap data. ETL (osmium →
shapely → **beaver**) builds a local geo DB; a search pipeline (FTS broad recall →
rapidfuzz rerank, plus exact-portal and geometric-intersection paths) serves a
FastAPI REST API. Python 3.11+, `uv`, `beaver-db`.

## Door — current status

- **Beaver migration shipped (2026-07-18).** The repo was audited and found to
  have never run end-to-end; the fix re-homed persistence from raw SQLite onto
  **beaver** and the vision now works on real data. A real La Habana build
  (`--bbox habana`) yields **64.9k lugares** (16.5k streets, 6.4k portals, 42k
  POIs); `/geocode`, `/reverse` (nearest-neighbor), `/health`, `/municipios` all
  return real Havana results with correct municipio + barrio enrichment. Full
  suite green.
- **Routing shipped (2026-07-22).** Fastest-path A→B over the street network:
  a routing graph is built from raw OSM ways during the ETL (node id = identity,
  degree-2 chains contracted), stored in `graph_nodes`/`graph_edges`, routed with
  an admissible-heuristic A* by time (`search/router.py`). Endpoints resolve via
  the normal resolver, then snap onto the nearest edge. Surfaces:
  `geocoder route "<a>" "<b>"`, `POST /api/v1/route` (GeoJSON), and the "Ruta" UI
  mode. Validated on a real Habana build (40.5k-node graph; Hotel Nacional →
  El Capitolio ≈ 3.3 km / ~4 min).

## Known follow-ups (not yet done)

- ~~**beaver release**~~ — **resolved.** `beaver-db 2.1.0` (PyPI, 2026-07-18)
  carries the sync filtered-search the geocoder needs; `pyproject.toml` requires
  `>=2.1.0` and `uv.lock` pins it from PyPI. No sibling editable checkout is
  needed, and Docker/PyPI installs work.
- **beaver bug (filed: beaver issue #40)**: a second *large* `batched()` write
  under the sync portal deadlocks (root-caused: sync bridge drives async CM steps
  as separate tasks; `executemany` self-locks with `BEGIN IMMEDIATE` held).
  `GeoStore` uses a single batch per collection as a workaround — fine for Habana;
  a full-Cuba build needs #40 fixed to chunk safely.
- **build speed**: after batching writes (7.5s for 64.9k rows), the bottleneck is
  osmium doing 3 full PBF passes (~250s+; admin is now parsed unclipped, ~400s
  total). Collapsing to one pass would ~3× it.

### Resolved (data-quality pass, 2026-07-18)

Municipio and barrio enrichment now work correctly on real data — see the
`fix(etl)` commits. What it took (each was a real bug):

- **STRtree PIP predicate** was backwards (`contains` → `within`).
- **Cuba admin levels**: province=**4**, municipio=**6** (Havana's 15 municipios
  are level 6, not 8 — the 6/8 assumption stored them as provincias).
- **Multi-way boundary assembly**: `linemerge`+`polygonize` to stitch member-way
  segments into rings (was force-closing each way individually).
- **Coastal boundaries**: store *all* member-way geometry — Havana's coastal
  municipios reuse `natural=coastline` ways, which the boundary-tag filter dropped.
- **Admin parsed unclipped** (`bbox=None`) so boundary polygons fully assemble;
  entities stay bbox-filtered.
- **Node-barrio fallback**: nearest barrio centroid (2.2 km cap) for the ~63% of
  OSM barrios that are nodes.
- **beaver `dict.get()` raises on miss** — gate keyed lookups with `k in dict`.

Result: 167 municipios + 16 provincias assemble; Hotel Nacional →
"Plaza de la Revolución" / "Cayo Hueso"; barrio coverage ~96%; `municipio:`
filter works.

## Conventions

- **Commit straight to `main`** (no PR unless the maintainer asks). Conventional commits.
- **Package management: `uv`, never bare pip** — `uv pip install -e ".[api,dev]"`,
  `uv run geocoder ...`, `uv run pytest`.
- **TDD.** Changes start with a failing test; tests run inline, never delegated.
- **Lint as its own step** — `uv run ruff check src tests`; never pipe the gate.
- **beaver is the persistence layer** — no raw `sqlite3`/schema/FTS-trigger SQL in
  app code. Storage goes through `GeoStore` (the single persistence unit). beaver
  is a sibling syalia-srl repo (`repos/beaver`, tracked in dev via
  `uv pip install -e ../beaver`); **if the geocoder needs a capability beaver
  lacks, extend beaver** rather than work around it. beaver API traps and the
  storage layout live in `know-how/beaver-data-model.md`.

## Layout

- `src/geocoder/cli/main.py` — typer CLI (`download`, `build`, `search`, `serve`,
  `stats`, `bench`, `region`, `route`). Entry point `geocoder = geocoder.cli.main:cli`.
- `src/geocoder/etl/` — `download`, `admin_parser`, `places_parser`,
  `entity_parser`, `enrich` (STRtree point-in-polygon), `build` (orchestrator).
- `src/geocoder/store.py` — `GeoStore`: beaver-backed persistence
  (`lugares` collection + `portales`/`calles_geom`/`municipios`/… dicts).
- `src/geocoder/search/` — `parser`, `fts`, `fuzzy` (rapidfuzz WRatio), `portal`,
  `intersection`, `pipeline`, `router` (A* over the graph, behind
  `SearchPipeline.route` / `geocoder route` / `POST /api/v1/route`);
  **`address`** (Cuban address/place/area parser →
  `ParsedLocation`) + **`resolver`** (unified ladder → point + `precision` +
  `radius_m`; behind `pipeline.resolve()`, `/api/v1/resolve`, `geocoder resolve`).
  **`tour`** (`search/tour.py`) is the exact Held-Karp solver for the visiting
  order of several stops — pure matrix-in/order-out, no store or router, so it is
  tested against matrices whose optimum is known by inspection. `pipeline.tour()`
  builds the N×N time matrix from pairwise A* and re-traces the real geometry;
  behind `POST /api/v1/tour` and the Viaje UI mode. Capped at 12 points (the DP is
  2^N·N²).
  **`describe`** is the reverse direction (a point → a Cuban-style address);
  it degrades `describe_segment` → `describe_street` → `describe_place` and is
  behind `pipeline.describe()` and the `address` field of `/api/v1/reverse`. It
  uses `store.street_tree()` (a cached STRtree over street geometry, warmed in
  the background at startup like the routing graph — building it inline froze
  the server).
  **`resolver.resolve_region`** builds the convex polygon enclosed by ≥3 streets
  (the `region_hull` rung → `Region`; behind `SearchPipeline.resolve_region`,
  `POST /api/v1/region`, `geocoder region`, and the Región UI mode). It reuses the
  crossing engine, so its quality tracks the crossing data — lettered streets with
  alt-name gaps (e.g. Línea in Vedado) can leave corners unresolved.
- `src/geocoder/api/` — FastAPI app, routes (`geocode`, `reverse`, `admin`,
  `health`, `region`, `route`), schemas, dependency injection. The web UI
  (`static/`) renders **fully offline** on **MapLibre GL** (WebGL): maplibre-gl +
  pmtiles.js + web fonts + SDF label glyphs are vendored under `static/vendor/`,
  and the basemap is a local Cuba PMTiles served at `/tiles`. Tile build:
  `bin/build-tiles` (tilemaker, non-Java) with `deploy/tiles/{config.json,process.lua}`;
  label-glyph build: `bin/build-glyphs`. See `know-how/local-map-tiles.md`.
- `src/geocoder/models/` — single dataclass model set; `utils/normalize.py`.
- `tests/` — unit (`test_search`), ETL (`test_etl`, hand-written `.osm` fixture),
  integration (`test_api`), benchmarks (optional).

The stray top-level `geocoder/` package (dead v1 duplicate) is being removed —
`src/geocoder/` is the only real package.

## know-how/ — reach for these by task

- `know-how/building-the-database.md` — *reach for it when building or rebuilding
  the geo DB (download PBF → `geocoder build --bbox habana` / `--full-cuba`),
  or debugging an ETL run that produces an empty/partial DB.*
- `know-how/beaver-data-model.md` — *reach for it when touching persistence or
  search: the collection/dict layout, how forward/reverse/portal/intersection
  queries map onto beaver, and why street geometry is kept out of the FTS doc.*
- `know-how/running-and-testing.md` — *reach for it when running the API, running
  the test suite (unit vs ETL-fixture vs integration), or smoke-testing the
  README curl set against a real Havana build.*
- `know-how/deploying-airgapped.md` — *reach for it when shipping the geocoder to
  a machine with no internet: which producer (Docker bundle vs no-engine
  tarball), the shared bake stage and why it refuses stale data, the smoke gates,
  the USB step, and the target-side install.*
