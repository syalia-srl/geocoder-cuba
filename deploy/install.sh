#!/usr/bin/env bash
# Instala el geocodificador de Cuba en un Linux sin internet.
#
#   ./install.sh                 # interactivo (pregunta el puerto)
#   ./install.sh --port 8080     # desatendido
#
# Necesita: docker + docker compose v2. Nada más. No descarga nada.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

PORT=""
TARBALL="${GEOCODER_IMAGES_TAR:-}"

while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2;;
    --tarball) TARBALL="$2"; shift 2;;
    -h|--help) sed -n '2,8p' "$0"; exit 0;;
    *) echo "opción desconocida: $1" >&2; exit 2;;
  esac
done

# 1. prerrequisitos
docker version >/dev/null 2>&1 || { echo "❌ Falta docker (o el usuario no está en el grupo 'docker')."; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "❌ Falta 'docker compose' v2."; exit 1; }

# 2. imagen
if [ -z "$TARBALL" ]; then
  TARBALL="$(ls -1 "$HERE"/geocoder-docker.tar.gz "$HERE"/geocoder-*.tar.gz 2>/dev/null | head -1 || true)"
fi
[ -n "$TARBALL" ] && [ -f "$TARBALL" ] || { echo "❌ No encuentro el .tar.gz de la imagen junto a install.sh."; exit 1; }

echo "→ Cargando la imagen (docker load)… puede tardar, son cientos de MB."
docker load < "$TARBALL"

# 3. puerto
if [ -z "$PORT" ]; then
  if [ -t 0 ]; then
    read -rp "Puerto donde publicar el geocodificador [8080]: " PORT
  fi
  PORT="${PORT:-8080}"
fi

# 4. .env + compose
TAG="$(docker images --format '{{.Tag}}' geocoder | grep -v '^latest$' | head -1 || true)"
TAG="${TAG:-latest}"
cat > "$HERE/.env" <<EOF
PORT=${PORT}
TAG=${TAG}
EOF
[ -f "$HERE/docker-compose.yml" ] || cp "$HERE/docker-compose.yml.tmpl" "$HERE/docker-compose.yml"

# 5. arrancar
#    --no-build es obligatorio: si una etiqueta no coincide, compose intenta
#    CONSTRUIR la imagen, lo que necesita internet y se queda colgado en una
#    máquina aislada. Así falla de inmediato y con un mensaje claro.
( cd "$HERE" && docker compose up -d --no-build )

# 6. esperar a que caliente los índices
#    El primer arranque tarda ~90 s con los datos de toda Cuba: carga el grafo de
#    rutas (352 mil nodos) y construye el índice espacial. Sin este aviso el
#    operador ve "desplegado", prueba, no responde, y concluye que está roto.
echo
echo -n "→ Preparando los índices (primer arranque, ~90 s con toda Cuba)"
READY=0
for _ in $(seq 1 240); do
  if curl -fsS "http://localhost:${PORT}/health" >/dev/null 2>&1; then READY=1; break; fi
  echo -n "."
  sleep 1
done
echo
if [ "$READY" -eq 0 ]; then
  echo "⚠ Sigue arrancando tras 4 minutos. Revisa:  (cd ${HERE} && docker compose logs -f)"
else
  echo "   listo."
fi

echo
echo "✅ Geocodificador desplegado en  http://localhost:${PORT}"
echo "   API:    http://localhost:${PORT}/api/v1/resolve?q=Calle+23+e/+L+y+M,+Vedado"
echo "   Docs:   http://localhost:${PORT}/docs"
echo "   Logs:   (cd ${HERE} && docker compose logs -f)"
echo "   Parar:  (cd ${HERE} && docker compose down)"
echo
echo "No guarda estado: para actualizar, copia un .tar.gz nuevo y repite ./install.sh"
