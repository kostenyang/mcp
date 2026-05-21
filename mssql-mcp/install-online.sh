#!/usr/bin/env bash
# install-online.sh — online installer for mssql-mcp.
# Builds the Docker image from source and brings up the container.
# Run as root on a fresh Ubuntu 20.04+ host that already has the repo checked out.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "ERR: run with sudo / as root" >&2; exit 1
  fi
}

step() { echo; echo "==> $*"; }

ensure_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    return
  fi
  step "Installing Docker CE + compose plugin"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl gnupg lsb-release
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
}

ensure_env() {
  if [ -f .env ]; then return; fi
  step ".env not found, copying from .env.example — EDIT IT before continuing"
  cp .env.example .env
  chmod 600 .env
  echo
  echo "Edit $HERE/.env to set SERVER_NAME, SQL_USER, SQL_PASSWORD, MCPO_API_KEY then rerun."
  exit 1
}

require_root
ensure_docker
ensure_env

step "Building image (this takes ~1-2 min the first time)"
docker compose build

step "Starting container"
docker compose up -d

step "Waiting for mcpo on :8000"
for i in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:8000/openapi.json >/dev/null 2>&1; then
    echo "ok"; break
  fi
  sleep 1
done

step "Tool list at /mssql/openapi.json:"
curl -s http://127.0.0.1:8000/mssql/openapi.json \
  | python3 -c 'import sys,json; print("\n".join(json.load(sys.stdin).get("paths",{}).keys()))' \
  || echo "(could not parse — check 'docker compose logs')"

cat <<DONE

==================================================================
mssql-mcp is up on http://$(hostname -I | awk '{print $1}'):8000

API key (Authorization: Bearer ...):
  $(grep ^MCPO_API_KEY .env | cut -d= -f2-)

Quick test:
  curl -s -X POST http://127.0.0.1:8000/mssql/list_table \\
       -H "Authorization: Bearer \$KEY" \\
       -H 'Content-Type: application/json' \\
       -d '{"parameters":[]}'

Logs:    docker compose logs -f
Stop:    docker compose down
==================================================================
DONE
