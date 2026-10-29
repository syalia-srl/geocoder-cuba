# Cuba Geocoder

Offline geocoding service for Cuba built on OpenStreetMap data. Builds a local
geo database from an OSM PBF and serves forward/reverse geocoding over a FastAPI
REST API and a CLI — no external API calls at query time.

## Features

- **Fully offline** — one local database file; no network at query time.
- **Streets, portals, POIs** — 64.9k searchable places for La Habana (16.5k
  streets, 6.4k portals, 42k POIs).
- **Administrative enrichment** — real OSM boundary polygons; every place is
  point-in-polygon tagged with its **municipio** and **barrio** (e.g. *Hotel
  Nacional → Plaza de la Revolución / Cayo Hueso*).
- **Exact portal lookup** — `addr:housenumber` + `addr:street`, O(1).
- **Geometric intersections** — real `LineString` intersection for `A y B`.
- **Fuzzy matching** — typo tolerance via `rapidfuzz` (WRatio re-rank).
- **Reverse geocoding** — nearest places to a coordinate (bbox + haversine).
- **REST API** — `/geocode`, `/reverse`, `/municipios`, `/health`, `/stats`.

Persistence is [**beaver**](https://github.com/syalia-srl/beaver) (SQLite-backed
document + kv store); there is no hand-written SQL in the app.

## Requirements

