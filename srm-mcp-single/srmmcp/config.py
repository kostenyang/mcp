"""Config loader for srm-mcp.

Loads the site/appliance topology from a YAML file and resolves passwords from
env vars (never stored in YAML). Also exposes the runtime mode flags:

  SRM_LIVE=1           -> hit real appliances; default (unset/0) = MOCK fixtures
  SRM_ALLOW_ACTIONS=1  -> permit destructive action tools (recovery/pairing);
                          default (unset/0) = read-only, actions refuse to run
  SRM_CONFIG           -> path to the topology YAML (default: config/srm.yaml,
                          falling back to config/srm.example.yaml)
"""

from __future__ import annotations

import os
import pathlib

import yaml

_HERE = pathlib.Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG = _HERE / "config" / "srm.yaml"
_EXAMPLE_CONFIG = _HERE / "config" / "srm.example.yaml"


def live_mode() -> bool:
    return os.getenv("SRM_LIVE", "0") == "1"


def actions_allowed() -> bool:
    return os.getenv("SRM_ALLOW_ACTIONS", "0") == "1"


def vcenter_tools_enabled() -> bool:
    """SRM_VCENTER_TOOLS=0 -> SRM-only mode: no vCenter tools, no vCenter access.

    The MCP then only needs an SSO account with SRM 'run recovery plan' privilege —
    NOT vCenter permissions. VM power status still comes from SRM (recovery_plan_vms).
    """
    return os.getenv("SRM_VCENTER_TOOLS", "1") == "1"


def config_path() -> pathlib.Path:
    explicit = os.getenv("SRM_CONFIG")
    if explicit:
        return pathlib.Path(explicit)
    if _DEFAULT_CONFIG.exists():
        return _DEFAULT_CONFIG
    return _EXAMPLE_CONFIG


class SrmConfig:
    """Parsed topology + resolved secrets."""

    def __init__(self, raw: dict):
        self.raw = raw
        self.domain = raw.get("domain", "")
        self.sso_user = raw.get("sso_user", "administrator@vsphere.local")
        self.appliance_user = raw.get("appliance_user", "admin")
        self._sso_pass_env = raw.get("sso_pass_env", "SRM_SSO_PASS")
        self._appliance_pass_env = raw.get("appliance_pass_env", "SRM_APPLIANCE_PASS")
        self.sites = raw.get("sites", {})

    @property
    def sso_pass(self) -> str:
        return os.getenv(self._sso_pass_env, "")

    @property
    def appliance_pass(self) -> str:
        return os.getenv(self._appliance_pass_env, "")

    @property
    def default_site(self) -> str:
        """First configured site key — the fallback when no site is specified.

        In single-site mode this is the only site, so tools operate on it
        regardless of the `site` argument they were called with.
        """
        return next(iter(self.sites), "site1")

    @property
    def is_single_site(self) -> bool:
        """True when the config holds exactly one site (single-site deployment),
        or when SRM_SINGLE_SITE=1 forces it. Cross-site tools (pairing) are then
        disabled and every tool targets `default_site`."""
        return len(self.sites) == 1 or os.getenv("SRM_SINGLE_SITE", "0") == "1"

    def site(self, key: str) -> dict:
        if key not in self.sites:
            raise KeyError(
                f"unknown site {key!r}; known sites: {', '.join(self.sites) or '(none)'}"
            )
        return self.sites[key]

    def _block(self, site_key: str, name: str) -> dict:
        """Return a site sub-block (srm/vcenter/vr), with a clear error if absent.

        Only `srm` is required. `vcenter` (pairing) and `vr` (VR tools) are optional —
        for a pure SRM-only deployment the config may contain just the `srm` blocks.
        """
        blk = self.site(site_key).get(name)
        if not blk:
            raise KeyError(
                f"site {site_key!r} has no '{name}' block in config "
                f"(it's optional — add it only if you use the tools that need it)"
            )
        return blk

    def srm_host(self, site_key: str) -> str:
        """SRM appliance IP (prefer IP — rtolab.local often not resolvable)."""
        s = self._block(site_key, "srm")
        return s.get("ip") or s.get("fqdn")

    def vr_host(self, site_key: str) -> str:
        s = self._block(site_key, "vr")
        return s.get("ip") or s.get("fqdn")

    def vcenter(self, site_key: str) -> dict:
        return self._block(site_key, "vcenter")

    def summary(self) -> dict:
        """Redacted topology for list_srm_environment (no secrets)."""
        return {
            "domain": self.domain,
            "sso_user": self.sso_user,
            "appliance_user": self.appliance_user,
            "live_mode": live_mode(),
            "actions_allowed": actions_allowed(),
            "vcenter_tools": vcenter_tools_enabled(),
            "single_site": self.is_single_site,
            "default_site": self.default_site,
            "mode": self._mode_str(),
            "config_path": str(config_path()),
            "sites": self.sites,
        }

    def _mode_str(self) -> str:
        base = "full" if vcenter_tools_enabled() else "srm-only (no vCenter access)"
        if self.is_single_site:
            return f"single-site [{self.default_site}] · {base} · pairing disabled"
        return base


_cached: SrmConfig | None = None


def load_config() -> SrmConfig:
    global _cached
    if _cached is not None:
        return _cached
    path = config_path()
    if not path.exists():
        raise RuntimeError(f"SRM config not found: {path}")
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    _cached = SrmConfig(raw)
    return _cached
