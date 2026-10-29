#!/usr/bin/env bash
# Runs INSIDE geocoder-build:24.04. /src = repo (ro), /dist = output.
#
# The no-engine producer: a relocatable venv + the baked payload, for targets
# without Docker (an air-gapped VM). The payload was already checkpointed and
# asserted by deploy/bake-data.sh on the host — this only packages it.
set -euo pipefail
OUT="$1"
STAGE=/build/geocoder
mkdir -p "$STAGE/data/tiles"

# 1. Relocatable venv + install the package with the API extra (internet here).
#    Install from a WRITABLE copy of just the package (src/ + pyproject + README):
#    /src is mounted read-only, and setuptools writes egg-info into the source tree.
uv venv --relocatable --python 3.12 "$STAGE/.venv"
mkdir -p /build/pkg
cp -a /src/src /src/pyproject.toml /build/pkg/
[ -f /src/README.md ] && cp /src/README.md /build/pkg/ || true
( cd /build/pkg && uv pip install --python "$STAGE/.venv/bin/python" ".[api]" )

# 2. The baked payload — full Cuba, WITH the routing graph, plus the offline
#    basemap. (Earlier this shipped cuba_geo.db: 70 MB, no routing, no tiles, so
#    `geocoder route` and the map both failed on the VM.) Already WAL-checkpointed
#    by deploy/bake-data.sh; copying a -wal sidecar alongside would shadow it.
cp /src/data/cuba_routing_full.db "$STAGE/data/cuba_routing_full.db"
cp /src/data/tiles/cuba.pmtiles   "$STAGE/data/tiles/cuba.pmtiles"

# 3. Launcher + plantilla de env. Se instala como `env.example`, NUNCA como
#    `.env`: este tarball viaja dentro del paquete de otra aplicación, que se
#    actualiza extrayendo encima de la instalación existente — un `.env` aquí le
#    pisaría la configuración que el operador ya editó.
cp /src/deploy/tarball/Makefile.serve "$STAGE/Makefile"
cp /src/deploy/tarball/env.example "$STAGE/env.example"

# 4. Tarball.
tar czf "/dist/$OUT" -C /build geocoder
