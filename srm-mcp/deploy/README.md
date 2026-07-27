# srm-mcp — VM test stack (docker-compose)

Single Linux VM stack to exercise the SRM MCP through a chat UI:

```
open-webui ──(OpenAPI tools)──> mcpo ──(MCP streamable-HTTP)──> srm-mcp ──> VMware API
     └──────────────────────> external OpenAI-compatible LLM (tool calling)
```

OpenWebUI can't speak MCP directly — **mcpo** proxies our MCP into an OpenAPI tool
server that OpenWebUI consumes.

## Ports

| service | url | what |
|---|---|---|
| open-webui | `http://<vm>:3000` | chat UI |
| mcpo | `http://<vm>:8000/srm` | OpenAPI tool server (docs at `/srm/docs`) |
| srm-mcp | `http://<vm>:8080/healthz` | MCP health (debug) |

## Bring it up

```bash
cp .env.example .env                      # fill in token + OpenAI endpoint
sed -i "s/change-me.*/$(openssl rand -hex 24)/" .env
TOKEN=$(grep SRM_MCP_API_KEYS .env | cut -d= -f2)
mkdir -p mcpo
sed "s/__SRM_MCP_API_KEY__/$TOKEN/" mcpo/config.example.json > mcpo/config.json
docker compose up -d --build
```

Starts in **MOCK** mode (`SRM_LIVE=0`) — no lab access needed. Flip `SRM_LIVE=1`
(and set `SRM_SSO_PASS` / `SRM_APPLIANCE_PASS`) in `.env` to hit the real appliances,
then `docker compose up -d`.

## Wire up OpenWebUI (one-time, in the browser)

1. Open `http://<vm>:3000`, create the first (admin) account — it's local to this VM.
2. **Settings → Connections**: confirm the OpenAI connection (base URL + key from `.env`);
   pick a model that supports tool calling.
3. **Settings → Tools → +** add an OpenAPI server: URL `http://mcpo:8000/srm`
   (inside the compose network) — verify it lists the `srm_*` / `vm_*` tools.
4. In a chat, enable the tool and ask e.g. *"list the SRM environment"* or
   *"list VMs on site1"*. In MOCK mode you'll get the fixtures.

## Notes

- `mcpo/config.json` and `.env` hold secrets → gitignored. Only the `*.example.*` are committed.
- The VM must reach `192.168.114.x` (for `SRM_LIVE=1`) **and** the external OpenAI
  endpoint. On rtolab the `172.16.10.x` infra segment reaches both.
- Destructive tools stay gated even in the UI: `SRM_ALLOW_ACTIONS=1` **and** the
  per-call `confirm=` (and `execute=true` for recovery/pairing).
