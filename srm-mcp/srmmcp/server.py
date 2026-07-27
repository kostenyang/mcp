#!/usr/bin/env python3
"""srm-mcp — MCP server for rtolab VCF 5.2.1 two-site SRM / vSphere Replication.

Wraps the VMware Live Site Recovery 9.0.2 REST surfaces (SRM v2 REST + appliance
config API) as MCP tools.

Safety model (mirrors the lab's "safe by design" convention):
  * Read-only tools are always available.
  * Destructive ACTION tools (run recovery plan, pair sites) refuse to run unless
    BOTH  SRM_ALLOW_ACTIONS=1  is set  AND  the call passes confirm=<target>.
    Even then they DRY-RUN (print the request they would send) unless execute=True.
  * MOCK by default — set SRM_LIVE=1 to hit real appliances.

Transport : streamable-HTTP on :8080 (path /mcp), k8s/VKS friendly.
Auth      : Bearer token(s) from env SRM_MCP_API_KEYS (comma-separated). If unset,
            auth is DISABLED with a loud warning (local dev only).
Health    : GET /healthz -> 200 (unauthenticated), for k8s liveness/readiness.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import ssl

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .client import SrmClient
from .config import actions_allowed, live_mode, load_config
from .vcenter import VCenterClient

# ── setup ─────────────────────────────────────────────────────────────────────
_cfg = load_config()
_client = SrmClient(_cfg)
_vc = VCenterClient(_cfg)

mcp = FastMCP(
    "srm-mcp",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def _j(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def _resolve_site(site: str) -> str:
    site = (site or "site1").lower()
    if site not in _cfg.sites:
        raise KeyError(f"unknown site {site!r}; known: {', '.join(_cfg.sites)}")
    return site


def _resolve_pairing(site: str, pairing_id: str = "") -> str:
    """Return a pairing_id — explicit if given, else the first from GET /pairings."""
    if pairing_id:
        return pairing_id
    res = _client.srm_rest(site, "GET", "/pairings")
    data = res.get("data", {})
    lst = data.get("list") if isinstance(data, dict) else None
    if lst:
        return lst[0].get("pairing_id") or lst[0].get("id")
    raise RuntimeError(
        "no pairing found — the two sites are not paired yet (rtolab: self-signed SRM "
        "leaf certs block cross-site trust). Recovery-plan operations require a pairing. "
        "Pass pairing_id once paired, or see rtolab/srm/README.md '配對卡點'."
    )


def _rm_base(pairing_id: str) -> str:
    return f"/pairings/{pairing_id}/recovery-management"


# ══════════════════════════════════════════════════════════════════════════════
# Read-only tools
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def list_srm_environment() -> str:
    """列出 SRM/VR 環境拓樸（兩站台端點、角色、runtime 模式）。無 secrets。

    Shows both sites (protected/recovery), their vCenter/VR/SRM endpoints, and the
    current runtime flags (live_mode / actions_allowed / config_path).
    """
    return _j(_cfg.summary())


@mcp.tool()
def get_ssl_thumbprint(host: str, port: int = 443) -> str:
    """回傳任一主機的 SHA-256 憑證指紋（冒號分隔）。

    Useful before probeSsl / pairing. Works for vCenter (443), SRM/VR appliance
    (443 or 5480). Example: get_ssl_thumbprint("192.168.114.47")
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
                digest = hashlib.sha256(der).hexdigest().upper()
                return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))
    except Exception as exc:
        return f"Error getting thumbprint from {host}:{port}: {exc}"


@mcp.tool()
def srm_appliance_summary(site: str = "site1", appliance: str = "srm") -> str:
    """SRM/VR appliance 的設定與健康摘要（config API getSummaryInfo）。

    site      : site1 (protected) | site2 (recovery)
    appliance : srm | vr
    Returns drConfiguration (siteName, trustedConnection, configured...).
    """
    try:
        return _j(_client.config_api(_resolve_site(site), "getSummaryInfo", {}, appliance))
    except Exception as exc:
        return f"srm_appliance_summary error: {exc}"


