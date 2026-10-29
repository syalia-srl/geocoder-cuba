# Running & testing

*Reach for this when running the API, running the test suite, or smoke-testing the
README curl set against a real Havana build.*

## Setup (uv, never bare pip; Python ≥ 3.12)

```bash
uv venv --python 3.12
uv pip install -e ".[api,dev]"
uv pip install -e ../beaver     # dev: track local beaver (its sync search(where=)
                                # isn't in a published release yet — do NOT `uv add`
                                # it as a path dep; the pyproject pin stays beaver-db>=2.0)
```

## Run the API

```bash
uv run geocoder serve --host 0.0.0.0 --port 8000   # needs a built DB (see building-the-database.md)
uv run geocoder serve --reload                     # dev auto-reload
```

Interactive OpenAPI at `http://localhost:8000/docs`.

## Tests

```bash
uv run pytest                        # unit + ETL-fixture + API integration (green, no DB needed)
uv run pytest tests/benchmarks       # opt-in: needs a built DB + pytest-benchmark
uv run ruff check src tests          # lint as its own step, never piped
```

- **Unit** (`test_search`, `test_store`, `test_pipeline`, `test_enrich`) need no
  prebuilt DB — where data is needed, they build a small store/fixture in a tmp path.
- **ETL** (`test_etl`) parses `tests/fixtures/habana_sample.osm` (osmium reads XML —
  no binary PBF, no network): a municipio boundary, a barrio polygon, a named
  street, a portal node, a POI. Asserts municipio + barrio enrichment.
- **API** (`test_api`) builds a beaver DB from that fixture, overrides the FastAPI
  dependencies, and asserts real response shapes/values (incl. reverse ordering).
- **Resolver** (`test_resolver`, `test_address_parser`) cover the address parser
  and the resolution ladder on small in-memory stores (no DB).
- **Corpus** (`test_corpus`) runs the hard Cuban-address golden set
  (`tests/fixtures/cuban_addresses.json`) against the **real** `data/cuba_geo.db`;
  it skips cleanly when the DB isn't built, so the default suite stays green on a
  fresh checkout.
- **Benchmarks** (`tests/benchmarks/`) are excluded from the default run via
  `addopts = "--ignore=tests/benchmarks"` — they need a real built DB and
  `pytest-benchmark`. Run explicitly with `uv run pytest tests/benchmarks`.

## Smoke test against a real Havana build

After `geocoder build --bbox habana` and `geocoder serve`, these must return
**real coordinates with correct municipio + barrio**, not just HTTP 200:

```bash
curl "http://localhost:8000/api/v1/geocode?q=Hotel%20Nacional"
#   → Plaza de la Revolución, La Habana, Cayo Hueso  (23.1432, -82.3807)
curl "http://localhost:8000/api/v1/geocode?q=23%20y%2012"                   # intersection
curl "http://localhost:8000/api/v1/geocode?q=municipio:plaza%20Calle%2023" # filter → Plaza de la Revolución
curl "http://localhost:8000/api/v1/reverse?lat=23.1432&lon=-82.3807&radius=200"
curl "http://localhost:8000/api/v1/health"      # {"status":"healthy","database":"connected"}
curl "http://localhost:8000/api/v1/municipios"  # 15 Havana municipios among them
curl "http://localhost:8000/api/v1/stats"       # ~64.9k lugares, 167 municipios
```

### Resolver smoke (`geocoder resolve` / `/api/v1/resolve`)

Each must return a sensible point with the expected `precision` + `±radius_m`:

```bash
geocoder resolve "Calle L e/ 23 y 25, Vedado"   # → segment_midpoint ±~60 m
geocoder resolve "23 esq. a L, Vedado"          # → esquina ±20 m
geocoder resolve "Calle 23 #456 e/ L y M"       # → exact_portal ±15 m
geocoder resolve "Hotel Nacional"               # → poi ±25 m
geocoder resolve "Coppelia, Vedado"             # → poi (the Vedado one, not La Lisa)
geocoder resolve "Vedado"                       # → barrio ±800 m
```

If `entre`/`esquina` degrade to `street`, the street's fragments didn't cleanly
intersect (or weren't scoped to the right municipio) — check `GeoStore.street_geoms`
and the barrio-centroid `ref` scoping in `resolver.py`. A rebuild is required after
any ETL change (merged street geometry, barrio centroids).
