#!/usr/bin/env bash
# build-offline-bundle.sh — produce a self-contained offline install bundle.
# Run this on a machine that has docker + internet, sitting in the mssql-mcp/
# repo directory. Output:
#
#   dist-offline/
#   ├── mssql-mcp.image.tar.gz       (the docker image)
#   ├── docker-deb/                  (cached debs for offline Docker install)
#   └── mssql-mcp-offline.tar.gz     (everything bundled — ship this)
#
# Then on the air-gapped target:
#   scp mssql-mcp-offline.tar.gz target:/tmp/
#   ssh target
#   sudo tar -xzf /tmp/mssql-mcp-offline.tar.gz -C /opt/
#   cd /opt/mssql-mcp
#   sudo bash install-offline.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

step() { echo; echo "==> $*"; }

mkdir -p dist-offline

step "Building docker image (if not already built)"
docker compose build

step "Saving image to dist-offline/mssql-mcp.image.tar.gz"
docker save mssql-mcp:local | gzip > dist-offline/mssql-mcp.image.tar.gz
ls -lh dist-offline/mssql-mcp.image.tar.gz

step "Caching Docker .deb packages for offline-host install"
mkdir -p dist-offline/docker-deb
(
  cd dist-offline/docker-deb
  apt-get download docker-ce docker-ce-cli containerd.io \
                   docker-buildx-plugin docker-compose-plugin 2>/dev/null || \
    echo "  (apt-get download failed — ok if host doesn't have docker apt repo configured)"
)

step "Bundling everything into dist-offline/mssql-mcp-offline.tar.gz"
# The tar is rooted at $(dirname $HERE) so paths inside the archive look like
# mssql-mcp/Dockerfile, mssql-mcp/src/... etc. — extracting with `-C /opt`
# drops everything into /opt/mssql-mcp/ on the target.
PARENT="$(dirname "$HERE")"
BASE="$(basename "$HERE")"          # usually "mssql-mcp"
TMPLIST="$(mktemp)"
trap "rm -f $TMPLIST" EXIT
{
  for f in Dockerfile docker-compose.yml config.json .env.example \
           .gitattributes .gitignore \
           install-offline.sh install-online.sh build-offline-bundle.sh \
           README.md INSTALL.md PATCHES.md; do
    [ -f "$HERE/$f" ] && echo "$BASE/$f"
  done
  ( cd "$PARENT" && find "$BASE/src" -type f \
      \! -path '*/node_modules/*' \! -path '*/dist/*' )
  ( cd "$PARENT" && find "$BASE/dist-offline" -type f )
} > "$TMPLIST"
tar -czf dist-offline/mssql-mcp-offline.tar.gz -C "$PARENT" -T "$TMPLIST"

ls -lh dist-offline/mssql-mcp-offline.tar.gz

cat <<DONE

==================================================================
✓ Offline bundle built.

To publish on the lab static server (10.0.0.68:8080):
  scp dist-offline/mssql-mcp-offline.tar.gz \\
      root@10.0.0.68:/opt/mssql-mcp/dist-offline/

To install on a target (assuming the target can reach 10.0.0.68:8080):
  curl -O http://10.0.0.68:8080/mssql-mcp-offline.tar.gz
  sudo tar -xzf mssql-mcp-offline.tar.gz -C /opt
  cd /opt/mssql-mcp
  sudo bash install-offline.sh
==================================================================
DONE
