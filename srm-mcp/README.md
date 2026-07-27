# srm-mcp

MCP server that exposes the **rtolab VCF 5.2.1 two-site SRM / vSphere Replication**
environment (VMware Live Site Recovery 9.0.2) as tools for Claude Code / Desktop.

Wraps the two verified REST surfaces of the SRM/VR appliances:

- **SRM v2 REST** — `https://<srm>/api/rest/srm/v2/…` (session → `x-dr-session`)
- **Appliance config API** — `https://<ip>:5480/configure/requestHandlers/…`

Runs **mock-first** (no lab needed to develop), ships as a **container**, and deploys
to **Kubernetes / VKS** with the manifests in [`manifests/`](manifests/).

> Target environment: rtolab only (`192.168.114.x` / `rtolab.local`). Endpoints come
> from [`rtolab/srm/README.md`](../rtolab/srm/README.md). Never carry these across labs.

---

## Design

| | |
|---|---|
| **Framework** | Python + FastMCP (same stack as `mcp/rtolab-mcp`) |
| **Transport** | streamable-HTTP on `:8080`, path `/mcp` (k8s/VKS friendly) |
| **Auth** | Bearer token(s) from env `SRM_MCP_API_KEYS` (comma-separated) |
| **Health** | `GET /healthz` → 200, unauthenticated (k8s probes) |
| **Mode** | MOCK by default; `SRM_LIVE=1` hits real appliances |
| **Safety** | read-only tools always on; action tools default-OFF |

### Safety model (three gates on destructive actions)

Action tools (`srm_run_recovery_plan`, `srm_pair_sites`) refuse unless **all** hold:

1. `SRM_ALLOW_ACTIONS=1` is set on the server, **and**
2. the call passes `confirm=<target>` (plan id / remote site), **and**
3. the call passes `execute=True` — otherwise it **dry-runs** and prints the exact
   request it would send.

With the defaults (`SRM_ALLOW_ACTIONS=0`) the server is physically read-only.

---

## Tools

**Read-only (always available)**

| tool | what |
|---|---|
| `list_srm_environment` | topology + runtime flags (no secrets) |
| `get_ssl_thumbprint` | SHA-256 cert thumbprint of any host |
| `srm_appliance_summary` | appliance config/health (`getSummaryInfo`) |
| `srm_list_pairings` | site pairings — **verified** path `/pairings` |
| `srm_list_recovery_plans` | recovery plans — *best-effort* path |
| `srm_list_protection_groups` | protection groups — *best-effort* path |
| `vr_list_replications` | VR replications — *best-effort* (VR dr-rest undocumented) |
| `srm_api` | generic SRM v2 passthrough (auto session) |
| `srm_config_api` | generic `:5480` config-API passthrough |
| `vm_list` | VMs on a site's inner vCenter (name/id/power) |
| `vm_info` | one VM's detail |
| `vm_snapshot_list` | list a VM's snapshots |

**Actions (gated — default OFF)**

| tool | gate | what |
|---|---|---|
| `vm_power` | `confirm=vm_id` | on / off / reset / suspend a VM |
| `vm_snapshot` | `confirm=vm_id` | create / revert / delete a snapshot |
| `srm_run_recovery_plan` | `confirm=plan_id` + `execute=True` | test / cleanup / recovery / reprotect / cancel a plan |
| `srm_pair_sites` | `confirm=remote_site` + `execute=True` | build + attempt the two-site pairing |

VM tools are **site-oriented**: `site1` → inner vCenter `.96` (protected), `site2` →
`.56` (recovery). Read tools are always on; power/snapshot writes need
`SRM_ALLOW_ACTIONS=1` + `confirm=<vm_id>`. The heavier SRM actions add an `execute=True`
dry-run gate on top.

> **Path honesty:** only `/session` and `/pairings` are verified against the rtolab
> appliances. Recovery-plan / protection-group / VR paths are marked *best-effort* in
> the tool docstrings and backed by clearly-labelled synthetic mock fixtures —
> calibrate them the first time you run LIVE, then update `client.py` / `mock.py`.

---

## Quickstart — local dev (MOCK, no lab)

