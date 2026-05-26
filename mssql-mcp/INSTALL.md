# mssql-mcp — step-by-step install

Target host: a fresh Ubuntu **20.04 / 22.04 / 24.04** VM with network access to the MSSQL server.

There are two paths:

- **Online** — host has internet. `install-online.sh` does everything from source.
- **Offline** — air-gapped. Drop the prebuilt image tarball alongside the repo and run `install-offline.sh`.

Both end with: container `mssql-mcp` running, OpenAPI on `:8000`, bearer-secured.

---

## A. Online install (from source)

### 1. Prerequisites

- Ubuntu **20.04+**, kernel new enough for Docker (default focal kernel is fine).
- Network path to the MSSQL host on tcp/1433 (or whatever `SERVER_PORT` you use).
- Outbound https to `download.docker.com`, `deb.nodesource.com`, `ghcr.io`, `registry.npmjs.org` for the build.

```bash
# verify
ping -c1 <mssql-host>
nc -zv <mssql-host> 1433
```

### 2. Get the repo

```bash
sudo mkdir -p /opt/mssql-mcp
sudo chown $USER /opt/mssql-mcp
git clone https://github.com/kostenyang/mcp.git /tmp/mcp-clone
cp -r /tmp/mcp-clone/mssql-mcp/* /tmp/mcp-clone/mssql-mcp/.gitignore /opt/mssql-mcp/
cd /opt/mssql-mcp
```

> If `kostenyang/mcp` later moves the `mssql-mcp/` subfolder, adjust the path.

### 3. Fill in `.env`

```bash
cp .env.example .env
chmod 600 .env
$EDITOR .env
```

Minimum to set:

| Var | Why |
| --- | --- |
| `MCPO_API_KEY` | Bearer that callers (Open WebUI / Claude) must present. **Generate a fresh one per deploy** — e.g. `openssl rand -hex 24`. |
| `SERVER_NAME` | MSSQL host (IP or FQDN). |
| `DATABASE_NAME` | DB to default to. `master` works for a quick smoke test. |
| `SQL_USER`, `SQL_PASSWORD` | SQL Server login. Leave blank to fall back to Azure AD interactive auth. |
| `TRUST_SERVER_CERTIFICATE` | `true` for lab / self-signed; `false` for prod with a valid cert. |
| `READONLY` | `true` exposes only read tools. |

### 4. Run the installer

```bash
sudo bash install-online.sh
```

The script will:

1. Install Docker CE + compose plugin if not present.
2. `docker compose build` — assembles the image (mcpo base + Node 20 + npm install + tsc).
3. `docker compose up -d`.
4. Poll `:8000/openapi.json` until ready.
5. Print the bearer key + a sample `curl`.

### 5. Verify

```bash
KEY=$(grep ^MCPO_API_KEY /opt/mssql-mcp/.env | cut -d= -f2-)

# tool catalogue
curl -s http://127.0.0.1:8000/mssql/openapi.json | jq '.paths | keys'

# list user tables (will hit MSSQL)
curl -s -X POST http://127.0.0.1:8000/mssql/list_table \
     -H "Authorization: Bearer $KEY" \
     -H 'Content-Type: application/json' \
     -d '{"parameters":[]}'

# trivial read
curl -s -X POST http://127.0.0.1:8000/mssql/read_data \
     -H "Authorization: Bearer $KEY" \
     -H 'Content-Type: application/json' \
     -d '{"query":"SELECT 1 AS one, CURRENT_TIMESTAMP AS now"}'
```

Container logs: `docker compose -f /opt/mssql-mcp/docker-compose.yml logs -f`.

---

## B. Build the offline bundle (once, on a machine with internet)

```bash
cd /opt/mssql-mcp        # repo dir (or wherever you cloned it)
sudo bash build-offline-bundle.sh
```

This produces:

```
dist-offline/
├── mssql-mcp.image.tar.gz       (the docker image, ~200 MB)
├── docker-deb/                  (cached debs for offline Docker install)
└── mssql-mcp-offline.tar.gz     (everything bundled — ship this)
```

## B-bis. Publish on the lab static server (optional but recommended)

Have one host serve the image so air-gapped targets can `curl` it instead of needing scp. The compose stack ships an `offline-files` service for exactly this — it's a tiny `nginx:alpine` over `./dist-offline/`, gated by a profile so it only starts when you opt in:

