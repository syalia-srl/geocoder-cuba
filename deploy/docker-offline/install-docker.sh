#!/usr/bin/env bash
# install-docker.sh — instala Docker en una máquina que no lo tiene, SIN RED.
#
#   bash install-docker.sh                 # usa el bundle que esté al lado
#
# Adaptado del instalador sin red de otro proyecto. El geocodificador no depende de nada más que de
# Docker, así que esto es lo único que hace falta antes de `install.sh`. Se distribuye como bundle aparte porque son 118 MB
# que la mayoría de las máquinas no necesitan.
#
# POR QUÉ BINARIOS ESTÁTICOS Y NO .deb/.rpm:
# los paquetes de Docker son específicos de distribución y versión (noble != jammy
# != bookworm), así que un kit con .deb sirve para una distro y falla en la de al
# lado — justo cuando ya no hay red para arreglarlo. Los binarios estáticos
# oficiales de Docker son un solo artefacto que corre en cualquier Linux x86_64
# con glibc. El precio es que no se integra con apt: para desinstalar hay que
# borrar los ficheros (se listan al final).
set -euo pipefail

DOCKER_VERSION="${DOCKER_VERSION:-29.7.1}"
COMPOSE_VERSION="${COMPOSE_VERSION:-v5.3.1}"
STATIC_BASE="https://download.docker.com/linux/static/stable/x86_64"
COMPOSE_BASE="https://github.com/docker/compose/releases/download"

G=$'\033[38;2;52;211;153m'; Y=$'\033[38;2;250;204;21m'; E=$'\033[38;2;248;113;113m'
B=$'\033[1m'; R=$'\033[0m'
say() { printf '\n%s❯ %s%s\n' "$B" "$1" "$R"; }
ok()  { printf '  %s✓%s %s\n' "$G" "$R" "$1"; }
warn(){ printf '  %s!%s %s\n' "$Y" "$R" "$1"; }
die() { printf '\n  %s✗%s %s\n\n' "$E" "$R" "$1" >&2; exit 1; }

# Resolved BEFORE any cd, like install.sh — the bundle sits beside this script and
# `dirname $0` re-resolved later would point somewhere else.
KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || printf '%s' "$PWD")"

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 || die "hay que correr esto como root (o instalar sudo)"
  SUDO=sudo
fi

# --- 0. arquitectura --------------------------------------------------------
case "$(uname -m)" in
  x86_64|amd64) ;;
  *) die "$(uname -m) no soportado — este bundle es x86_64. la imagen del geocodificador tampoco existe para otra arquitectura." ;;
esac

# --- 0b. iptables: requisito duro, y se comprueba ANTES de instalar nada ----
# `dockerd` NO ARRANCA sin el binario iptables. Medido, no supuesto:
#   failed to start daemon: Error initializing network controller:
#   failed to register "bridge" driver: failed to create NAT chain DOCKER:
#   iptables not found
# `nft` a secas no basta: Docker 29 lo intenta y luego exige iptables igual.
#
# No va en el bundle porque es un paquete de la distro y meterlo devolvería la
# dependencia de distro que los binarios estáticos evitan. Se comprueba aquí, al
# principio, porque avisar y seguir instalando deja la máquina con todo puesto y un
# demonio que no levanta — el fallo aparece cuatro pasos después y no dice esto.
if ! command -v iptables >/dev/null 2>&1; then
  # Con red y apt, se resuelve solo; es un paquete diminuto.
  if command -v apt-get >/dev/null 2>&1 && $SUDO apt-get install -y iptables >/dev/null 2>&1; then
    ok "iptables instalado con apt"
  else
    die "falta iptables, y dockerd no arranca sin él (no es opcional: Docker lo exige para su red).
     Instálalo desde el medio de tu distribución y vuelve a correr esto:
       Debian/Ubuntu   sudo apt install iptables
       Fedora/RHEL     sudo dnf install iptables-nft
       Alpine          sudo apk add iptables"
  fi
else
  ok "iptables presente"
fi

