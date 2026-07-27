"""HTTP client for the SRM / vSphere Replication appliances.

Two REST surfaces, both verified against rtolab appliances (see rtolab/srm/README.md):

1. Appliance CONFIG API  —  https://<ip>:5480/configure/requestHandlers/<handler>
   - POST login {"username","password"} -> data.sessionId (value INCLUDES quotes)
   - every later call carries header  dr.config.service.sessionid: "<40hex>"  (quotes kept)
   - handlers: probeSsl, listVcServices, configureAppliance, getSummaryInfo,
     addCaCertificate, ...

2. SRM v2 REST API  —  https://<srm>/api/rest/srm/v2/<path>
   - POST /session with Basic auth -> session id
   - every later call carries header  x-dr-session: <id>
   - paths verified: /session, /pairings. Others are best-effort (calibrate on LIVE).

In MOCK mode (default; SRM_LIVE unset) nothing touches the network — canned fixtures
from mock.py are returned so the server runs on any Linux box with no lab access.
"""

from __future__ import annotations

import json

import requests
import urllib3

from . import mock
from .config import SrmConfig, live_mode

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_TIMEOUT = 40


class SrmError(RuntimeError):
    pass


class SrmClient:
    def __init__(self, cfg: SrmConfig):
        self.cfg = cfg
        # SRM/Live Site Recovery REST version. recovery-management is documented
        # under v1; session/pairings also answer on v1. Override with SRM_API_VERSION.
        import os
        self.api_version = os.getenv("SRM_API_VERSION", "v1")

    # ── appliance CONFIG API (:5480) ─────────────────────────────────────────
    def _config_base(self, host: str) -> str:
        return f"https://{host}:5480/configure/requestHandlers"

    def _config_login(self, host: str) -> str:
        """Return the 40-hex session id (without quotes)."""
        r = requests.post(
            f"{self._config_base(host)}/login",
            json={"username": self.cfg.appliance_user, "password": self.cfg.appliance_pass},
            verify=False, timeout=_TIMEOUT,
        )
        r.raise_for_status()
        # sessionId value comes back quoted inside data; grab the first 40-hex run.
        import re
        m = re.search(r"[0-9a-f]{40}", r.text)
        if not m:
            raise SrmError(f"config login: no session id in response: {r.text[:200]}")
        return m.group(0)

    def config_api(self, site: str, handler: str, body: dict | None = None,
                   appliance: str = "srm") -> dict:
        """Call a config-API request handler on the SRM (or VR) appliance :5480.

        appliance: "srm" or "vr" — selects which appliance of the site to hit.
        """
        if not live_mode():
            return mock.config_api(site, handler, body, appliance)
        host = self.cfg.srm_host(site) if appliance == "srm" else self.cfg.vr_host(site)
        hexid = self._config_login(host)
        headers = {
            "Content-Type": "application/json",
            # quotes are REQUIRED around the hex — the backend expects a JSON string
            "dr.config.service.sessionid": f'"{hexid}"',
        }
        r = requests.post(
            f"{self._config_base(host)}/{handler}",
            headers=headers, data=json.dumps(body or {}),
            verify=False, timeout=_TIMEOUT,
        )
        return _wrap(r)

    # ── SRM REST API (/api/rest/srm/{version}) ───────────────────────────────
    def _srm_session(self, host: str) -> str:
        r = requests.post(
            f"https://{host}/api/rest/srm/{self.api_version}/session",
            auth=(self.cfg.sso_user, self.cfg.sso_pass),
            verify=False, timeout=_TIMEOUT,
        )
        r.raise_for_status()
        try:
            data = r.json()
        except ValueError:
            return r.text.strip().strip('"')
        if isinstance(data, str):
            return data
        return data.get("session_id") or data.get("sessionId") or json.dumps(data)

    def srm_rest(self, site: str, method: str, path: str, body: dict | None = None,
                 version: str | None = None) -> dict:
        """Call the SRM REST API. `path` is relative to /api/rest/srm/{version}.

        Recovery-management lives under:
          /pairings/{pid}/recovery-management/plans/{plan}/actions/{test|recovery|...}
        """
        if not live_mode():
            return mock.srm_rest(site, method, path, body)
        ver = version or self.api_version
        host = self.cfg.srm_host(site)
        sid = self._srm_session(host)
        headers = {"Content-Type": "application/json", "x-dr-session": sid}
        rel = path if path.startswith("/") else f"/{path}"
        r = requests.request(
            method.upper(), f"https://{host}/api/rest/srm/{ver}{rel}",
            headers=headers, data=json.dumps(body) if body is not None else None,
            verify=False, timeout=_TIMEOUT,
        )
        return _wrap(r)

    # ── vSphere Replication dr-rest (best-effort; endpoints undocumented) ─────
    def vr_api(self, site: str, method: str, path: str, body: dict | None = None) -> dict:
        if not live_mode():
            return mock.vr_api(site, method, path, body)
        host = self.cfg.vr_host(site)
        # VR envoy exposes dr-rest on :5480/api/rest; the exact sub-paths are not
        # publicly documented. This is a raw passthrough for exploration.
        rel = path if path.startswith("/") else f"/{path}"
        r = requests.request(
            method.upper(), f"https://{host}:5480/api/rest{rel}",
            auth=(self.cfg.sso_user, self.cfg.sso_pass),
            headers={"Content-Type": "application/json"},
            data=json.dumps(body) if body is not None else None,
            verify=False, timeout=_TIMEOUT,
        )
        return _wrap(r)


def _wrap(r: requests.Response) -> dict:
    """Normalise a requests.Response into a JSON-serialisable dict."""
    try:
        payload = r.json()
    except ValueError:
        payload = r.text[:6000]
    return {"status": r.status_code, "ok": r.ok, "data": payload}
