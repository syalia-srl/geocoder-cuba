#!/usr/bin/env bash
# Prepare the baked payload both airgap producers ship.
#
# This is a VERIFIER, not a rebuilder. A full-Cuba ETL is ~2 h, so unlike
# smaller projects we cannot re-run it on every package. Instead we check that
# the database was built by the code we are about to ship alongside it, and
# refuse otherwise: the ETL and the query path share normalize_street_name, so
# a one-line change there silently invalidates every key already written.
#
#   deploy/bake-data.sh [--rebuild] [--allow-stale-data]
#
# Leaves data/cuba_routing_full.db checkpointed and asserted, and prints a
# one-line summary the producers echo into their own output.
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root

DB="${GEOCODER_DB_PATH:-data/cuba_routing_full.db}"
TILES="data/tiles/cuba.pmtiles"
PBF="${GEOCODER_PBF:-data/cuba-latest.osm.pbf}"
REBUILD=0
ALLOW_STALE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --rebuild) REBUILD=1; shift;;
    --allow-stale-data) ALLOW_STALE=1; shift;;
    *) echo "unknown flag: $1" >&2; exit 2;;
  esac
done

if [ "$REBUILD" -eq 1 ]; then
  echo "→ Full-Cuba ETL + routing graph (this takes ~2 h)…"
  [ -f "$PBF" ] || { echo "❌ missing $PBF — run 'geocoder download' first" >&2; exit 1; }
  GEOCODER_DB_PATH="$DB" uv run geocoder build --full-cuba --force
  echo "→ Vector tiles…"
  bin/build-tiles "$PBF"
fi

[ -f "$DB" ]    || { echo "❌ missing $DB (use --rebuild)" >&2; exit 1; }
[ -f "$TILES" ] || { echo "❌ missing $TILES (use --rebuild, or run bin/build-tiles)" >&2; exit 1; }

# ---- provenance --------------------------------------------------------------
HEAD_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
STAMP_SHA="$(GEOCODER_DB_PATH="$DB" uv run geocoder stamp 2>/dev/null \
             | python3 -c 'import json,sys; print(json.load(sys.stdin)["commit"])' 2>/dev/null || echo "")"

if [ "$STAMP_SHA" = "$HEAD_SHA" ]; then
  echo "✓ build stamp matches HEAD ($HEAD_SHA)"
else
  echo "⚠ build stamp mismatch"
  echo "    database built by : ${STAMP_SHA:-<unstamped>}"
  echo "    shipping code     : $HEAD_SHA"

  # What the guard actually protects is the store's key space: the ETL and the
  # query path share normalize_street_name, so a store written by different
  # PYTHON can carry keys this code will never look up. Commits that touch only
  # packaging (release scripts, launcher Makefiles, docs) cannot do that. So
  # instead of demanding an exact SHA, ask whether anything under src/ moved
  # between the stamp and HEAD — that is the invariant, stated directly.
  CODE_DELTA="unknown"
  if [ -n "$STAMP_SHA" ] && git cat-file -e "${STAMP_SHA}^{commit}" 2>/dev/null; then
    CODE_DELTA="$(git diff --name-only "$STAMP_SHA..HEAD" -- src/ 2>/dev/null || echo unknown)"
  fi

  if [ -z "$CODE_DELTA" ]; then
    echo "✓ nothing under src/ changed between $STAMP_SHA and $HEAD_SHA"
    echo "  (packaging-only delta — the store's key space is unaffected)"
    git log --oneline "$STAMP_SHA..HEAD" | sed 's/^/    /'
  elif [ "$ALLOW_STALE" -eq 0 ]; then
    if [ "$CODE_DELTA" = "unknown" ]; then
      echo "  cannot diff: ${STAMP_SHA:-<unstamped>} is not a commit in this repo" >&2
    else
      echo "  src/ files changed since the stamp:" >&2
      echo "$CODE_DELTA" | sed 's/^/    /' >&2
    fi
    cat >&2 <<EOF
❌ Refusing to bake. The ETL and the query path share normalize_street_name, so
   a store written by different code can carry keys this code will never look
   up — a silent wrong-answer bug, not a crash.

   Rebuild:   deploy/bake-data.sh --rebuild        (~2 h, full Cuba)
   Override:  deploy/bake-data.sh --allow-stale-data   (pipeline testing ONLY —
              the resulting bundle is an artifact, not a deliverable)
EOF
    exit 1
  else
    echo "  …proceeding anyway (--allow-stale-data). NOT shippable."
  fi
fi

# ---- checkpoint + assert -----------------------------------------------------
# beaver runs SQLite in WAL mode, so freshly written data can live entirely in
# <db>-wal. Baking only <db> then ships an empty database — a sibling project shipped
# exactly that once. The asserts matter more than the checkpoint: a checkpoint
# that silently no-ops looks identical to one that worked.
echo "→ Checkpointing WAL and asserting the payload…"
GEOCODER_DB_PATH="$DB" TILES_PATH="$TILES" uv run python - <<'PY'
import os, sqlite3, sys
from pathlib import Path
from geocoder.store import GeoStore

db = Path(os.environ["GEOCODER_DB_PATH"])
tiles = Path(os.environ["TILES_PATH"])

sqlite3.connect(str(db)).execute("PRAGMA wal_checkpoint(TRUNCATE)")

size = db.stat().st_size
tsize = tiles.stat().st_size
s = GeoStore(str(db))
counts = {
    "lugares": s.stats().get("lugares", 0),
    "graph_nodes": len(s.graph_nodes),
    "calles_geom": len(s.calles_geom),
    "intersections": len(s.intersections),
}
s.close()

checks = [
    ("db size",      size,               400_000_000),
    ("tiles size",   tsize,               50_000_000),
    ("lugares",      counts["lugares"],      150_000),
    ("graph_nodes",  counts["graph_nodes"],  300_000),
    ("calles_geom",  counts["calles_geom"],   20_000),
]
bad = [(n, got, want) for n, got, want in checks if got < want]
for n, got, want in checks:
    print(f"   {n:12} = {got:>12,}  (min {want:,})")
if bad:
    for n, got, want in bad:
        print(f"❌ {n} too small: {got:,} < {want:,}", file=sys.stderr)
    sys.exit("❌ payload failed its asserts — data stuck in WAL, or a partial build?")
print(f"BAKED db={size/1e6:.0f}MB tiles={tsize/1e6:.0f}MB "
      f"lugares={counts['lugares']:,} graph={counts['graph_nodes']:,} "
      f"streets={counts['calles_geom']:,} crossings={counts['intersections']:,}")
PY

echo "✅ payload ready: $DB + $TILES"