```bash
python -m venv .venv && ./.venv/Scripts/python -m pip install -r requirements.txt   # Windows
# source .venv/bin/activate on Linux

# runs in MOCK mode; auth disabled if SRM_MCP_API_KEYS unset (dev only)
python -m srmmcp.server
# -> streamable-HTTP on http://0.0.0.0:8080/mcp ,  health at /healthz
```

Point an MCP client at `http://<host>:8080/mcp` (Bearer token if configured).

## Going LIVE (against real appliances)

Run from somewhere that can reach `192.168.114.x` (SELAB-Cluster Ubuntu VM, or a VKS
pod with routing to that subnet — the same reachability the `rtolab-mcp` server needs).

```bash
export SRM_LIVE=1
export SRM_SSO_PASS='<your-vcenter-sso-password>'       # vCenter SSO administrator@vsphere.local
export SRM_APPLIANCE_PASS='<your-appliance-vami-password>'  # appliance VAMI admin
export SRM_MCP_API_KEYS="$(openssl rand -hex 24)"
# to enable destructive tools as well:
# export SRM_ALLOW_ACTIONS=1
cp config/srm.example.yaml config/srm.yaml      # edit if endpoints change
python -m srmmcp.server
```

Passwords are **only** read from env vars — never stored in `srm.yaml`.

---

## Three ways to run (one codebase, `MCP_TRANSPORT` switches)

**Full step-by-step for all three: [DEPLOYMENT.md](DEPLOYMENT.md).**

| form | transport | where | docs |
|---|---|---|---|
| **k8s** | streamable-HTTP `:8080` | VKS / shared | `manifests/` |
| **vm** | HTTP + mcpo + OpenWebUI | one Linux VM, browser chat | `deploy/` |
| **docker (stdio)** | stdio | Claude Code / Desktop, `docker run -i` | `deploy/mcp-stdio/` |

## Container + Kubernetes / VKS

```bash
docker build -t srm-mcp:0.1.0 .
# smoke test the image in MOCK mode:
docker run --rm -p 8080:8080 -e SRM_MCP_API_KEYS=dev srm-mcp:0.1.0
```

Deploy to VKS (push the image to your registry / Harbor first, update `image:` in
`manifests/deployment.yaml`):

```bash
kubectl apply -f manifests/namespace.yaml
kubectl apply -f manifests/configmap.yaml
kubectl -n srm-mcp create secret generic srm-secrets \
  --from-literal=SRM_SSO_PASS='<your-vcenter-sso-password>' \
  --from-literal=SRM_APPLIANCE_PASS='<your-appliance-vami-password>' \
  --from-literal=SRM_MCP_API_KEYS="$(openssl rand -hex 24)"
kubectl apply -f manifests/deployment.yaml
kubectl apply -f manifests/service.yaml

# quick access without an Ingress:
kubectl -n srm-mcp port-forward svc/srm-mcp 8080:8080
```

To go LIVE on the cluster, set `SRM_LIVE=1` (and, if wanted, `SRM_ALLOW_ACTIONS=1`)
in `manifests/deployment.yaml`, and make sure the pod network can reach
`192.168.114.x` and resolve (or `/etc/hosts`) the `rtolab.local` FQDNs.

---

## Known limitation (inherited from the lab)

The two rtolab sites are **not yet paired**: SRM appliance leaf certs are self-signed
(not VMCA-issued), so cross-site pairing fails with `ProbeServicesException`. Until the
cert-trust wall is resolved (see [`rtolab/srm/README.md`](../rtolab/srm/README.md)),
`srm_list_pairings` returns empty and `srm_pair_sites` will report that failure rather
than succeed. The read-only per-site tools still work.

## Layout

```
srmmcp/
  config.py   topology + env flags (SRM_LIVE / SRM_ALLOW_ACTIONS / SRM_CONFIG)
  client.py   SRM v2 REST + config API sessions; MOCK/LIVE dispatch
  vcenter.py  inner-vCenter client for VM tools (site1=.96 / site2=.56)
  mock.py     offline fixtures (real-state summary + synthetic plans + VM inventory)
  server.py   FastMCP tools, Bearer gateway, /healthz, streamable-HTTP entry
config/srm.example.yaml   topology template (copy to srm.yaml, gitignored)
manifests/                namespace / configmap / secret / deployment / service
Dockerfile
```
