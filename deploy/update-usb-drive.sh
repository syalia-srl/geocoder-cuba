#!/usr/bin/env bash
# Copy the airgap handoff onto a USB drive.
#
#   deploy/update-usb-drive.sh                   # autodetect the single mounted USB
#   USB=/media/$USER/MIUSB deploy/update-usb-drive.sh
#
# Writes ONLY to a mounted filesystem. Never dd, never format: hand it a raw
# /dev/sdX and it aborts, because the difference between "copy files to a drive"
# and "overwrite a drive" is one careless argument.
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root

TARBALL="geocoder-docker.tar.gz"
DEST_NAME="geocoder"

if [ -n "${USB:-}" ]; then
  MOUNT="$USB"
else
  mapfile -t CANDIDATES < <(lsblk -o MOUNTPOINT,RM,TYPE -pnr \
    | awk '$2==1 && $3=="part" && $1!="" {print $1}')
  case "${#CANDIDATES[@]}" in
    0) echo "❌ No encuentro ningún USB montado. Monta el pendrive, o pasa USB=/ruta/al/montaje." >&2; exit 1;;
    1) MOUNT="${CANDIDATES[0]}";;
    *) echo "❌ Hay varios USB montados; elige uno con USB=<ruta>:" >&2
       printf '   %s\n' "${CANDIDATES[@]}" >&2; exit 1;;
  esac
fi

case "$MOUNT" in
  /dev/*)
    echo "❌ '$MOUNT' es un dispositivo crudo, no un punto de montaje." >&2
    echo "   Este script copia ficheros; nunca escribe sobre el dispositivo." >&2
    echo "   Monta el pendrive y pasa la ruta del montaje." >&2
    exit 1;;
esac
[ -d "$MOUNT" ] || { echo "❌ '$MOUNT' no es un directorio." >&2; exit 1; }
mountpoint -q "$MOUNT" 2>/dev/null || echo "⚠ '$MOUNT' no parece un punto de montaje; sigo porque es un directorio."

[ -f "$TARBALL" ] || { echo "❌ Falta $TARBALL — corre deploy/build-airgapped.sh primero." >&2; exit 1; }

DEST="$MOUNT/$DEST_NAME"
mkdir -p "$DEST"
echo "→ Copiando a $DEST … (son ~270 MB, puede tardar)"
cp -f "$TARBALL" deploy/install.sh deploy/docker-compose.yml.tmpl "$DEST/"

# The recipient's guide. Its source is deploy/usb/README.md, so the words on
# the drive are reviewed like code instead of living in a heredoc here.
cp -f deploy/usb/README.md "$DEST/README.md"
rm -f "$DEST/LEEME.txt"   # superseded by README.md on drives written earlier

# Docker for a target that has none: static binaries + their installer. Optional
# because most targets already run Docker (make usb always passes it).
if [ -n "${DOCKER_BUNDLE:-}" ]; then
  [ -f "$DOCKER_BUNDLE" ] || { echo "❌ DOCKER_BUNDLE=$DOCKER_BUNDLE no existe (make airgap-docker)." >&2; exit 1; }
  echo "→ Copiando Docker sin red ($(du -h "$DOCKER_BUNDLE" | cut -f1))…"
  cp -f "$DOCKER_BUNDLE" deploy/docker-offline/install-docker.sh "$DEST/"
fi

# A spreadsheet to try the Lote mode with, when one is given.
if [ -n "${EXAMPLE_XLSX:-}" ]; then
  cp -f "$EXAMPLE_XLSX" "$DEST/ejemplo-negocios.xlsx"
fi

sync
echo "✅ Listo:"
ls -lh "$DEST" | sed '1d;s/^/   /'
echo
echo "   Expulsa el pendrive con seguridad antes de sacarlo."