@mcp.tool()
def srm_list_pairings(site: str = "site1") -> str:
    """列出此站 SRM 的站台配對（GET /api/rest/srm/v2/pairings）。VERIFIED path.

    In rtolab this is currently empty (sites unpaired — self-signed SRM leaf certs
    block cross-site trust; see rtolab/srm/README.md).
    """
    try:
        return _j(_client.srm_rest(_resolve_site(site), "GET", "/pairings"))
    except Exception as exc:
        return f"srm_list_pairings error: {exc}"


@mcp.tool()
def srm_list_recovery_plans(site: str = "site1", pairing_id: str = "") -> str:
    """列出 recovery plans（GET /pairings/{pid}/recovery-management/plans）。

    pairing_id 留空 = 自動用第一個 pairing。未配對會回明確錯誤（rtolab 目前如此）。
    """
    try:
        site = _resolve_site(site)
        pid = _resolve_pairing(site, pairing_id)
        return _j(_client.srm_rest(site, "GET", f"{_rm_base(pid)}/plans"))
    except Exception as exc:
        return f"srm_list_recovery_plans error: {exc}"


@mcp.tool()
def srm_list_protection_groups(site: str = "site1", pairing_id: str = "") -> str:
    """列出 protection groups（GET /pairings/{pid}/replication-management/groups）。pairing_id 留空=自動。"""
    try:
        site = _resolve_site(site)
        pid = _resolve_pairing(site, pairing_id)
        return _j(_client.srm_rest(site, "GET", f"/pairings/{pid}/replication-management/groups"))
    except Exception as exc:
        return f"srm_list_protection_groups error: {exc}"


@mcp.tool()
def srm_recovery_plan_status(site: str, plan_id: str, pairing_id: str = "") -> str:
    """查 recovery plan 目前狀態（GET .../plans/{plan_id}）：state / 目前 mode / 步驟進度。read-only。

    典型 state：READY / TEST_IN_PROGRESS / TEST_COMPLETE / RECOVERY_IN_PROGRESS /
    RECOVERY_COMPLETE / CANCELLED。用來輪詢一個進行中的 test/failover。
    """
    try:
        site = _resolve_site(site)
        pid = _resolve_pairing(site, pairing_id)
        return _j(_client.srm_rest(site, "GET", f"{_rm_base(pid)}/plans/{plan_id}"))
    except Exception as exc:
        return f"srm_recovery_plan_status error: {exc}"


@mcp.tool()
def srm_recovery_plan_vms(site: str, plan_id: str, pairing_id: str = "") -> str:
    """查 recovery plan 內每台 VM 的復原狀態與開機狀況（GET .../plans/{plan_id}/vms）。read-only。

    這是「從 SRM 探知 VM 開機狀況」的直接來源：每台回 recovery_status + power_state。
    """
    try:
        site = _resolve_site(site)
        pid = _resolve_pairing(site, pairing_id)
        return _j(_client.srm_rest(site, "GET", f"{_rm_base(pid)}/plans/{plan_id}/vms"))
    except Exception as exc:
        return f"srm_recovery_plan_vms error: {exc}"


@mcp.tool()
def vr_list_replications(site: str = "site1") -> str:
    """列出 vSphere Replication 的複寫項目。BEST-EFFORT — VR dr-rest 端點未公開文件。"""
    try:
        return _j(_client.vr_api(_resolve_site(site), "GET", "/replications"))
    except Exception as exc:
        return f"vr_list_replications error: {exc}"


