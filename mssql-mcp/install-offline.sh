#!/usr/bin/env bash
# install-offline.sh — air-gapped installer for mssql-mcp.
# Expects this directory to contain:
#   docker-compose.yml, config.json, .env (filled in), .env.example
#   mssql-mcp.image.tar.gz   (output of `docker save mssql-mcp:local | gzip`)
#   docker-deb/              (optional: cached .deb files for offline Docker install)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

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

require_root
ensure_docker_offline

if [ ! -f mssql-mcp.image.tar.gz ]; then
  echo "ERR: mssql-mcp.image.tar.gz missing in $HERE" >&2
  exit 1
fi

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
