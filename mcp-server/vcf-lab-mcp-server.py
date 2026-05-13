#!/usr/bin/env python3
"""
VCF Lab MCP Server
Runs on Ubuntu 10.0.0.65, exposes tools for Claude to interact with the VCF9 lab.

Tools:
  ssh_exec              - SSH into any lab machine and run a command
  get_ssl_thumbprint    - Get SHA-256 SSL cert thumbprint from ESXi / vCenter
  vcenter_api           - vCenter REST API calls
  vcf_installer_api     - VCF Installer REST API calls (bringup, status, bundles)
  sddc_manager_api      - SDDC Manager REST API calls
  vrops_api             - vRealize Operations (Aria Operations) REST API calls
  check_dns             - Resolve FQDN via lab DNS server
  ping_host             - Ping a host
  vcf_version           - Detect VCF component version (SDDC / Installer / vCenter)
  list_environments     - List known lab environments (M01 / M02 / M03 / VCF5.x)

Start:  python3 vcf-lab-mcp-server.py
Port:   https://0.0.0.0:7000/sse

Required environment variables (set via systemd EnvironmentFile or shell):
  VCENTER_PASS, VCF_INSTALLER_PASS, SDDC_MANAGER_PASS, VROPS_PASS, DEFAULT_SSH_PASS
If unset, tool calls that need them will fail at auth time (401 from upstream).

API keys for clients are loaded from /opt/vcf-mcp/keys.json (chmod 600).
Generate a new key:  python3 -c "import secrets; print(secrets.token_urlsafe(32))"
"""

import hashlib
import json
import os
import pathlib
import socket
import ssl
import subprocess

import paramiko
import requests
import urllib3
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Lab defaults (override via tool arguments if needed) ─────────────────────
# IPs / usernames are lab-internal and fine to live in source.
# Passwords MUST come from env vars — never commit secrets.
VCENTER_IP   = "10.0.0.101"
VCENTER_USER = "administrator@vsphere.local"
VCENTER_PASS = os.getenv("VCENTER_PASS", "")

VCF_INSTALLER_IP   = "10.0.1.4"
VCF_INSTALLER_USER = "admin@local"
VCF_INSTALLER_PASS = os.getenv("VCF_INSTALLER_PASS", "")

SDDC_MANAGER_IP   = "10.0.1.5"
SDDC_MANAGER_USER = "administrator@vsphere.local"
SDDC_MANAGER_PASS = os.getenv("SDDC_MANAGER_PASS", "")

VROPS_IP   = "10.0.0.111"
VROPS_USER = "admin"
VROPS_PASS = os.getenv("VROPS_PASS", "")

DEFAULT_SSH_PASS = os.getenv("DEFAULT_SSH_PASS", "")

DNS_SERVER = "10.0.0.200"

# ── Lab environment map (M01 / M02 / M03 — 9.0 與 9.1 共用 IP，差別只在已部署的版本)
LAB_ENVIRONMENTS = {
    "M01": {
        "purpose": "Main VCF 9 management domain (9.0 by default, upgrade-able to 9.1)",
        "vcenter_fqdn":     "vcf-m01-vc01.home.lab",
        "vcenter_ip":       "10.0.1.9",
        "sddc_manager_fqdn":"vcf-m01-sddcm01.home.lab",
        "sddc_manager_ip":  "10.0.1.5",
        "vcf_installer_ip": "10.0.1.4",
        "esxi_hosts":       ["10.0.1.10", "10.0.1.11", "10.0.1.12", "10.0.1.13"],
        "deploy_script_90": "autodeployvcf9m01.ps1",
        "deploy_script_91": "autodeployvcf91m01.ps1",
    },
    "M02": {
        "purpose": "Secondary VCF 9 management domain",
        "sddc_manager_ip":  "10.0.1.18",
        "vcf_installer_ip": "10.0.1.4",
        "esxi_hosts":       ["10.0.1.14", "10.0.1.15", "10.0.1.16", "10.0.1.17"],
        "deploy_script_90": "autodeployvcf9m02.ps1",
        "deploy_script_91": "autodeployvcf91m02.ps1",
    },
    "M03": {
        "purpose": "Tertiary VCF 9 management domain",
        "sddc_manager_ip":  "10.0.1.56",
        "vcf_installer_ip": "10.0.1.4",
        "esxi_hosts":       ["10.0.1.50", "10.0.1.51", "10.0.1.52", "10.0.1.53"],
        "deploy_script_90": "autodeployvcf9m03.ps1",
        "deploy_script_91": "autodeployvcf91m03.ps1",
    },
    "VCF5-M02": {
        "purpose": "Legacy VCF 5.x workload domain (different deploy path)",
        "esxi_hosts":       ["10.0.1.14", "10.0.1.15", "10.0.1.16", "10.0.1.17"],
        "deploy_script":    "autodeployvcfm02.ps1",
        "note":             "ESXi IP overlaps with VCF9 M02 — only one can be active",
    },
}