@mcp.tool()
def srm_api(site: str, method: str, path: str, body: str = "") -> str:
    """Generic passthrough to the SRM REST API (/api/rest/srm/{version} + <path>).

    Handles session auth (x-dr-session) automatically. Use to explore endpoints the
    typed tools don't cover yet. Version defaults to v1 (env SRM_API_VERSION).
    method : GET | POST | PUT | PATCH | DELETE
    path   : e.g. /pairings, /pairings/{pid}/recovery-management/plans/{id}
    body   : JSON string for write methods
    NOTE: POST/PUT/PATCH/DELETE here are NOT gated — use read methods unless you
          know the endpoint is safe. The typed action tools add confirm-gating.
    """
    try:
        payload = json.loads(body) if body.strip() else None
        return _j(_client.srm_rest(_resolve_site(site), method, path, payload))
    except Exception as exc:
        return f"srm_api error: {exc}"


@mcp.tool()
def srm_config_api(site: str, handler: str, body: str = "", appliance: str = "srm") -> str:
    """Generic passthrough to the appliance config API (:5480/configure/requestHandlers).

    handler   : login | probeSsl | listVcServices | configureAppliance |
                getSummaryInfo | addCaCertificate | ...
    appliance : srm | vr
    body      : JSON string (login is handled automatically; pass the handler body)
    """
    try:
        payload = json.loads(body) if body.strip() else {}
        return _j(_client.config_api(_resolve_site(site), handler, payload, appliance))
    except Exception as exc:
        return f"srm_config_api error: {exc}"


# ══════════════════════════════════════════════════════════════════════════════
# VM tools — inner vCenter of each site (site1=.96 / site2=.56)
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def vm_list(site: str = "site1", filter_name: str = "") -> str:
    """列出某站 inner vCenter 的 VM(名稱/ID/電源狀態)。read-only。

    site        : site1 (protected, vc .96) | site2 (recovery, vc .56)
    filter_name : 只列名稱含此字串的 VM(如 "srm-test")
    """
    try:
        return _j(_vc.vm_list(_resolve_site(site), filter_name))
    except Exception as exc:
        return f"vm_list error: {exc}"


@mcp.tool()
def vm_info(site: str, vm_id: str) -> str:
    """某 VM 的詳細資訊(GET /api/vcenter/vm/{id})。read-only。vm_id 從 vm_list 取得。"""
    try:
        return _j(_vc.vm_info(_resolve_site(site), vm_id))
    except Exception as exc:
        return f"vm_info error: {exc}"


@mcp.tool()
def vm_snapshot_list(site: str, vm_id: str) -> str:
    """列出某 VM 的快照。read-only。"""
    try:
        return _j(_vc.snapshot_list(_resolve_site(site), vm_id))
    except Exception as exc:
        return f"vm_snapshot_list error: {exc}"


@mcp.tool()
def vm_power(site: str, vm_id: str, action: str, confirm: str = "") -> str:
    """VM 電源管理(on/off/reset/suspend)。GATED：需 SRM_ALLOW_ACTIONS=1 + confirm=vm_id。

    action : on | off | reset | suspend
    """
    site = _resolve_site(site)
    if action not in ("on", "off", "reset", "suspend"):
        return "invalid action; use: on, off, reset, suspend"
    planned = {"site": site, "vm": vm_id, "action": action}
    gate = _gate_simple(vm_id, confirm, planned)
    if gate is not None:
        return gate
    try:
        return _j(_vc.vm_power(site, vm_id, action))
    except Exception as exc:
        return f"vm_power error: {exc}"