- **Python ≥ 3.12** (beaver uses PEP 695 generics).
- [`uv`](https://docs.astral.sh/uv/) for env + running (never bare `pip`).

## Install

```bash
uv venv --python 3.12
uv pip install -e ".[api,dev]"
# dev tracks the local beaver (its sync filtered-search isn't in a published
# release yet — see "beaver dependency" below):
uv pip install -e ../beaver
```

## Build the database

```bash
uv run geocoder download                     # fetch Cuba PBF from Geofabrik (~61 MB)
uv run geocoder build --bbox habana --force  # build La Habana (~6-7 min)
uv run geocoder stats                        # sanity: counts per table
```

Full Cuba: `uv run geocoder build --full-cuba` (same code path; heavier). The DB
lands at `data/cuba_geo.db` (override with `GEOCODER_DB_PATH`); `data/` is
gitignored. See `know-how/building-the-database.md` for the pipeline + gotchas.

## Search (CLI)

```bash
uv run geocoder search "Calle 23"
uv run geocoder search "23 y 12" --json
uv run geocoder search "municipio:plaza Calle 23"
```

## API server

```bash
uv run geocoder serve --host 0.0.0.0 --port 8000     # needs a built DB
uv run geocoder serve --reload                       # dev auto-reload
```

OpenAPI docs at `http://localhost:8000/docs`.

```bash
curl "http://localhost:8000/api/v1/geocode?q=Hotel%20Nacional"
curl "http://localhost:8000/api/v1/geocode?q=23%20y%2012"
curl "http://localhost:8000/api/v1/geocode?q=municipio:plaza%20Calle%2023"
curl "http://localhost:8000/api/v1/reverse?lat=23.1432&lon=-82.3807&radius=200"
curl "http://localhost:8000/api/v1/health"
curl "http://localhost:8000/api/v1/municipios"
curl "http://localhost:8000/api/v1/stats"
```

## Address resolution (`/resolve`, `geocoder resolve`)

The **unified resolver** takes any location query — a Cuban street address, a
POI/named place, or an administrative area — and returns the single best point
with a **precision** label and an honest **± error radius in meters** derived
from the geometry it used. More info → a tighter pin.

```bash
geocoder resolve "Calle L e/ 23 y 25, Vedado"   # segment between two cross-streets
geocoder resolve "23 esq. a L, Vedado"          # corner
geocoder resolve "Calle 23 #456 e/ L y M"       # exact portal
geocoder resolve "Hotel Nacional"               # POI
geocoder resolve "Vedado"                       # barrio area
curl "http://localhost:8000/api/v1/resolve?q=Calle%20L%20e/%2023%20y%2025,%20Vedado"
```

Grammar understood: `Calle X`, house `#N` / `No. N`, *entre* (`e/` / `entre` / `/`)
two cross-streets, *esquina* (`esq.` / `esquina` / `A y B`), and trailing
`, Barrio` / `, Municipio` context (which disambiguates repeated names like
`Coppelia`). Precision ladder, most→least precise: `exact_portal` (~15 m) ›
`interpolated_entre` / `segment_midpoint` › `esquina` (~20 m) › `poi` (~25 m or
footprint) › `street` › `barrio` › `municipio` (all geometry-sized).

## Query syntax (`/geocode`, `geocoder search` — multi-candidate FTS)

| Pattern | Example | Description |
|---|---|---|
| `street number` | `Calle 23 415` | Exact portal lookup |
| `A y B` | `23 y 12` | Geometric intersection |
| `name` | `Hotel Nacional` | POI / place search |
| `municipio:X` | `municipio:plaza` | Filter by municipality (resolved to canonical name) |
| `municipio:"X Y"` | `municipio:"Plaza de la Revolución"` | Quoted multi-word filter |
| `barrio:Y` | `barrio:Vedado` | Filter by neighborhood |
| `tipo:Z` | `tipo:portal` | Filter by type (`calle`/`portal`/`poi`) |

## Storage layout (beaver)

| beaver object | kind | holds |
|---|---|---|
| `lugares` | `db.docs` (FTS) | one document per street / portal / POI |
| `portales` | `db.dict` | exact portal lookup, key `street\|numero\|municipio` |
| `calles_geom` | `db.dict` | street `LineString` WKT (kept out of the FTS doc) |
| `municipios` / `provincias` / `barrios` | `db.dict` | boundary polygons + centroids |
| `meta` | `db.dict` | build metadata + counts |

Details: `know-how/beaver-data-model.md`.

## OSM admin levels (Cuba)

Cuba tags administrative boundaries as **país=2, provincia=4, municipio=6**.
La Habana's 15 municipios are `admin_level=6` (not 8). Boundaries are assembled
from member ways with `linemerge`+`polygonize` and parsed **unclipped** so coastal
municipios (whose boundary follows `natural=coastline` ways) close correctly.
Configured in `config.ADMIN_LEVEL_PROVINCE` / `ADMIN_LEVEL_MUNICIPIO`.

## Project layout

```
src/geocoder/
├── cli/main.py     # typer CLI: download, build, search, serve, stats, bench
├── etl/            # download, admin/places/entity parsers, enrich (PIP), build
├── store.py        # GeoStore — the beaver-backed persistence unit
├── search/         # parser, fts, fuzzy (rapidfuzz), portal, intersection, pipeline
├── api/            # FastAPI app, routes, schemas, DI
├── models/         # dataclass models; utils/normalize.py
tests/              # unit, ETL (.osm fixture), API integration, benchmarks (opt-in)
```

## Dependencies

`osmium` (PBF parsing), `shapely` (geometry), `rapidfuzz` (fuzzy),
`beaver-db` (storage), `fastapi`+`uvicorn` (API), `typer` (CLI).

## beaver dependency

The geocoder uses beaver's sync filtered search (`docs.search(where=…)`), which is
on beaver `main` but not yet in a published `beaver-db` release. Local dev uses the
editable `../beaver`; a beaver release + dependency-floor bump is needed before
Docker/PyPI installs work standalone.

## Testing

```bash
uv run pytest                       # unit + ETL-fixture + API integration (green)
uv run pytest tests/benchmarks      # opt-in; needs a built DB + pytest-benchmark
uv run ruff check src tests
```

ETL tests run against a hand-written `.osm` fixture (osmium reads XML — no PBF, no
network). See `know-how/running-and-testing.md`.

## Docker

`Dockerfile` + `docker-compose.yml` are provided (health check hits `/health`,
`GEOCODER_DB_PATH` honored). Note: a working image needs a beaver release first
(see above).

## License

MIT