# --- 1. ¿ya está? -----------------------------------------------------------
# Idempotente a propósito: se puede correr dos veces sin romper nada, y si la
# máquina ya trae Docker de su distro NO lo tocamos — dos instalaciones
# compitiendo en el PATH es peor que ninguna.
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  ok "Docker ya está instalado y funcionando — $(docker --version)"
  if docker compose version >/dev/null 2>&1; then
    ok "el plugin compose también — $(docker compose version | head -1)"
    say "No hay nada que hacer. Sigue con: bash install.sh"
    exit 0
  fi
  warn "falta sólo el plugin compose — se instala eso y nada más"
  ONLY_COMPOSE=1
else
  ONLY_COMPOSE=0
fi

# --- 2. de dónde salen los bytes -------------------------------------------
# El bundle al lado manda; la red es el plan B. Al revés, una máquina con red
# lenta se pondría a descargar 118 MB teniéndolos en el USB.
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
DTGZ=""; CBIN=""

bundle="$(ls "$KIT_DIR"/docker-offline-*.tar.gz 2>/dev/null | head -1 || true)"
if [ -n "$bundle" ]; then
  say "Instalando desde el bundle local (sin red): $(basename "$bundle")"
  tar xzf "$bundle" -C "$work" || die "no se pudo desempacar $bundle"
  DTGZ="$(ls "$work"/docker-*.tgz 2>/dev/null | head -1 || true)"
  CBIN="$(ls "$work"/docker-compose 2>/dev/null | head -1 || true)"
  [ -n "$DTGZ" ] || die "el bundle no trae docker-*.tgz"
  [ -n "$CBIN" ] || die "el bundle no trae docker-compose"
  if [ -f "$work/SHA256SUMS" ]; then
    ( cd "$work" && sha256sum -c SHA256SUMS >/dev/null 2>&1 ) \
      && ok "sha256 del bundle correcto" \
      || die "el bundle está corrupto (sha256 no coincide) — vuelve a copiarlo"
  fi
else
  say "No hay bundle al lado — descargando de internet"
  command -v curl >/dev/null 2>&1 || die "no hay bundle local ni curl para descargar. Copia docker-offline-*.tar.gz junto a este script."
  curl -fL --http1.1 --retry 5 --retry-delay 3 -C - \
    "$STATIC_BASE/docker-$DOCKER_VERSION.tgz" -o "$work/docker-$DOCKER_VERSION.tgz" \
    || die "no se pudo descargar Docker $DOCKER_VERSION"
  curl -fL --http1.1 --retry 5 --retry-delay 3 \
    "$COMPOSE_BASE/$COMPOSE_VERSION/docker-compose-linux-x86_64" -o "$work/docker-compose" \
    || die "no se pudo descargar el plugin compose $COMPOSE_VERSION"
  DTGZ="$work/docker-$DOCKER_VERSION.tgz"; CBIN="$work/docker-compose"
fi

# --- 3. el plugin compose (siempre) ----------------------------------------
say "Instalando el plugin compose"
$SUDO install -d /usr/local/lib/docker/cli-plugins
$SUDO install -m 0755 "$CBIN" /usr/local/lib/docker/cli-plugins/docker-compose
ok "docker compose → /usr/local/lib/docker/cli-plugins/docker-compose"

if [ "$ONLY_COMPOSE" = 1 ]; then
  docker compose version >/dev/null 2>&1 \
    && { ok "$(docker compose version | head -1)"; say "Listo. Sigue con: bash install.sh"; exit 0; } \
    || die "el plugin quedó instalado pero 'docker compose' no responde"
fi