@mcp.tool()
def vm_snapshot(site: str, vm_id: str, action: str, snapshot_name: str = "",
                snapshot_id: str = "", memory: bool = False, confirm: str = "") -> str:
    """VM 快照 create/revert/delete。GATED：需 SRM_ALLOW_ACTIONS=1 + confirm=vm_id。

    action        : create | revert | delete   (list 用 vm_snapshot_list)
    snapshot_name : create 時用
    snapshot_id   : revert/delete 時用(如 snapshot-1)
    memory        : create 是否含記憶體(預設 false)
    """
    site = _resolve_site(site)
    if action not in ("create", "revert", "delete"):
        return "invalid action; use: create, revert, delete (list via vm_snapshot_list)"
    if action == "create" and not snapshot_name:
        return "snapshot_name required for create"
    if action in ("revert", "delete") and not snapshot_id:
        return f"snapshot_id required for {action}"
    planned = {"site": site, "vm": vm_id, "action": action,
               "name": snapshot_name, "id": snapshot_id}
    gate = _gate_simple(vm_id, confirm, planned)
    if gate is not None:
        return gate
    try:
        return _j(_vc.snapshot(site, vm_id, action, snapshot_name, snapshot_id, memory))
    except Exception as exc:
        return f"vm_snapshot error: {exc}"


# ══════════════════════════════════════════════════════════════════════════════
# Action tools (gated: SRM_ALLOW_ACTIONS=1 + confirm=<target> + execute=True)
# ══════════════════════════════════════════════════════════════════════════════

_RECOVERY_MODES = ("test", "cleanup", "recovery", "reprotect", "cancel")
# mode -> (action segment, request body)
_MODE_SPEC = {
    "test":      ("test",      {"sync_data": False}),
    "cleanup":   ("cleanup",   {}),
    "recovery":  ("recovery",  {"planned_failover": False}),
    "reprotect": ("reprotect", {}),
    "cancel":    ("cancel",    {}),
}


def _gate_simple(target: str, confirm: str, planned: dict) -> str | None:
    """Lighter gate for VM mutations: SRM_ALLOW_ACTIONS + confirm match (no dry-run)."""
    if not actions_allowed():
        return _j({"refused": True, "reason": "actions disabled",
                   "fix": "set SRM_ALLOW_ACTIONS=1 to enable VM power/snapshot writes",
                   "would_have_done": planned})
    if confirm != target:
        return _j({"refused": True, "reason": "confirm mismatch",
                   "need": f'pass confirm="{target}" to authorise this action',
                   "would_have_done": planned})
    return None


def _gate(target: str, confirm: str, execute: bool, planned: dict) -> str | None:
    """Return a refusal/dry-run string if the action must not proceed, else None."""
    if not actions_allowed():
        return _j({"refused": True, "reason": "actions disabled",
                   "fix": "set SRM_ALLOW_ACTIONS=1 to enable destructive tools",
                   "would_have_done": planned})
    if confirm != target:
        return _j({"refused": True, "reason": "confirm mismatch",
                   "need": f'pass confirm="{target}" to authorise this action',
                   "would_have_done": planned})
    if not execute:
        return _j({"dry_run": True, "note": "pass execute=True to actually send",
                   "would_send": planned})
    return None


@mcp.tool()
def srm_run_recovery_plan(site: str, plan_id: str, mode: str, pairing_id: str = "",
                          confirm: str = "", execute: bool = False) -> str:
    """觸發 recovery plan（破壞性）。DEFAULT-OFF：需 SRM_ALLOW_ACTIONS=1 + confirm=plan_id + execute=True。

    mode : test | cleanup | recovery | reprotect | cancel
      - test     : 非破壞性測試復原（建 test VMs on isolated net）
      - cleanup  : 清掉 test 產物
      - recovery : 真正 failover（破壞性；轉移到 recovery site）
      - reprotect: 反向保護
      - cancel   : 取消進行中的 run
    Path: POST /pairings/{pid}/recovery-management/plans/{plan_id}/actions/{seg} (v1).
    只送出觸發、不等待；用 srm_recovery_plan_status/_vms 或 srm_failover_and_watch 觀察。
    """
    site = _resolve_site(site)
    if mode not in _MODE_SPEC:
        return f"invalid mode {mode!r}; use: {', '.join(_RECOVERY_MODES)}"
    seg, mbody = _MODE_SPEC[mode]
    try:
        pid = _resolve_pairing(site, pairing_id)
    except Exception as exc:
        return f"srm_run_recovery_plan error: {exc}"
    path = f"{_rm_base(pid)}/plans/{plan_id}/actions/{seg}"
    planned = {"site": site, "method": "POST", "path": path, "mode": mode, "body": mbody}
    gate = _gate(plan_id, confirm, execute, planned)
    if gate is not None:
        return gate
    try:
        return _j(_client.srm_rest(site, "POST", path, mbody))
    except Exception as exc:
        return f"srm_run_recovery_plan error: {exc}"


