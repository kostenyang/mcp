# mssql-mcp

MSSQL MCP server packaged for the VCF lab.

Microsoft's official [Azure-Samples/SQL-AI-samples/MssqlMcp/Node](https://github.com/Azure-Samples/SQL-AI-samples/tree/main/MssqlMcp/Node) only ships a **stdio** transport and **Azure AD** auth. This wrapper:

1. Vendors the upstream source under `src/`.
2. Patches `src/src/index.ts` to **also accept SQL Server username/password auth** (on-prem MSSQL) — the AAD path is preserved as a fallback.
3. Wraps the stdio MCP with [`mcpo`](https://github.com/open-webui/mcpo) so the tools are reachable over **HTTP / OpenAPI** on port `8000`, secured with a bearer key.
4. Ships a Docker image that contains everything (Python + Node + the built MCP), so deployment is a single `docker compose up`.

Result: one container per target DB, mcpo on `:8000`, drop-in tool source for Open WebUI or any OpenAPI consumer.

## Tools exposed

When `READONLY=false` (default):

- `list_table`, `read_data`, `describe_table`
- `insert_data`, `update_data`, `create_table`, `create_index`, `drop_table`

When `READONLY=true`: only the read trio.

## Install

See [INSTALL.md](INSTALL.md) for the step-by-step. TL;DR — three paths:

```bash
cp .env.example .env && $EDITOR .env           # fill in DB creds + API key

# Path 1: target has internet
sudo bash install-online.sh                     # builds image from source

# Path 2: target is in lab, can reach the lab static server (10.0.0.68:8080)
sudo bash install-offline.sh                    # auto-curls image from BUNDLE_URL

# Path 3: fully air-gapped — first run build-offline-bundle.sh on a connected
# machine, ship the produced mssql-mcp-offline.tar.gz, then on the target:
sudo tar -xzf mssql-mcp-offline.tar.gz -C /opt && cd /opt/mssql-mcp && \
  sudo bash install-offline.sh
```

To make Path 2 work for others, the host that built the image runs:

```bash
docker compose --profile offline-server up -d offline-files
# now http://<this-host>:8080/ serves dist-offline/ over plain nginx
```

## Quick test

```bash
KEY=$(grep ^MCPO_API_KEY .env | cut -d= -f2-)
curl -s -X POST http://127.0.0.1:8000/mssql/list_table \
     -H "Authorization: Bearer $KEY" \
     -H 'Content-Type: application/json' \
     -d '{"parameters":[]}'
```

## Wire into Open WebUI

This container already speaks OpenAPI on `:8000`, so register it **directly** in Open WebUI — don't try to chain it behind another `mcpo`, they're the same layer.

1. Open WebUI → **Admin Panel → Settings → Tools → +**
2. Fill:
   - **URL**: `http://<this-host>:8000/mssql`
   - **API Key Type**: `Bearer`
   - **API Key**: value of `MCPO_API_KEY` from `.env`
3. Save. Open WebUI fetches the schema and exposes every tool (`list_table`, `read_data`, …) in chat.

For why this differs from how `vcf-lab` is wired (which goes through a central `mcpo` because the upstream is stdio-only), see the openwebui repo §7.7.

## Layout

```
mssql-mcp/
├── README.md
├── INSTALL.md           — step-by-step (online + offline)
├── Dockerfile           — builds: mcpo (base) + Node 20 + compiled MCP
├── docker-compose.yml   — single container, port 8000
├── config.json          — mcpo upstream: stdio → node dist/index.js
├── .env.example         — env template (commit), .env stays out of git
├── install-online.sh    — first-time install with internet
├── install-offline.sh   — install from pre-built image tar
└── src/                 — vendored Microsoft sample + SQL-auth patch
```