SSL_CERTFILE = "/opt/vcf-mcp/cert.pem"
SSL_KEYFILE  = "/opt/vcf-mcp/key.pem"

# ── API Keys — 從 /opt/vcf-mcp/keys.json 載入（不寫死在 source 裡）─────────────
# 格式: {"使用者名稱": "Bearer token", ...}
# 產生新 key: python3 -c "import secrets; print(secrets.token_urlsafe(32))"
API_KEYS_FILE = pathlib.Path("/opt/vcf-mcp/keys.json")


def _load_api_keys() -> dict[str, str]:
    if not API_KEYS_FILE.exists():
        raise RuntimeError(f"API keys file not found: {API_KEYS_FILE}")
    with API_KEYS_FILE.open() as f:
        keys = json.load(f)
    if not isinstance(keys, dict) or not keys:
        raise RuntimeError("Invalid keys file: must be non-empty JSON object")
    return keys


API_KEYS: dict[str, str] = _load_api_keys()

mcp = FastMCP(
    "vcf-lab",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)


# ── API Key Middleware (pure ASGI, SSE-safe) ──────────────────────────────────
class APIKeyMiddleware:
    def __init__(self, app):
        self.app = app
        self._VALID = frozenset(API_KEYS.values())

    def _extract_token(self, scope) -> str:
        # 1. Authorization: Bearer <token> header
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        auth = headers.get(b"authorization", b"").decode()
        if auth.startswith("Bearer "):
            return auth[7:].strip()
        # 2. ?api_key=<token> query string (legacy — header preferred)
        qs = scope.get("query_string", b"").decode()
        for part in qs.split("&"):
            if part.startswith("api_key="):
                return part[8:]
        return ""

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            if self._extract_token(scope) not in self._VALID:
                await send({
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        [b"content-type", b"application/json"],
                        [b"www-authenticate", b'Bearer realm="vcf-lab"'],
                    ],
                })
                await send({"type": "http.response.body",
                            "body": b'{"error":"Unauthorized"}'})
                return
        await self.app(scope, receive, send)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _vcenter_token(ip=VCENTER_IP, user=VCENTER_USER, pwd=None) -> str:
    pwd = pwd or VCENTER_PASS
    resp = requests.post(f"https://{ip}/api/session", auth=(user, pwd), verify=False, timeout=10)
    resp.raise_for_status()
    return resp.json()