@mcp.tool()
def srm_failover_and_watch(site: str, plan_id: str, mode: str = "test",
                           recovery_site: str = "site2", pairing_id: str = "",
                           poll_seconds: int = 60, confirm: str = "", execute: bool = False) -> str:
    """一鍵：觸發 test/recovery → 輪詢 plan 狀態 → 回報每台 VM 開機狀況（SRM + recovery-site vCenter）。

    這就是「由 MCP 觸發 recovery group 轉移/測試，並探知 VM 開機狀況」的高階工具。
    mode          : test（預設，安全）| recovery（真 failover，破壞性）
    recovery_site : 復原端站台（預設 site2），用來交叉比對 vCenter 的 VM 電源
    poll_seconds  : 觸發後輪詢多久（上限 120s，每 ~10s 一次）；不等於整個復原完成，
                    只回這段時間內觀察到的狀態變化。要繼續看就再呼叫本工具或 status/_vms。
    DEFAULT-OFF：需 SRM_ALLOW_ACTIONS=1 + confirm=plan_id + execute=True。
    """
    site = _resolve_site(site)
    if mode not in ("test", "recovery"):
        return "mode must be 'test' or 'recovery' for this workflow tool"
    seg, mbody = _MODE_SPEC[mode]
    try:
        pid = _resolve_pairing(site, pairing_id)
        rsite = _resolve_site(recovery_site)
    except Exception as exc:
        return f"srm_failover_and_watch error: {exc}"
    path = f"{_rm_base(pid)}/plans/{plan_id}/actions/{seg}"
    planned = {"trigger": {"site": site, "method": "POST", "path": path, "body": mbody},
               "then": f"poll status up to {poll_seconds}s + read plan VMs + vCenter {rsite} power"}
    gate = _gate(plan_id, confirm, execute, planned)
    if gate is not None:
        return gate

    import time
    timeline = []
    try:
        trig = _client.srm_rest(site, "POST", path, mbody)
        timeline.append({"t": 0, "event": "trigger", "resp": trig.get("data")})
        deadline = min(max(int(poll_seconds), 0), 120)
        elapsed, interval = 0, 10
        last_state = None
        while elapsed < deadline:
            time.sleep(min(interval, deadline - elapsed))
            elapsed += interval
            st = _client.srm_rest(site, "GET", f"{_rm_base(pid)}/plans/{plan_id}")
            state = (st.get("data") or {}).get("state") if isinstance(st.get("data"), dict) else None
            vms = _client.srm_rest(site, "GET", f"{_rm_base(pid)}/plans/{plan_id}/vms")
            timeline.append({"t": elapsed, "state": state,
                             "vms": (vms.get("data") or {}).get("vms")})
            last_state = state
            if state in ("TEST_COMPLETE", "RECOVERY_COMPLETE", "CANCELLED", "ERROR"):
                break
        # cross-check the recovery-site vCenter power state
        vcpower = _vc.vm_list(rsite, "srm-test")
        return _j({"plan_id": plan_id, "mode": mode, "final_state": last_state,
                   "timeline": timeline,
                   "recovery_site_vcenter": (vcpower.get("data") or {}).get("vms")})
    except Exception as exc:
        return _j({"error": f"srm_failover_and_watch: {exc}", "timeline": timeline})


