#!/usr/bin/env bash
# The gate. Proves the bundle is self-contained BEFORE it goes on a USB drive.
#
# Run on the build host after deploy/build-airgapped.sh. Three stages:
#   1. baked assets are inside the image      (--network none)
#   2. a scratch install actually answers     (HTTP asserts)
#   3. nothing was fetched at runtime         (log grep)
#
# Stage 3 is the only one that proves airgap-completeness — stages 1-2 can pass
# on a build host where a missing thing is still sitting in a cache.
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root

IMG="${IMG:-geocoder:latest}"
TARBALL="${TARBALL:-$PWD/geocoder-docker.tar.gz}"
PORT="${SMOKE_PORT:-8897}"
WORK="$(mktemp -d)"
die() { echo "❌ $*" >&2; exit 1; }
cleanup() {
  [ -f "$WORK/docker-compose.yml" ] && ( cd "$WORK" && docker compose down -v >/dev/null 2>&1 ) || true
  docker rm -f geocoder-smoke >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

echo "→ 1/3  Baked assets are INSIDE the image (--network none)…"
docker run --rm --network none --entrypoint python "$IMG" -c "
import os, sys
from pathlib import Path
from geocoder.store import GeoStore
from geocoder.config import SETTINGS

db = Path(os.environ['GEOCODER_DB_PATH'])
assert db.exists(), 'baked db missing'
assert db.stat().st_size > 400_000_000, f'db too small: {db.stat().st_size}'

tiles = SETTINGS.DATA_DIR / 'tiles' / 'cuba.pmtiles'
assert tiles.exists(), f'tiles missing at {tiles} (GEOCODER_DATA_DIR wrong?)'
assert tiles.stat().st_size > 50_000_000, 'tiles too small'

s = GeoStore(str(db))
lug = s.stats().get('lugares', 0)
gn, cg = len(s.graph_nodes), len(s.calles_geom)
s.close()
assert lug > 150_000, f'lugares {lug}'
assert gn > 300_000, f'graph_nodes {gn}'
assert cg > 20_000, f'calles_geom {cg}'

# The map labels need the vendored SDF glyphs; a missing range means a blank map.
import geocoder.api as api
g = Path(api.__file__).parent / 'static' / 'vendor' / 'glyphs' / 'Inter Medium' / '0-255.pbf'
assert g.exists(), f'glyph range missing at {g}'
print(f'   ok: {lug:,} lugares, {gn:,} graph nodes, {cg:,} streets, tiles+glyphs present')
" || die "baked assets missing or too small"

echo "→ 2/3  Scratch install answers over HTTP…"
cp deploy/install.sh deploy/docker-compose.yml.tmpl "$WORK/"
( cd "$WORK" && ./install.sh --port "$PORT" --tarball "$TARBALL" >/dev/null ) || die "install.sh failed"

# /health answers in seconds now that the routing graph warms on a background
# thread. The generous budget stays: the first /route below may still block on
# that warm (~70 s on full Cuba), and a slower box makes both longer.
echo -n "   waiting for startup"
for _ in $(seq 1 240); do
  curl -fsS "http://localhost:$PORT/health" >/dev/null 2>&1 && break
  echo -n "."
  sleep 1
done
echo
curl -fsS "http://localhost:$PORT/health" >/dev/null || die "/health never came up (4 min)"

_resolve() { curl -fsS --get "http://localhost:$PORT/api/v1/resolve" --data-urlencode "q=$1"; }

# a known-good urban address must be precise and land in Havana
_resolve "Calle 23 e/ L y M, Vedado" | python3 -c '
import sys, json
r = json.load(sys.stdin)["results"][0]
assert r["precision"] == "segment_midpoint", r["precision"]
c = r["coordinates"]          # lat/lon are nested, not top-level
assert 22.9 < c["lat"] < 23.3 and -82.6 < c["lon"] < -82.0, (c["lat"], c["lon"])
print("   resolve: %s (%.5f,%.5f)" % (r["precision"], c["lat"], c["lon"]))
' || die "urban resolve wrong"

# v0.4.0 regression guard: a failed address must NOT come back as a POI
_resolve "ave las banderas e/ 7 y 9, Habana del Este" | python3 -c '
import sys, json
rs = json.load(sys.stdin)["results"]
if rs:
    assert rs[0]["precision"] != "poi", "attractor regression: failed address returned a POI"
    print("   attractor guard: %s" % rs[0]["precision"])
else:
    print("   attractor guard: empty (also correct)")
' || die "POI attractor regression"

curl -fsS -X POST "http://localhost:$PORT/api/v1/route" -H 'content-type: application/json' \
  -d '{"from":"Hotel Nacional","to":"El Capitolio, Habana Vieja"}' | python3 -c '
import sys, json
d = json.load(sys.stdin)
km = d["distance_m"] / 1000
assert 1 < km < 10, f"route {km} km"
print(f"   route: {km:.2f} km")
' || die "route failed"

curl -fsS -X POST "http://localhost:$PORT/api/v1/region" -H 'content-type: application/json' \
  -d '{"streets":["23","21","L","M"]}' | python3 -c '
import sys, json
d = json.load(sys.stdin)
assert d.get("polygon_wkt","").startswith("POLYGON"), "no polygon"
print("   region: %.1f ha" % d["area_ha"])
' || die "region failed"

# The offline basemap: Range must yield 206, or pmtiles.js cannot read the archive.
code=$(curl -s -o /dev/null -w '%{http_code}' -H 'Range: bytes=0-99' "http://localhost:$PORT/tiles/cuba.pmtiles")
[ "$code" = "206" ] || die "/tiles Range returned $code, expected 206"
echo "   tiles: Range 206"

code=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PORT/static/vendor/glyphs/Inter%20Medium/0-255.pbf")
[ "$code" = "200" ] || die "glyph range returned $code"
echo "   glyphs: 200"

# Lote mode: the batch endpoint answers every row, including one whose quotes
# used to 500 the whole batch, and the UI ships the script that drives it.
curl -fsS -X POST "http://localhost:$PORT/api/v1/resolve/batch" -H 'content-type: application/json' \
  -d '{"queries":["Hotel Nacional","Tienda \"La Concepción\", Palma Soriano","23 esq. a L, Vedado"]}' | python3 -c '
import sys, json
d = json.load(sys.stdin)
assert d["count"] == 3 and all(r.get("error") is None for r in d["results"]), d
assert sum(1 for r in d["results"] if r["results"]) >= 2, "batch resolved too few"
print("   batch: 3 rows, no errors")
' || die "batch failed"

curl -fsS "http://localhost:$PORT/" | grep -qi "geocodificador" || die "UI did not render"
code=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PORT/static/lote.js")
[ "$code" = "200" ] || die "lote.js returned $code"
echo "   UI: ok (lote.js 200)"

echo "→ 3/3  Nothing was fetched at runtime…"
( cd "$WORK" && docker compose logs > "$WORK/log" 2>&1 ) || true
if grep -qiE 'pip install|apt-get (update|install)|Downloading |docker pull|Resolving deps' "$WORK/log"; then
  grep -iE 'pip install|apt-get|Downloading |docker pull' "$WORK/log" | head -5 >&2
  die "the container fetched something at runtime — the bundle is NOT airgap-complete"
fi
echo "   clean: no fetch attempts in the container log"

echo
echo "✅ airgap smoke passed — bundle is self-contained"
