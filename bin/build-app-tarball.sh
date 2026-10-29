#!/usr/bin/env bash
# Build a baked, relocatable offline tarball of the geocoder for an air-gapped VM.
#
# The SECONDARY airgap producer — for targets with no container engine. The
# primary is deploy/build-airgapped.sh (a Docker bundle). Both consume the same
# payload from deploy/bake-data.sh, so the tarball cannot ship staler data than
# the image; flags (--rebuild, --allow-stale-data) pass straight through.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATE="$(date +%Y-%m-%d)"
OUT="geocoder-${DATE}.tar.gz"

# Shared payload stage first: provenance check, WAL checkpoint, size asserts.
"$REPO_ROOT/deploy/bake-data.sh" "$@"
# --network=host: a laptop's /etc/resolv.conf points at the systemd-resolved stub
# (127.0.0.53), which docker strips; the default bridge then has no reachable
# DNS on this network. Host networking gives the build a working resolver.
docker build --network=host --build-arg APT_MIRROR="${APT_MIRROR:-}" \
  -f "$REPO_ROOT/docker/build-app.Dockerfile" -t geocoder-build:24.04 "$REPO_ROOT"
mkdir -p "$REPO_ROOT/dist"
docker run --rm --network=host \
  -v "$REPO_ROOT:/src:ro" \
  -v "$REPO_ROOT/dist:/dist" \
  geocoder-build:24.04 bash /src/bin/_build-geocoder-inside.sh "$OUT"
echo "==> repos/geocoder/dist/$OUT"