def _vcf_token(ip=VCF_INSTALLER_IP, user=VCF_INSTALLER_USER, pwd=None) -> str:
    pwd = pwd or VCF_INSTALLER_PASS
    resp = requests.post(
        f"https://{ip}/v1/tokens",
        json={"username": user, "password": pwd},
        verify=False, timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("accessToken", "")

def _sddc_token(ip=SDDC_MANAGER_IP, user=SDDC_MANAGER_USER, pwd=None) -> str:
    pwd = pwd or SDDC_MANAGER_PASS
    resp = requests.post(
        f"https://{ip}/v1/tokens",
        json={"username": user, "password": pwd},
        verify=False, timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("accessToken", "")

def _vrops_token(ip=VROPS_IP, user=VROPS_USER, pwd=None) -> str:
    pwd = pwd or VROPS_PASS
    resp = requests.post(
        f"https://{ip}/suite-api/api/auth/token/acquire",
        json={"username": user, "password": pwd, "authSource": "Local"},
        headers={"Accept": "application/json"},
        verify=False, timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("token", "")

# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def ssh_exec(host: str, command: str, username: str = "root", password: str = "") -> str:
    """
    Execute a shell command on any lab host via SSH and return stdout+stderr.
    Works for ESXi hosts, SDDC Manager, VCF Installer, etc.

    If password is empty, falls back to env var DEFAULT_SSH_PASS.
    Example: ssh_exec("10.0.1.10", "esxcli system version get")
    """
    password = password or DEFAULT_SSH_PASS
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, username=username, password=password, timeout=30,
                       look_for_keys=False, allow_agent=False)
        _, stdout, stderr = client.exec_command(command, timeout=60)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        return (out + err).strip() or "(no output)"
    except Exception as exc:
        return f"SSH error connecting to {host}: {exc}"
    finally:
        client.close()


@mcp.tool()
def get_ssl_thumbprint(host: str, port: int = 443) -> str:
    """
    Return the SHA-256 SSL certificate thumbprint (colon-separated) for any host.
    Useful for getting ESXi thumbprints before VCF bringup.
    Example: get_ssl_thumbprint("10.0.1.10")
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
                digest = hashlib.sha256(der).hexdigest().upper()
                return ":".join(digest[i:i+2] for i in range(0, len(digest), 2))
    except Exception as exc:
        return f"Error getting thumbprint from {host}:{port}: {exc}"


@mcp.tool()
def vcenter_api(
    method: str,
    path: str,
    body: str = "",
    vcenter_ip: str = VCENTER_IP,
    username: str = VCENTER_USER,
    password: str = "",
) -> str:
    """
    Make a vCenter REST API call.
    method : GET | POST | PUT | PATCH | DELETE
    path   : e.g. /api/vcenter/cluster, /api/vcenter/host
    body   : JSON string for POST/PUT/PATCH (leave empty for GET/DELETE)

    If password is empty, falls back to env var VCENTER_PASS.

    Examples:
      vcenter_api("GET", "/api/vcenter/cluster")
      vcenter_api("GET", "/api/vcenter/host")
      vcenter_api("GET", "/api/vcenter/datastore")
    """
    try:
        token = _vcenter_token(vcenter_ip, username, password or VCENTER_PASS)
        headers = {"vmware-api-session-id": token, "Content-Type": "application/json"}
        resp = requests.request(
            method.upper(), f"https://{vcenter_ip}{path}",
            headers=headers, data=body or None, verify=False, timeout=30,
        )
        return f"HTTP {resp.status_code}\n{resp.text[:6000]}"
    except Exception as exc:
        return f"vCenter API error: {exc}"


@mcp.tool()
def vcf_installer_api(
    method: str,
    path: str,
    body: str = "",
    installer_ip: str = VCF_INSTALLER_IP,
    username: str = VCF_INSTALLER_USER,
    password: str = "",
) -> str:
    """
    Make a VCF Installer REST API call.
    method : GET | POST | PUT | PATCH | DELETE
    path   : e.g. /v1/system/appliance-info, /v1/sddcs, /v1/bundles/download-status

    If password is empty, falls back to env var VCF_INSTALLER_PASS.

    Common use cases:
      vcf_installer_api("GET", "/v1/system/appliance-info")        # check ready
      vcf_installer_api("GET", "/v1/sddcs")                        # deployment status
      vcf_installer_api("POST", "/v1/sddcs", body=<json_string>)   # start bringup
      vcf_installer_api("GET", "/v1/system/settings/depot")        # depot config
    """
    try:
        token = _vcf_token(installer_ip, username, password or VCF_INSTALLER_PASS)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        resp = requests.request(
            method.upper(), f"https://{installer_ip}{path}",
            headers=headers, data=body or None, verify=False, timeout=30,
        )
        return f"HTTP {resp.status_code}\n{resp.text[:6000]}"
    except Exception as exc:
        return f"VCF Installer API error: {exc}"


@mcp.tool()
def sddc_manager_api(
    method: str,
    path: str,
    body: str = "",
    sddc_ip: str = SDDC_MANAGER_IP,
    username: str = SDDC_MANAGER_USER,
    password: str = "",
) -> str:
    """
    Make an SDDC Manager REST API call.
    method : GET | POST | PUT | PATCH | DELETE
    path   : e.g. /v1/domains, /v1/hosts, /v1/clusters, /v1/tasks

    If password is empty, falls back to env var SDDC_MANAGER_PASS.

    Examples:
      sddc_manager_api("GET", "/v1/domains")
      sddc_manager_api("GET", "/v1/hosts?status=UNASSIGNED_USEABLE")
      sddc_manager_api("GET", "/v1/tasks?status=Failed")
    """
    try:
        token = _sddc_token(sddc_ip, username, password or SDDC_MANAGER_PASS)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        resp = requests.request(
            method.upper(), f"https://{sddc_ip}{path}",
            headers=headers, data=body or None, verify=False, timeout=30,
        )
        return f"HTTP {resp.status_code}\n{resp.text[:6000]}"
    except Exception as exc:
        return f"SDDC Manager API error: {exc}"


@mcp.tool()
def vrops_api(
    method: str,
    path: str,
    body: str = "",
    vrops_ip: str = VROPS_IP,
    username: str = VROPS_USER,
    password: str = "",
) -> str:
    """
    Make a vRealize Operations (Aria Operations) REST API call.
    method : GET | POST | PUT | PATCH | DELETE
    path   : e.g. /suite-api/api/resources, /suite-api/api/alerts

    If password is empty, falls back to env var VROPS_PASS.

    Common use cases:
      vrops_api("GET", "/suite-api/api/versions/current")
      vrops_api("GET", "/suite-api/api/resources?resourceKind=VirtualMachine")
      vrops_api("GET", "/suite-api/api/alerts?activeOnly=true")
      vrops_api("GET", "/suite-api/api/adapters")
    """
    try:
        token = _vrops_token(vrops_ip, username, password or VROPS_PASS)
        headers = {
            "Authorization": f"vRealizeOpsToken {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        resp = requests.request(
            method.upper(), f"https://{vrops_ip}{path}",
            headers=headers, data=body or None, verify=False, timeout=30,
        )
        return f"HTTP {resp.status_code}\n{resp.text[:6000]}"
    except Exception as exc:
        return f"vROps API error: {exc}"


@mcp.tool()
def check_dns(fqdn: str, dns_server: str = DNS_SERVER) -> str:
    """
    Resolve an FQDN using the lab DNS server (defaults to DNS_SERVER).
    Useful for verifying DNS records before VCF bringup.
    Example: check_dns("vcf-m01-vc01.home.lab")
    """
    try:
        result = subprocess.run(
            ["nslookup", fqdn, dns_server],
            capture_output=True, text=True, timeout=10,
        )
        return (result.stdout + result.stderr).strip()
    except Exception as exc:
        return f"DNS check error: {exc}"


@mcp.tool()
def ping_host(host: str, count: int = 3) -> str:
    """
    Ping a lab host to check network reachability.
    Example: ping_host("10.0.1.10")
    """
    try:
        result = subprocess.run(
            ["ping", "-c", str(count), host],
            capture_output=True, text=True, timeout=20,
        )
        return (result.stdout + result.stderr).strip()
    except Exception as exc:
        return f"Ping error: {exc}"


@mcp.tool()
def vcf_version(
    host: str,
    host_type: str = "auto",
    username: str = "",
    password: str = "",
) -> str:
    """
    偵測 VCF 元件版本。Claude 可用此判斷某環境是 VCF 9.0 還是 9.1。

    host_type: "auto" / "sddc" / "installer" / "vcenter"
      auto: 自動依序嘗試 SDDC Manager → VCF Installer → vCenter
    username/password: 留空時使用該元件的 lab 預設帳密 (env var)

    Examples:
      vcf_version("10.0.1.5")                  # 自動偵測 SDDC Mgr 版本
      vcf_version("10.0.1.4", "installer")     # 強制查 VCF Installer
      vcf_version("10.0.0.101", "vcenter")     # vCenter 版本
    """
    errors = []

    def _try_sddc():
        u = username or SDDC_MANAGER_USER
        p = password or SDDC_MANAGER_PASS
        try:
            t = _sddc_token(host, u, p)
            r = requests.get(f"https://{host}/v1/manifest",
                             headers={"Authorization": f"Bearer {t}"},
                             verify=False, timeout=10)
            if r.ok:
                d = r.json()
                v = d.get("vcfVersion") or d.get("releaseVersion") or "unknown"
                return f"SDDC Manager: VCF {v}"
        except Exception as e:
            errors.append(f"  sddc: {e.__class__.__name__}: {str(e)[:80]}")
        return None

    def _try_installer():
        try:
            r = requests.get(f"https://{host}/v1/system/appliance-info",
                             verify=False, timeout=10)
            if r.ok:
                d = r.json()
                v = d.get("version") or d.get("buildNumber") or d
                return f"VCF Installer: {v}"
        except Exception as e:
            errors.append(f"  installer: {e.__class__.__name__}: {str(e)[:80]}")
        return None

    def _try_vcenter():
        u = username or VCENTER_USER
        p = password or VCENTER_PASS
        try:
            r = requests.get(f"https://{host}/api/appliance/system/version",
                             auth=(u, p), verify=False, timeout=10)
            if r.ok:
                d = r.json()
                v = d.get("version", "unknown")
                build = d.get("build", "")
                return f"vCenter {v} (build {build})"
        except Exception as e:
            errors.append(f"  vcenter: {e.__class__.__name__}: {str(e)[:80]}")
        return None

    probes = {"sddc": _try_sddc, "installer": _try_installer, "vcenter": _try_vcenter}
    if host_type == "auto":
        for fn in probes.values():
            out = fn()
            if out:
                return out
        return f"Could not detect version at {host}\nProbes tried:\n" + "\n".join(errors)

    if host_type not in probes:
        return f"Invalid host_type: {host_type}. Use auto/sddc/installer/vcenter."
    out = probes[host_type]()
    return out or f"Could not detect {host_type} version at {host}\n" + "\n".join(errors)


@mcp.tool()
def list_environments(probe: bool = False) -> str:
    """
    列出 lab 內所有已知的 VCF 環境（M01 / M02 / M03 / VCF5.x M02）。

    probe: True 時會額外 TCP-probe 每個 SDDC Manager / VCF Installer / vCenter
           並嘗試偵測版本（速度較慢，需要連線到 lab）。

    回傳 JSON 字串，方便 Claude parse。
    """
    env = json.loads(json.dumps(LAB_ENVIRONMENTS))
    if not probe:
        return json.dumps(env, indent=2, ensure_ascii=False)

    for name, cfg in env.items():
        cfg["_live"] = {}
        for key in ("sddc_manager_ip", "vcf_installer_ip", "vcenter_ip"):
            ip = cfg.get(key)
            if not ip:
                continue
            try:
                with socket.create_connection((ip, 443), timeout=2):
                    pass
                cfg["_live"][key] = {"reachable": True}
                kind = ("sddc" if "sddc_manager" in key
                        else "installer" if "installer" in key
                        else "vcenter")
                try:
                    cfg["_live"][key]["version"] = vcf_version(ip, kind)
                except Exception:
                    pass
            except OSError:
                cfg["_live"][key] = {"reachable": False}
    return json.dumps(env, indent=2, ensure_ascii=False)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Starting VCF Lab MCP Server on https://0.0.0.0:7000/sse")
    print(f"API keys loaded: {list(API_KEYS.keys())}")
    uvicorn.run(
        APIKeyMiddleware(mcp.sse_app()),
        host="0.0.0.0",
        port=7000,
        ssl_keyfile=SSL_KEYFILE,
        ssl_certfile=SSL_CERTFILE,
    )
