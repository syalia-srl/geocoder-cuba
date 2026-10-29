#!/usr/bin/env bash
# Build the airgap bundle. Run on a host WITH internet (pip needs it once).
#
#   deploy/build-airgapped.sh [--rebuild] [--allow-stale-data]
#
# Flags pass through to bake-data.sh. Produces geocoder-docker.tar.gz at the
# repo root; hand that plus deploy/install.sh and deploy/docker-compose.yml.tmpl
# to the target.
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root

TAG="${TAG:-$(git rev-parse --short HEAD 2>/dev/null || echo latest)}"
OUT="geocoder-docker.tar.gz"

# 1. Shared payload stage — provenance, WAL checkpoint, asserts. Its own step so
#    a refusal here stops the build before the expensive parts.
deploy/bake-data.sh "$@"

# 2. Image. The 575 MB payload makes this slow to send to the daemon; the
#    .dockerignore keeps the other databases and the PBF out of the context.
echo "→ docker build (payload is ~575 MB, be patient)…"
docker build --network=host -t "geocoder:$TAG" -t geocoder:latest -f deploy/Dockerfile .

# 3. Ship both tags so install.sh can reference either.
echo "→ docker save + gzip…"
docker save "geocoder:$TAG" geocoder:latest | gzip > "$OUT"

echo
echo "✅ $OUT  ($(du -h "$OUT" | cut -f1))"
sha256sum "$OUT"
echo
echo "Deliver these three files together:"
echo "   $OUT"
echo "   deploy/install.sh"
echo "   deploy/docker-compose.yml.tmpl"
echo
echo "Verify before shipping:  deploy/airgap-smoke.sh"
