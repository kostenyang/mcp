#!/usr/bin/env bash
# install-offline.sh — air-gapped / lab-internal installer for mssql-mcp.
#
# Two ways to feed it the image tarball:
#
#   A) Pre-staged locally — put mssql-mcp.image.tar.gz in this directory
#      (this is what `build-offline-bundle.sh` produces, or what gets
#      shipped inside mssql-mcp-offline.tar.gz).
#
#   B) Lab static server — set BUNDLE_URL to a reachable URL serving the
#      tarball, e.g. http://10.0.0.68:8080/mssql-mcp.image.tar.gz, and the
#      script will curl it on first run.
#
# Optional: docker-deb/ next to this script will install Docker from cached
# .debs if Docker is missing. Otherwise the script errors and asks you to
# pre-install Docker.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# Default lab static endpoint. Override by exporting BUNDLE_URL beforehand.
: "${BUNDLE_URL:=http://10.0.0.68:8080/mssql-mcp.image.tar.gz}"

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "ERR: run with sudo / as root" >&2; exit 1
  fi
}

step() { echo; echo "==> $*"; }

ensure_docker_offline() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    return
  fi
  if [ -d docker-deb ]; then
    step "Installing Docker from cached .deb packages"
    dpkg -i docker-deb/*.deb || apt-get install -y -f
    systemctl enable --now docker
  else
    echo "ERR: Docker not installed and no docker-deb/ directory found." >&2
    echo "Either pre-install Docker on this host, or run install-online.sh." >&2
    exit 1
  fi
}

fetch_image_if_missing() {
  if [ -f mssql-mcp.image.tar.gz ]; then
    echo "  using local mssql-mcp.image.tar.gz ($(du -h mssql-mcp.image.tar.gz | cut -f1))"
    return
  fi
  step "Tarball missing locally — fetching from $BUNDLE_URL"
  if ! curl -fL --connect-timeout 10 -o mssql-mcp.image.tar.gz "$BUNDLE_URL"; then
    echo "ERR: failed to download $BUNDLE_URL" >&2
    echo "  Either:" >&2
    echo "    a) bring the tarball over manually and place it next to this script, or" >&2
    echo "    b) export BUNDLE_URL=<reachable url> and re-run," >&2
    echo "    c) start the offline-files server on a reachable host (see INSTALL.md)" >&2
    exit 1
  fi
  echo "  downloaded $(du -h mssql-mcp.image.tar.gz | cut -f1)"
}

require_root
ensure_docker_offline
fetch_image_if_missing

step "Loading container image"
gunzip -c mssql-mcp.image.tar.gz | docker load

if [ ! -f .env ]; then
  step ".env not found, copying from .env.example — EDIT IT before continuing"
  cp .env.example .env
  chmod 600 .env
  echo "Edit $HERE/.env then rerun this script."
  exit 1
fi

step "Starting container"
docker compose up -d

step "Waiting for mcpo on :8000"
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/openapi.json >/dev/null 2>&1; then
    echo "ok"; break
  fi
  sleep 1
done

cat <<DONE

==================================================================
mssql-mcp is up on http://$(hostname -I | awk '{print $1}'):8000

API key:  $(grep ^MCPO_API_KEY .env | cut -d= -f2-)

Logs:    docker compose logs -f
Stop:    docker compose down
==================================================================
DONE
