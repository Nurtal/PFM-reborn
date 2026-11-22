#!/bin/sh
# PFM-NG installer: one command, no question asked.
#
#   curl -fsSL https://raw.githubusercontent.com/Nurtal/PFM-reborn/main/install.sh | sh
#
# Re-running it updates the installation (pull + restart, data kept).
# Optional settings, passed as environment variables (… | PFM_DOMAIN=x sh):
#   PFM_DOMAIN   public domain name: adds Caddy with automatic HTTPS (ports 80/443)
#   PFM_URL      address users type, if auto-detection guesses wrong
#   PFM_PORT     HTTP port without a domain (default 8000)
#   PFM_DIR      install directory (default /opt/pfm as root, ~/pfm otherwise)
#   PFM_VERSION  image tag (default latest)
#   PFM_IMAGE    image name (default ghcr.io/nurtal/pfm-ng)
set -eu

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\033[31mError: %s\033[0m\n' "$*" >&2; exit 1; }

IMAGE="${PFM_IMAGE:-ghcr.io/nurtal/pfm-ng}:${PFM_VERSION:-latest}"
if [ "$(id -u)" = 0 ]; then DIR="${PFM_DIR:-/opt/pfm}"; else DIR="${PFM_DIR:-$HOME/pfm}"; fi
PORT="${PFM_PORT:-8000}"
DOMAIN="${PFM_DOMAIN:-}"

# --- Docker ---------------------------------------------------------------
SUDO=""
if ! command -v docker >/dev/null 2>&1; then
  say "Docker is missing: installing it with the official script (get.docker.com)"
  [ "$(id -u)" = 0 ] || SUDO="sudo"
  command -v curl >/dev/null 2>&1 || die "curl is required"
  curl -fsSL https://get.docker.com | $SUDO sh
fi
if ! docker info >/dev/null 2>&1; then
  if [ "$(id -u)" != 0 ] && command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
    SUDO="sudo"
  else
    die "cannot reach the Docker daemon (is it running? are you in the docker group?)"
  fi
fi
$SUDO docker compose version >/dev/null 2>&1 || die "the 'docker compose' plugin is required"
# Compose reads its settings from $DIR/.env only, never from this shell.
dc() {
  env -u PFM_IMAGE -u PFM_URL -u PFM_PORT -u PFM_DOMAIN -u PFM_BEHIND_PROXY -u PFM_NO_NEW_PRIVILEGES -u COMPOSE_PROFILES \
    $SUDO docker compose --project-directory "$DIR" -p pfm "$@"
}

# Snap-packaged Docker (offered by the Ubuntu Server installer) cannot start
# any container with no-new-privileges (AppArmor): keep it everywhere else.
NNP=true
case "$($SUDO docker info --format '{{.DockerRootDir}}' 2>/dev/null)" in
  */snap/*) NNP=false; say "Snap Docker detected: no-new-privileges disabled (unsupported there)" ;;
esac

# --- Address users will type ------------------------------------------------
if [ -n "$DOMAIN" ]; then
  URL="https://$DOMAIN"
elif [ -n "${PFM_URL:-}" ]; then
  URL="$PFM_URL"
else
  ip=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -n1)
  [ -n "$ip" ] || ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  URL="http://${ip:-localhost}:$PORT"
fi

# --- Files ------------------------------------------------------------------
$SUDO mkdir -p "$DIR"
say "Installing into $DIR"

# Settings are kept on update: edit $DIR/.env, then re-run the installer.
if [ ! -f "$DIR/.env" ]; then
  {
    echo "PFM_IMAGE=$IMAGE"
    echo "PFM_URL=$URL"
    echo "PFM_PORT=$PORT"
    echo "PFM_DOMAIN=$DOMAIN"
    if [ -n "$DOMAIN" ]; then
      echo "PFM_BEHIND_PROXY=true"
      echo "COMPOSE_PROFILES=tls"
    else
      echo "PFM_BEHIND_PROXY=false"
      echo "COMPOSE_PROFILES="
    fi
  } | $SUDO tee "$DIR/.env" >/dev/null
else
  say "Keeping the existing settings ($DIR/.env)"
  $SUDO sed -i "s|^PFM_IMAGE=.*|PFM_IMAGE=$IMAGE|; /^PFM_NO_NEW_PRIVILEGES=/d" "$DIR/.env"
fi
echo "PFM_NO_NEW_PRIVILEGES=$NNP" | $SUDO tee -a "$DIR/.env" >/dev/null

$SUDO tee "$DIR/compose.yaml" >/dev/null <<'YAML'
# Written by install.sh (overwritten on update: put settings in .env).
services:
  pfm:
    image: ${PFM_IMAGE}
    container_name: pfm
    restart: unless-stopped
    ports: ["${PFM_PORT:-8000}:8000"]
    volumes: ["pfm-data:/data"]
    environment:
      PFM_URL: ${PFM_URL}
      PFM_BEHIND_PROXY: ${PFM_BEHIND_PROXY:-false}
    read_only: true
    tmpfs: ["/tmp"]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:${PFM_NO_NEW_PRIVILEGES:-true}"]
    mem_limit: 384m

  caddy:
    profiles: [tls]
    image: caddy:2-alpine
    restart: unless-stopped
    command: caddy reverse-proxy --from ${PFM_DOMAIN:-localhost} --to pfm:8000
    ports: ["80:80", "443:443"]
    volumes: ["caddy-data:/data"]
    depends_on: [pfm]

volumes:
  pfm-data:
  caddy-data:
YAML

# Admin shortcut: ./pfm backup, ./pfm createadmin, ./pfm logs, ./pfm update…
$SUDO tee "$DIR/pfm" >/dev/null <<SH
#!/bin/sh
cd "\$(dirname "\$0")"
case "\${1:-help}" in
  logs)    exec $SUDO docker compose -p pfm logs -f pfm ;;
  update)  $SUDO docker compose -p pfm pull && exec $SUDO docker compose -p pfm up -d ;;
  stop)    exec $SUDO docker compose -p pfm stop ;;
  start)   exec $SUDO docker compose -p pfm up -d ;;
  *)       exec $SUDO docker exec -it pfm pfm "\$@" ;;
esac
SH
$SUDO chmod +x "$DIR/pfm"

# --- Start ------------------------------------------------------------------
say "Downloading $IMAGE"
dc pull
say "Starting"
dc up -d --remove-orphans

printf 'Waiting for PFM-NG to be ready'
i=0
until [ "$($SUDO docker inspect -f '{{.State.Health.Status}}' pfm 2>/dev/null)" = healthy ]; do
  i=$((i + 1))
  [ $i -le 60 ] || { echo; dc logs --tail 50 pfm; die "PFM-NG did not become ready (logs above)"; }
  printf '.'
  sleep 2
done
echo

URL=$(sed -n 's/^PFM_URL=//p' "$DIR/.env")
TOKEN=$($SUDO docker logs pfm 2>&1 | sed -n 's/.*enter the setup token: //p' | tail -n1)
say "PFM-NG is running: $URL"
if [ -n "$TOKEN" ]; then
  echo "    Open $URL/setup/ and enter the setup token: $TOKEN"
fi
echo "    Admin commands: $DIR/pfm help  (backup, logs, update, createadmin…)"
echo "    Update later:   $DIR/pfm update  (or re-run this installer)"
