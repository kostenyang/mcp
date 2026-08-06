"""Inner-vCenter client for the two SRM sites (vSphere 8.0 U3).

Site-oriented: site1 -> 192.168.114.96, site2 -> 192.168.114.56. Auth is vSphere 8
(POST /api/session, Basic -> bare token; header vmware-api-session-id). Reuses the
same SSO creds as the SRM config (administrator@vsphere.local / SRM_SSO_PASS).

MOCK by default (SRM_LIVE unset) — fixtures come from mock.py so VM tools work with
no lab access.
"""

from __future__ import annotations

import json

import requests
import urllib3

from . import mock
from .config import SrmConfig, live_mode

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_TIMEOUT = 30


class VCenterClient:
    def __init__(self, cfg: SrmConfig):
        self.cfg = cfg

    def _host(self, site: str) -> str:
        vc = self.cfg.site(site)["vcenter"]
        return vc.get("ip") or vc.get("fqdn")

    def _token(self, host: str) -> str:
        r = requests.post(f"https://{host}/api/session",
                          auth=(self.cfg.sso_user, self.cfg.sso_pass),
                          verify=False, timeout=_TIMEOUT)
        r.raise_for_status()
        tok = r.json()
        return tok if isinstance(tok, str) else tok.get("value", str(tok))

    def _req(self, method: str, host: str, path: str, token: str,
             params: dict | None = None, body: dict | None = None) -> dict:
        r = requests.request(
            method.upper(), f"https://{host}{path}",
            headers={"vmware-api-session-id": token, "Content-Type": "application/json"},
            params=params, data=json.dumps(body) if body is not None else None,
            verify=False, timeout=_TIMEOUT,
        )
        try:
            payload = r.json()
        except ValueError:
            payload = r.text[:6000]
        return {"status": r.status_code, "ok": r.ok, "data": payload}

    # ── read ─────────────────────────────────────────────────────────────────
    def vm_list(self, site: str, filter_name: str = "") -> dict:
        if not live_mode():
            return mock.vm_list(site, filter_name)
        host = self._host(site)
        tok = self._token(host)
        res = self._req("GET", host, "/api/vcenter/vm", tok)
        if res["ok"] and isinstance(res["data"], list) and filter_name:
            res["data"] = [v for v in res["data"]
                           if filter_name.lower() in v.get("name", "").lower()]
        return res

    def vm_info(self, site: str, vm_id: str) -> dict:
        if not live_mode():
            return mock.vm_info(site, vm_id)
        host = self._host(site)
        return self._req("GET", host, f"/api/vcenter/vm/{vm_id}", self._token(host))

    def snapshot_list(self, site: str, vm_id: str) -> dict:
        if not live_mode():
            return mock.snapshot_list(site, vm_id)
        host = self._host(site)
        return self._req("GET", host, f"/api/vcenter/vm/{vm_id}/snapshots", self._token(host))

    # ── mutate ───────────────────────────────────────────────────────────────
    def vm_power(self, site: str, vm_id: str, action: str) -> dict:
        verb = {"on": "start", "off": "stop", "reset": "reset", "suspend": "suspend"}[action]
        if not live_mode():
            return mock.vm_power(site, vm_id, action)
        host = self._host(site)
        return self._req("POST", host, f"/api/vcenter/vm/{vm_id}/power",
                         self._token(host), params={"action": verb})

    def snapshot(self, site: str, vm_id: str, action: str,
                 snapshot_name: str = "", snapshot_id: str = "", memory: bool = False) -> dict:
        if not live_mode():
            return mock.snapshot(site, vm_id, action, snapshot_name, snapshot_id)
        host = self._host(site)
        tok = self._token(host)
        base = f"/api/vcenter/vm/{vm_id}/snapshots"
        if action == "create":
            return self._req("POST", host, base, tok,
                             body={"name": snapshot_name, "memory": memory, "quiesce": False})
        if action == "revert":
            return self._req("POST", host, f"{base}/{snapshot_id}?action=revert", tok)
        if action == "delete":
            return self._req("DELETE", host, f"{base}/{snapshot_id}", tok)
        return {"status": 400, "ok": False, "data": f"invalid snapshot action {action!r}"}
