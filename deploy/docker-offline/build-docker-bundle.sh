#!/usr/bin/env bash
# build-docker-bundle.sh — empaqueta Docker para instalarlo sin red.
#
#   bash deploy/docker-offline/build-docker-bundle.sh                # versiones por defecto
#   DOCKER_VERSION=29.7.1 COMPOSE_VERSION=v5.3.1 bash release/build-docker-bundle.sh
#
# Adapted from a sibling project's offline bundle script.
#
# Produce ./docker-offline-<ver>.tar.gz con:
#   docker-<ver>.tgz     binarios estáticos oficiales (dockerd, containerd, runc…)
#   docker-compose       el plugin, binario suelto
#   SHA256SUMS           lo que install-docker.sh comprueba antes de instalar
#
# Binarios estáticos y no .deb/.rpm a propósito: los paquetes son específicos de
# distro y versión, así que un bundle serviría para noble y fallaría en bookworm —
# exactamente cuando ya no hay red. Esto es un artefacto que corre en cualquier
# Linux x86_64.
set -euo pipefail
cd "$(dirname "$0")/.."

DOCKER_VERSION="${DOCKER_VERSION:-29.7.1}"
COMPOSE_VERSION="${COMPOSE_VERSION:-v5.3.1}"
OUT="${DOCKER_BUNDLE_OUT:-$PWD/docker-offline-$DOCKER_VERSION.tar.gz}"
STATIC_BASE="https://download.docker.com/linux/static/stable/x86_64"
COMPOSE_BASE="https://github.com/docker/compose/releases/download"

say() { printf '\n\033[1m❯ %s\033[0m\n' "$1"; }

work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT

say "Descargando Docker $DOCKER_VERSION (binarios estáticos)"
curl -fL --http1.1 --retry 5 --retry-delay 3 -C - \
  "$STATIC_BASE/docker-$DOCKER_VERSION.tgz" -o "$work/docker-$DOCKER_VERSION.tgz"

say "Descargando el plugin compose $COMPOSE_VERSION"
curl -fL --http1.1 --retry 5 --retry-delay 3 \
  "$COMPOSE_BASE/$COMPOSE_VERSION/docker-compose-linux-x86_64" -o "$work/docker-compose"
chmod +x "$work/docker-compose"

# Comprobar que lo descargado es lo que decimos que es, ANTES de empaquetarlo: un
# bundle con un tarball truncado dentro se descubriría en la máquina sin red.
say "Comprobando el contenido"
# El listado se guarda UNA vez y se busca en el fichero, no por tubería.
#
# `tar -tzf … | grep -q` bajo `set -o pipefail` es una trampa: grep -q sale en
# cuanto encuentra, tar recibe SIGPIPE y muere con 141, y pipefail propaga ese 141
# — así que una coincidencia CORRECTA se lee como fallo. Costó un "el tarball no
# trae dockerd" sobre un tarball que sí lo traía. De paso, esto pasa por los 85 MB
# comprimidos una vez en lugar de siete.
tar -tzf "$work/docker-$DOCKER_VERSION.tgz" > "$work/listing.txt"
for b in dockerd containerd runc docker containerd-shim-runc-v2 docker-init docker-proxy; do
  grep -qx "docker/$b" "$work/listing.txt" \
    || { echo "falta docker/$b en el tarball de Docker" >&2; exit 1; }
done
"$work/docker-compose" version >/dev/null 2>&1 \
  || { echo "el binario de compose no ejecuta" >&2; exit 1; }
echo "  ✓ dockerd, containerd, runc y el resto están; compose ejecuta"

( cd "$work" && sha256sum "docker-$DOCKER_VERSION.tgz" docker-compose > SHA256SUMS )

say "Empaquetando"
tar czf "$OUT" -C "$work" "docker-$DOCKER_VERSION.tgz" docker-compose SHA256SUMS
sha256sum "$OUT" | awk '{print $1}' > "$OUT.sha256"

printf '\n  %s  (%s MB)\n  sha256: %s\n\n' \
  "$OUT" "$(( $(stat -c%s "$OUT") / 1024 / 1024 ))" "$(cat "$OUT.sha256")"