# --- 4. los binarios del motor ---------------------------------------------
say "Instalando el motor de Docker $DOCKER_VERSION"
tar xzf "$DTGZ" -C "$work" || die "no se pudo desempacar $DTGZ"
[ -x "$work/docker/dockerd" ] || die "el tarball no trae dockerd"
for b in "$work"/docker/*; do
  $SUDO install -m 0755 "$b" /usr/local/bin/"$(basename "$b")"
done
ok "binarios en /usr/local/bin ($(ls "$work"/docker | tr '\n' ' '))"

# --- 5. systemd -------------------------------------------------------------
# Unidades oficiales de moby/moby (contrib). Se escriben aquí en vez de
# descargarlas para no añadir otra cosa que pueda faltar sin red.
say "Registrando los servicios"
$SUDO tee /etc/systemd/system/containerd.service >/dev/null <<'UNIT'
[Unit]
Description=containerd container runtime
Documentation=https://containerd.io
After=network.target local-fs.target dbus.service

[Service]
ExecStartPre=-/sbin/modprobe overlay
ExecStart=/usr/local/bin/containerd
Type=notify
Delegate=yes
KillMode=process
Restart=always
RestartSec=5
LimitNPROC=infinity
LimitCORE=infinity
TasksMax=infinity
OOMScoreAdjust=-999

[Install]
WantedBy=multi-user.target
UNIT

$SUDO tee /etc/systemd/system/docker.socket >/dev/null <<'UNIT'
[Unit]
Description=Docker Socket for the API

[Socket]
ListenStream=/run/docker.sock
SocketMode=0660
SocketUser=root
SocketGroup=docker

[Install]
WantedBy=sockets.target
UNIT

$SUDO tee /etc/systemd/system/docker.service >/dev/null <<'UNIT'
[Unit]
Description=Docker Application Container Engine
Documentation=https://docs.docker.com
After=network-online.target docker.socket containerd.service
Wants=network-online.target
Requires=docker.socket containerd.service

[Service]
Type=notify
ExecStart=/usr/local/bin/dockerd -H fd:// --containerd=/run/containerd/containerd.sock
ExecReload=/bin/kill -s HUP $MAINPID
TimeoutStartSec=0
RestartSec=2
Restart=always
LimitNOFILE=infinity
LimitNPROC=infinity
LimitCORE=infinity
TasksMax=infinity
Delegate=yes
KillMode=process
OOMScoreAdjust=-500

[Install]
WantedBy=multi-user.target
UNIT
ok "containerd.service, docker.service, docker.socket"

# El grupo tiene que existir ANTES de docker.socket, que le asigna el socket.
getent group docker >/dev/null 2>&1 || $SUDO groupadd docker
ok "grupo 'docker'"

if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  SYSTEMD=1
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now containerd.service >/dev/null 2>&1 || true
  $SUDO systemctl enable --now docker.socket >/dev/null 2>&1 || true
  $SUDO systemctl enable --now docker.service >/dev/null 2>&1 \
    || die "docker.service no arrancó — mira 'journalctl -u docker --no-pager -n 40'"
  ok "servicios arrancados y habilitados en el arranque"
else
  # Contenedores, WSL1, chroots: no hay systemd que gestione nada.
  SYSTEMD=0
  warn "no hay systemd — arranca el demonio a mano: 'sudo /usr/local/bin/dockerd &'"
fi

# --- 6. el usuario que va a instalar el geocodificador --------------------------------
TARGET_USER="${SUDO_USER:-$(id -un)}"
if [ "$TARGET_USER" != root ]; then
  $SUDO usermod -aG docker "$TARGET_USER" 2>/dev/null \
    && ok "'$TARGET_USER' añadido al grupo docker" \
    || warn "no se pudo añadir '$TARGET_USER' al grupo docker"
  warn "cierra la sesión y vuelve a entrar (o 'newgrp docker') para que surta efecto"
fi

# Espera a que el demonio conteste, hasta N segundos, y si no lo hace explica por
# qué en vez de mandar al operador a correr journalctl dentro de una distro de WSL
# que no sabe abrir.
#
# Dos motivos distintos para que este chequeo falle, y hay que distinguirlos:
#
# 1. CARRERA. `systemctl start docker.service` devuelve el control cuando el
#    servicio notifica READY=1, y eso no significa que el socket ya acepte
#    peticiones. De ahí la espera.
#
# 2. QUE `docker` NO SEA NUESTRO `docker`. Ésta es la que mordió el 2026-08-11 en
#    DESKTOP-O67CFOK: la espera agotó sus 60s completos mientras el journal decía
#    "Daemon has completed initialization" y "API listen on /run/docker.sock". No
#    era una carrera — el demonio llevaba 59s levantado. Esa máquina tiene un
#    Docker Desktop instalado (distro `docker-desktop` registrada), cuyo CLI se
#    cuela en el PATH de las distros con la integración de WSL activada, y habla
#    con un socket que en una distro importada a mano no existe. Las dos líneas de
#    `--version` de abajo ya usaban ruta absoluta; ésta no, y por eso preguntaba a
#    otro Docker. DOCKER_BIN se puede sobreescribir para poder probar esto.
#
# Se captura la salida ENTERA (2>&1), no sólo stderr: el intento anterior guardaba
# stderr y el mensaje de `docker info` no venía por ahí, así que el diagnóstico
# decía literalmente "(nada)" y costó un viaje entero a la máquina.
DOCKER_BIN="${DOCKER_BIN:-/usr/local/bin/docker}"

wait_for_docker_daemon() { # segundos_max
  local max="${1:-60}" waited=0 out=''
  while :; do
    if out="$($SUDO "$DOCKER_BIN" info 2>&1)"; then return 0; fi
    if [ "$waited" -ge "$max" ]; then break; fi
    sleep 2
    waited=$((waited + 2))
  done
  printf '%s' "$out"
  return 1
}

# El estado que hace falta para saber CUÁL de los dos motivos fue. Se enseña dentro
# del error: el operador está delante de un instalador, no de una terminal.
docker_daemon_log() {
  printf '     quién es "docker" en este PATH : %s\n' "$(command -v docker 2>/dev/null || printf '(ninguno)')"
  printf '     el que usa este script         : %s\n' "$DOCKER_BIN"
  printf '     sockets                        : %s\n' \
    "$(ls -l /run/docker.sock /var/run/docker.sock 2>&1 | tr '\n' ' ')"
  if command -v systemctl >/dev/null 2>&1; then
    printf '     systemctl is-active docker     : %s\n' "$($SUDO systemctl is-active docker 2>&1)"
  fi
  command -v journalctl >/dev/null 2>&1 || return 0
  printf '     últimas líneas de su log:\n'
  $SUDO journalctl -u docker --no-pager -n 20 2>&1 | sed 's/^/       /'
}

# --- 7. comprobar de verdad, no dar por hecho ------------------------------
say "Comprobando"
# Los binarios se comprueban SIEMPRE — que ejecuten es independiente de systemd, y
# es donde se vería una glibc demasiado vieja para los binarios estáticos.
/usr/local/bin/dockerd --version >/dev/null 2>&1 \
  || die "dockerd no ejecuta en esta máquina (¿glibc muy antigua?) — $(/usr/local/bin/dockerd --version 2>&1 | head -1)"
ok "demonio:  $(/usr/local/bin/dockerd --version)"
ok "cliente:  $(/usr/local/bin/docker --version)"
ok "compose:  $(/usr/local/bin/docker compose version | head -1)"

# El demonio sólo se puede exigir si hay algo que lo arranque. Sin systemd, pedirlo
# convertía un aviso ("arráncalo a mano") en un fallo dos líneas después.
if [ "$SYSTEMD" = 1 ]; then
  if err="$(wait_for_docker_daemon 60)"; then
    ok "el demonio responde"
  else
    die "los binarios están puestos pero el demonio no responde tras 60s.
     Lo que dijo '$DOCKER_BIN info':
$(printf '%s' "${err:-(nada)}" | sed 's/^/       /')
$(docker_daemon_log)"
  fi
else
  warn "sin systemd no se comprueba el demonio aquí — arráncalo y verifica con 'docker info'"
fi

cat <<EOF

  ${G}${B}Docker instalado${R}

  Sigue con el geocodificador:

      bash install.sh

  Si 'docker' sin sudo te da permiso denegado, cierra la sesión y vuelve a entrar.

  Para desinstalar (no usa apt): borra /usr/local/bin/{docker,dockerd,containerd,
  runc,ctr,docker-init,docker-proxy,containerd-shim-runc-v2},
  /usr/local/lib/docker y /etc/systemd/system/{docker.service,docker.socket,
  containerd.service}.

EOF
