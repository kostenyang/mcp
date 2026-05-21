FROM ghcr.io/open-webui/mcpo:main

USER root

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app/mssql-mcp

COPY src/package.json src/package-lock.json src/tsconfig.json ./
COPY src/src ./src

RUN npm ci --silent \
 && npm run build \
 && npm prune --omit=dev

COPY config.json /app/config.json

WORKDIR /app