```bash
cd /opt/mssql-mcp
docker compose --profile offline-server up -d offline-files
```

Now the tarballs are at:

```
http://<this-host>:8080/mssql-mcp.image.tar.gz
http://<this-host>:8080/mssql-mcp-offline.tar.gz
```

> Unauthenticated, read-only — lab-internal use only. Don't expose `:8080`
> outside the LAN.

To stop just the file server (keeps mssql-mcp running):

```bash
docker compose --profile offline-server stop offline-files
```

---

## C. Offline install on the target — three flavours

### C.1 Target can reach the lab static server (recommended)

```bash
sudo mkdir -p /opt
# Pull the bundle from the lab static server, extract.
curl -fL -O http://10.0.0.68:8080/mssql-mcp-offline.tar.gz
sudo tar -xzf mssql-mcp-offline.tar.gz -C /opt
cd /opt/mssql-mcp

sudo cp .env.example .env && sudo chmod 600 .env && sudoedit .env       # fill in DB creds
sudo bash install-offline.sh
```

`install-offline.sh` will:

1. Use the bundled `dist-offline/mssql-mcp.image.tar.gz` directly.
2. If Docker isn't installed AND `dist-offline/docker-deb/` is present, install Docker from those debs.
3. `docker load` the image.
4. `docker compose up -d`.
5. Poll for readiness.

### C.2 Target can only reach the image, not the full bundle

Put only the source repo on the target (e.g. clone from a mirror), then let the script fetch the image from the lab static server:

```bash
git clone https://github.com/kostenyang/mcp.git
cd mcp/mssql-mcp
cp .env.example .env && chmod 600 .env && $EDITOR .env

# Default BUNDLE_URL is http://10.0.0.68:8080/mssql-mcp.image.tar.gz; override if needed:
sudo BUNDLE_URL=http://your-host:8080/mssql-mcp.image.tar.gz bash install-offline.sh
```

### C.3 Fully air-gapped (no network at all)

Build the bundle on a connected machine (§B), `scp` the resulting `mssql-mcp-offline.tar.gz` to the target on a USB stick, then:

```bash
sudo tar -xzf mssql-mcp-offline.tar.gz -C /opt
cd /opt/mssql-mcp
sudo cp .env.example .env && sudo chmod 600 .env && sudoedit .env
sudo bash install-offline.sh
```

---

## D. Operate

```bash
cd /opt/mssql-mcp
docker compose ps                 # status
docker compose logs --tail=50     # logs
docker compose restart            # reload after .env change
docker compose down               # stop
docker compose pull               # n/a — image is local-built
```

To point at a different MSSQL: edit `.env`, then `docker compose up -d` (it'll recreate the container with new env).

To update the source after upstream changes:

```bash
cd /opt/mssql-mcp/src
git fetch origin && git checkout origin/main -- src/
# re-apply the SQL-auth patch by hand (or pull patched index.ts from this repo)
cd /opt/mssql-mcp
docker compose build --no-cache
docker compose up -d
```

---

## E. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `mcpo` log: `Failed to connect to 'mssql'` then container restarts | Node side crashed before mcpo could talk to it. Usually missing env vars. | `docker compose logs mssql-mcp` — read the node stderr lines; check `.env` actually has `SQL_USER`/`SQL_PASSWORD`. |
| `ELOGIN — Login failed for user 'X'` | SQL Server auth disabled, or wrong user/password. | Enable Mixed Mode on MSSQL, restart the service, retry. Or use `sa` to verify connectivity first. |
| `ESOCKET — Failed to connect to host:1433` | MSSQL not reachable. | Confirm `nc -zv host 1433` from inside the container: `docker compose exec mssql-mcp nc -zv $SERVER_NAME 1433`. |
| `read_data` returns `Security validation failed` | The Node-side tool has a SQL-injection guard that rejects `@@`-prefixed system functions and other patterns. | Phrase queries against real tables (`SELECT TOP 10 * FROM dbo.YourTable`). |
| 401 from `curl` | Wrong `Authorization: Bearer` value. | `grep MCPO_API_KEY /opt/mssql-mcp/.env`. |
| Container won't start after edit | YAML error in `docker-compose.yml`. | `docker compose config` to validate. |