@mcp.tool()
def srm_pair_sites(local_site: str = "site1", remote_site: str = "site2",
                   confirm: str = "", execute: bool = False) -> str:
    """建立兩站 SRM 配對（PairingSpec）。DEFAULT-OFF：需 SRM_ALLOW_ACTIONS=1 + confirm=remote_site + execute=True。

    ⚠️ KNOWN BLOCKER (rtolab): SRM appliance leaf certs are self-signed (not VMCA),
    so this currently fails with ProbeServicesException (cert verify). This tool
    builds the PairingSpec and reports the failure clearly — it does NOT fix the
    cert-trust wall. See rtolab/srm/README.md "配對卡點".
    """
    local_site = _resolve_site(local_site)
    remote_site = _resolve_site(remote_site)
    remote = _cfg.site(remote_site)
    spec = {
        "pair_psc_info": {
            "url": remote["vcenter"]["fqdn"], "port": 443,
            "thumbprint": "<remote vc SHA-256>",
            "username": _cfg.sso_user, "password": "<from env>",
        },
        "pair_vc_guid": remote["vcenter"]["instance_uuid"],
        "pair_srm_url": f"https://{remote['srm']['fqdn']}:443",
        "pair_srm_thumbprint": "<remote srm SHA-256>",
        "description": f"{local_site} <-> {remote_site}",
    }
    planned = {"site": local_site, "method": "POST", "path": "/pairings", "PairingSpec": spec}
    gate = _gate(remote_site, confirm, execute, planned)
    if gate is not None:
        return gate
    try:
        return _j(_client.srm_rest(local_site, "POST", "/pairings", spec))
    except Exception as exc:
        return f"srm_pair_sites error: {exc}"


# ══════════════════════════════════════════════════════════════════════════════
# HTTP wrapper: /healthz (open) + Bearer auth on everything else
# ══════════════════════════════════════════════════════════════════════════════

def _load_api_keys() -> frozenset[str]:
    raw = os.getenv("SRM_MCP_API_KEYS", "").strip()
    keys = frozenset(k.strip() for k in raw.split(",") if k.strip())
    return keys


class Gateway:
    """ASGI middleware: open /healthz, Bearer-guard the rest."""

    def __init__(self, app):
        self.app = app
        self.keys = _load_api_keys()
        if not self.keys:
            print("⚠️  SRM_MCP_API_KEYS unset — AUTH DISABLED (local dev only!)")

    def _token(self, scope) -> str:
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        auth = headers.get(b"authorization", b"").decode()
        return auth[7:].strip() if auth.startswith("Bearer ") else ""

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "") == "/healthz":
            await send({"type": "http.response.start", "status": 200,
                        "headers": [[b"content-type", b"text/plain"]]})
            await send({"type": "http.response.body", "body": b"ok"})
            return
        if scope["type"] in ("http", "websocket") and self.keys:
            if self._token(scope) not in self.keys:
                await send({"type": "http.response.start", "status": 401,
                            "headers": [[b"content-type", b"application/json"],
                                        [b"www-authenticate", b'Bearer realm="srm-mcp"']]})
                await send({"type": "http.response.body", "body": b'{"error":"Unauthorized"}'})
                return
        await self.app(scope, receive, send)


def main():
    import sys
    mode = "LIVE" if live_mode() else "MOCK"
    acts = "ENABLED" if actions_allowed() else "disabled"
    transport = os.getenv("MCP_TRANSPORT", "http").lower()

    if transport == "stdio":
        # stdio: stdout carries the JSON-RPC stream — banner MUST go to stderr.
        print(f"srm-mcp stdio — mode={mode}, actions={acts}, sites={list(_cfg.sites)}",
              file=sys.stderr, flush=True)
        mcp.run(transport="stdio")
        return

    print(f"srm-mcp starting — mode={mode}, actions={acts}, sites={list(_cfg.sites)}")
    print("Transport: streamable-HTTP on http://0.0.0.0:8080/mcp  (health: /healthz)")
    app = Gateway(mcp.streamable_http_app())
    uvicorn.run(app, host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
