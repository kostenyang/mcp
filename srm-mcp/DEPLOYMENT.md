# srm-mcp — Deployment Guide

One codebase, **three deployment forms**, selected by the `MCP_TRANSPORT` env var:

| Form | Transport | Best for | Section |
|------|-----------|----------|---------|
| **Kubernetes / VKS** | streamable-HTTP `:8080` | shared / production on a cluster | [§1](#1-kubernetes--vks) |
| **VM (docker-compose)** | HTTP + mcpo + OpenWebUI | one Linux VM, browser chat testing | [§2](#2-vm-docker-compose--mcpo--openwebui) |
| **Docker (stdio)** | stdio | Claude Code / Desktop, `docker run -i` | [§3](#3-docker-stdio) |

All three run the same image. They differ only in how the process is started and how a client reaches it.

---

## Configuration (applies to all forms)

**Topology** lives in `config/srm.example.yaml` (copy to `config/srm.yaml` for a real run;
IPs are already rtolab's, so the example works for LIVE too). Passwords are **never** in
YAML — they come from env vars.

**Runtime env vars:**

| Var | Default | Meaning |
|-----|---------|---------|
| `MCP_TRANSPORT` | `http` | `http` (k8s/vm) or `stdio` (docker/Claude clients) |
| `SRM_LIVE` | `0` | `0` = MOCK (no lab needed); `1` = hit real `192.168.114.x` appliances |
| `SRM_ALLOW_ACTIONS` | `0` | `1` = enable destructive tools (recovery / pairing / VM writes) |
| `SRM_SSO_PASS` | — | vCenter SSO password (only when `SRM_LIVE=1`) |
| `SRM_APPLIANCE_PASS` | — | SRM/VR appliance VAMI password (only when `SRM_LIVE=1`) |
| `SRM_MCP_API_KEYS` | — | comma-separated Bearer tokens for HTTP transport (k8s/vm) |
| `SRM_API_VERSION` | `v1` | SRM REST API version |

> **Reachability:** for `SRM_LIVE=1` the process must reach `192.168.114.x`. In rtolab the
> `172.16.10.x` infra segment reaches both that subnet and the internet.

> **Safety:** read-only tools are always available. Destructive tools refuse unless
> `SRM_ALLOW_ACTIONS=1` **and** the call passes `confirm=<target>` (and `execute=true` for
> recovery/pairing). Default config is physically read-only.

---

## 1. Kubernetes / VKS

Streamable-HTTP server on `:8080` behind a Service. Manifests are in `manifests/`.

### Build & push the image

```bash
docker build -t <registry>/srm-mcp:0.1.0 .
docker push <registry>/srm-mcp:0.1.0
# update the image: in manifests/deployment.yaml to match
```

### Deploy

```bash
kubectl apply -f manifests/namespace.yaml
kubectl apply -f manifests/configmap.yaml          # topology (no secrets)

# secret: create imperatively so values never sit in a file
kubectl -n srm-mcp create secret generic srm-secrets \
  --from-literal=SRM_SSO_PASS='<your-vcenter-sso-password>' \
  --from-literal=SRM_APPLIANCE_PASS='<your-appliance-vami-password>' \
  --from-literal=SRM_MCP_API_KEYS="$(openssl rand -hex 24)"

kubectl apply -f manifests/deployment.yaml
kubectl apply -f manifests/service.yaml
```

### Access

```bash
kubectl -n srm-mcp port-forward svc/srm-mcp 8080:8080
# health: http://localhost:8080/healthz   MCP: http://localhost:8080/mcp (Bearer token)
```

For access from outside the cluster, front the Service with an Ingress / HTTPProxy.

### Go LIVE

Set `SRM_LIVE: "1"` (and, if wanted, `SRM_ALLOW_ACTIONS: "1"`) in
`manifests/deployment.yaml`, and make sure the pod network can reach `192.168.114.x`
and resolve (or `/etc/hosts`) the `rtolab.local` FQDNs. `kubectl rollout restart deploy/srm-mcp -n srm-mcp`.

---

## 2. VM (docker-compose + mcpo + OpenWebUI)

Full stack on a single Linux VM so you can drive the MCP from a chat UI:

```
OpenWebUI ──(OpenAPI tools)──> mcpo ──(MCP streamable-HTTP)──> srm-mcp ──> VMware API
     └──────────────────────> external LLM (tool calling)
```

Files are in `deploy/`. The provisioning scripts that build the VM itself are in
`deploy/provision/` (rtolab-specific: cloud-init seed ISO + OVA deploy via PowerCLI).

### Prereqs on the VM

Ubuntu with Docker + compose plugin. (On Ubuntu 20.04, the `get.docker.com` script
fails on `docker-model-plugin`; install `docker-ce docker-ce-cli containerd.io
docker-compose-plugin docker-buildx-plugin` explicitly — the seed script already does this.)

### Bring it up

```bash
cd deploy
cp .env.example .env                                  # fill in
sed -i "s/change-me.*/$(openssl rand -hex 24)/" .env  # generate the Bearer token
TOKEN=$(grep SRM_MCP_API_KEYS .env | cut -d= -f2)
mkdir -p mcpo
sed "s/__SRM_MCP_API_KEY__/$TOKEN/" mcpo/config.example.json > mcpo/config.json
docker compose up -d --build
```

Starts in **MOCK** (`SRM_LIVE=0`). Flip `SRM_LIVE=1` (+ `SRM_SSO_PASS`/`SRM_APPLIANCE_PASS`)
in `.env` and `docker compose up -d` to hit real appliances.

### Ports

| Service | URL | What |
|---|---|---|
| open-webui | `http://<vm>:3000` | chat UI |
| mcpo | `http://<vm>:8000/srm` | OpenAPI tool server (docs at `/srm/docs`) |
| srm-mcp | `http://<vm>:8080/healthz` | MCP health |

### Wire up OpenWebUI (one-time, in the browser)

1. Open `http://<vm>:3000`, create the first (admin) account.
2. **Admin → Settings → Connections**: add an OpenAI connection to your LLM. For **Gemini**
   the base URL is `https://generativelanguage.googleapis.com/v1beta/openai` (paste your
   own API key). Save; the model list loads.
3. **Settings → Integrations → Manage Tool Servers → +**: add
   `http://<vm>:8000/srm` (browser-reachable; mcpo sends `Access-Control-Allow-Origin: *`).
4. In a chat, **Controls → Function Calling = Native**, enable the `srm-mcp` tool (input-bar
   tools icon; **re-enable it after switching models** — OpenWebUI resets it), then ask e.g.
   *"list VMs on site1"* or *"test failover plan rp-0001 on site1 and report each VM power_state"*.

### LLM backend note (function calling)

Tool calls only fire if the model actually supports and emits them:

- **Gemini** is strict: its function-calling accepts only a subset of JSON-Schema, so tool
  schemas are sanitized at startup (`_gemini_safe_schema` strips `title`/`default`/
  `additionalProperties`/`$ref`/`$defs`). Prefer **non-thinking** models
  (`gemini-2.5-flash-lite`); the *thinking* models (`gemini-2.5-flash`/`-pro`) can return
  empty content in some OpenWebUI versions.
- **OpenAI (`gpt-4o-mini`) / Claude** have lenient function-calling and work with
  OpenWebUI + mcpo out of the box — use them if a Gemini model won't emit tool calls.

---

## 3. Docker (stdio)

For an MCP client (Claude Code / Desktop) that launches the server itself over stdin/stdout.
No HTTP, no Bearer token, no open port. Details in `deploy/mcp-stdio/`.

### Build

```bash
docker build -t srm-mcp:0.1.0 .
```

### Smoke test (MOCK)

```bash
docker run -i --rm -e MCP_TRANSPORT=stdio srm-mcp:0.1.0
# waits on stdin for JSON-RPC; banner goes to stderr
```

### Claude Code (`.mcp.json`) / Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "srm": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-e", "MCP_TRANSPORT=stdio", "srm-mcp:0.1.0"]
    }
  }
}
```

### Go LIVE

Add env + host networking (the machine running docker must reach `192.168.114.x`):

```json
"args": ["run","-i","--rm",
  "-e","MCP_TRANSPORT=stdio",
  "-e","SRM_LIVE=1",
  "-e","SRM_ALLOW_ACTIONS=0",
  "-e","SRM_SSO_PASS=<your-vcenter-sso-password>",
  "-e","SRM_APPLIANCE_PASS=<your-appliance-vami-password>",
  "--network","host",
  "srm-mcp:0.1.0"]
```

stdio has no auth layer — security is "only whoever can run `docker run` can start it".

---

## Verifying any deployment

```bash
# HTTP forms (k8s/vm):
curl -s http://<host>:8080/healthz            # -> ok
curl -s http://<host>:8000/srm/openapi.json   # (vm) -> lists all tools

# any form, MOCK: list_srm_environment / vm_list return fixtures;
# srm_failover_and_watch (with SRM_ALLOW_ACTIONS=1 + confirm + execute) walks
# trigger -> poll -> VM power_state (OFF->ON) end to end.
```
