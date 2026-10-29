#!/usr/bin/env bash
# Gate for the no-engine tarball (bin/build-app-tarball.sh).
#
#   bin/tarball-smoke.sh [dist/geocoder-YYYY-MM-DD.tar.gz]
#
# WEAKER THAN deploy/airgap-smoke.sh, and knowingly so. There is no container to
# isolate, so "nothing was fetched" cannot be proven by running without a
# network the way the Docker gate does. Where `unshare -rn` is available we drop
# the network for the run, which is close; otherwise we can only assert the
# payload is present and the service answers. Treat a pass here as "it works",
# not as "it is provably self-contained".
#
# THE TARBALL NEEDS PYTHON 3.12 ON THE TARGET. `uv venv --relocatable` makes the
# paths inside the venv relative, but .venv/bin/python stays a symlink to
# /usr/bin/python3.12 — the interpreter is NOT bundled. Ubuntu 24.04 ships 3.12,
# which is why this works on the air-gapped VM; a box with 3.11 or 3.13 cannot run
# it. That is the price of the no-engine path, and the reason Docker is primary.
#
# Consequently this gate runs INSIDE geocoder-build:24.04 by default, since the
# build host may not have 3.12 (not every laptop does). SMOKE_LOCAL=1 forces host-local.
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root

if [ -z "${SMOKE_LOCAL:-}" ] && [ ! -x /usr/bin/python3.12 ]; then
  TB="${1:-$(ls -1t dist/geocoder-*.tar.gz 2>/dev/null | head -1 || true)}"
  [ -n "$TB" ] || { echo "❌ no tarball (build it first)" >&2; exit 1; }
  echo "→ /usr/bin/python3.12 not on this host — running the gate inside geocoder-build:24.04"
  exec docker run --rm -v "$PWD:/src:ro" -v "$PWD/dist:/dist" \
    -e SMOKE_LOCAL=1 geocoder-build:24.04 \
    bash -c "cp -r /src/bin /tmp/bin && cd /tmp && mkdir -p dist \
             && cp /dist/$(basename "$TB") dist/ && bash /tmp/bin/tarball-smoke.sh dist/$(basename "$TB")"
fi

TARBALL="${1:-$(ls -1t dist/geocoder-*.tar.gz 2>/dev/null | head -1 || true)}"
PORT="${SMOKE_PORT:-8896}"
[ -n "$TARBALL" ] && [ -f "$TARBALL" ] || { echo "❌ no tarball (build it first)" >&2; exit 1; }
TARBALL="$(realpath "$TARBALL")"

WORK="$(mktemp -d)"
SRV=""
cleanup() { [ -n "$SRV" ] && kill "$SRV" 2>/dev/null || true; rm -rf "$WORK"; }
trap cleanup EXIT
die() { echo "❌ $*" >&2; exit 1; }

echo "→ 1/3  Unpacking…"
tar xzf "$TARBALL" -C "$WORK"
APP="$WORK/geocoder"
[ -d "$APP" ] || die "tarball did not contain geocoder/"

echo "→ 2/3  Payload is present and complete…"
"$APP/.venv/bin/python" - "$APP" <<'PY' || exit 1
import sys
from pathlib import Path
app = Path(sys.argv[1])
db = app / "data" / "cuba_routing_full.db"
tiles = app / "data" / "tiles" / "cuba.pmtiles"
assert db.exists() and db.stat().st_size > 400_000_000, f"db missing/small: {db}"
assert tiles.exists() and tiles.stat().st_size > 50_000_000, f"tiles missing/small: {tiles}"
# a stale WAL sidecar would shadow the checkpointed data
for sfx in ("-wal", "-shm"):
    assert not db.with_name(db.name + sfx).exists(), f"stale {sfx} shipped alongside the db"
# the vendored map assets must have survived pip install into the venv
import geocoder.api as api
static = Path(api.__file__).parent / "static"
for rel in ("vendor/maplibre/maplibre-gl.js", "vendor/pmtiles/pmtiles.js",
            "vendor/glyphs/Inter Medium/0-255.pbf", "vendor/fonts/fonts.css"):
    assert (static / rel).exists(), f"missing vendored asset: {rel}"
print(f"   ok: db {db.stat().st_size/1e6:.0f}MB, tiles {tiles.stat().st_size/1e6:.0f}MB, vendored assets present")
PY

echo "→ 3/3  It answers (network dropped where possible)…"
RUNNER=()
if unshare -rn true 2>/dev/null; then
  RUNNER=(unshare -rn)
  echo "   (running under unshare -rn: no network at all)"
else
  echo "   ⚠ unshare unavailable — running WITH a network; this run cannot prove"
  echo "     the tarball is self-contained, only that it works."
fi

# `unshare -rn` leaves only loopback, which is all we need to curl ourselves.
( cd "$APP" && "${RUNNER[@]}" bash -c '
    set -e
    . ./.env 2>/dev/null || true
    export GEOCODER_DB_PATH GEOCODER_DATA_DIR
    exec ./.venv/bin/uvicorn geocoder.api.main:app --host 127.0.0.1 --port '"$PORT"'
  ' >"$WORK/server.log" 2>&1 ) &
SRV=$!

for _ in $(seq 1 240); do
  curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null || { tail -20 "$WORK/server.log" >&2; die "/health never came up"; }

curl -fsS --get "http://127.0.0.1:$PORT/api/v1/resolve" \
  --data-urlencode "q=Calle 23 e/ L y M, Vedado" | "$APP/.venv/bin/python" -c '
import sys, json
r = json.load(sys.stdin)["results"][0]
c = r["coordinates"]
assert r["precision"] == "segment_midpoint", r["precision"]
assert 22.9 < c["lat"] < 23.3, c["lat"]
print("   resolve: %s (%.5f,%.5f)" % (r["precision"], c["lat"], c["lon"]))
' || die "resolve wrong"

code=$(curl -s -o /dev/null -w '%{http_code}' -H 'Range: bytes=0-99' "http://127.0.0.1:$PORT/tiles/cuba.pmtiles")
[ "$code" = "206" ] || die "/tiles Range returned $code (GEOCODER_DATA_DIR wrong?)"
echo "   tiles: Range 206"

echo
if [ ${#RUNNER[@]} -gt 0 ]; then
  echo "✅ tarball smoke passed (network-isolated)"
else
  echo "✅ tarball smoke passed — but NOT network-isolated; see the warning above"
fi
