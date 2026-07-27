"""Canned fixtures for MOCK mode (default when SRM_LIVE is unset).

The server runs and every tool returns a plausibly-shaped response on any Linux box
with zero lab access. appliance-summary mirrors the REAL registered rtolab state.

For the failover WORKFLOW (trigger a recovery-plan test/recovery, then watch VMs power
on) MOCK simulates a *paired* environment with a small in-memory state machine, so the
whole trigger -> poll -> "VMs powered on" flow is demonstrable offline. Real rtolab is
NOT paired yet (self-signed SRM leaf certs) — every synthetic payload says so.
"""

from __future__ import annotations

_MOCK = {"_mock": True}


def _wrap(data, status: int = 200, ok: bool = True) -> dict:
    return {"status": status, "ok": ok, "data": data}


# ── failover state machine (module-level, persists across tool calls in-process) ──
# plan_id -> {"phase": str, "polls": int, "mode": str}
_PLAN_SM: dict[str, dict] = {}
_RECOVERED_VMS = ["srm-test-vm-01", "srm-test-vm-02"]


def _sm(plan_id: str) -> dict:
    return _PLAN_SM.setdefault(plan_id, {"phase": "READY", "polls": 0, "mode": None})


def _advance(plan_id: str) -> dict:
    """Called on each status poll — advances the simulated run."""
    st = _sm(plan_id)
    if st["mode"] in ("test", "recovery"):
        st["polls"] += 1
        if st["polls"] >= 2:
            st["phase"] = "TEST_COMPLETE" if st["mode"] == "test" else "RECOVERY_COMPLETE"
    return st


def _vms_powered(plan_id: str) -> bool:
    st = _sm(plan_id)
    return st["mode"] in ("test", "recovery") and st["polls"] >= 1


# ── config API (:5480) ───────────────────────────────────────────────────────
def config_api(site: str, handler: str, body, appliance: str) -> dict:
    if handler == "getSummaryInfo":
        return _wrap({
            **_MOCK, "site": site, "appliance": appliance,
            "drConfiguration": {"siteName": f"rtolab-521-{site}",
                                "trustedConnection": True, "configured": True},
            "_note": "mirrors real rtolab state: appliance registered, drConfiguration written",
        })
    if handler == "probeSsl":
        return _wrap({**_MOCK, "thumbprint": "5D:B7:A7:87:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:BC:09:95"})
    return _wrap({**_MOCK, "handler": handler, "echo": body,
                  "_note": "generic mock; run with SRM_LIVE=1 for the real handler response"})


# ── inner vCenter (VM tools) ─────────────────────────────────────────────────
_VMS = {
    "site1": [
        {"vm": "vm-1001", "name": "srm-test-vm-01", "power_state": "POWERED_OFF"},
        {"vm": "vm-1002", "name": "srm-test-vm-02", "power_state": "POWERED_OFF"},
        {"vm": "vm-0016", "name": "kosten-vcf521b-vc", "power_state": "POWERED_ON"},
    ],
    "site2": [
        {"vm": "vm-0016", "name": "kosten-vcf521-vc", "power_state": "POWERED_ON"},
    ],
}


def vm_list(site: str, filter_name: str = "") -> dict:
    vms = list(_VMS.get(site, []))
    # recovery site: once a plan test/recovery has run, the recovered VMs appear here powered on
    if site == "site2" and any(_vms_powered(p) for p in _PLAN_SM):
        for name in _RECOVERED_VMS:
            vms.append({"vm": f"vm-rec-{name[-2:]}", "name": name, "power_state": "POWERED_ON"})
    if filter_name:
        vms = [v for v in vms if filter_name.lower() in v["name"].lower()]
    note = "mock inventory"
    if site == "site2" and any(_vms_powered(p) for p in _PLAN_SM):
        note += "; recovered VMs shown POWERED_ON (a plan run happened this session)"
    return _wrap({"_mock": True, "site": site, "vms": vms, "_note": note})


def vm_info(site: str, vm_id: str) -> dict:
    for v in _VMS.get(site, []):
        if v["vm"] == vm_id:
            return _wrap({**_MOCK, **v, "cpu_count": 1, "memory_mib": 1024})
    return _wrap({**_MOCK, "error": f"vm {vm_id} not found in {site}"}, 404, False)


def snapshot_list(site: str, vm_id: str) -> dict:
    return _wrap({**_MOCK, "vm": vm_id, "snapshots": [], "_note": "mock: no snapshots"})


def vm_power(site: str, vm_id: str, action: str) -> dict:
    return _wrap({**_MOCK, "vm": vm_id, "action": action, "result": "accepted",
                  "_note": "MOCK — no real power change. Set SRM_LIVE=1 to act."})


def snapshot(site: str, vm_id: str, action: str, snapshot_name: str = "",
             snapshot_id: str = "") -> dict:
    return _wrap({**_MOCK, "vm": vm_id, "action": action, "name": snapshot_name,
                  "id": snapshot_id or "snapshot-mock", "result": "accepted",
                  "_note": "MOCK — no real snapshot change. Set SRM_LIVE=1 to act."})


# ── SRM REST (/api/rest/srm/v1) ──────────────────────────────────────────────
_UNPAIRED = ("real rtolab is UNPAIRED (self-signed SRM leaf certs block cross-site "
             "trust); this synthetic paired env exists only to demo the workflow.")


def srm_rest(site: str, method: str, path: str, body) -> dict:
    p = path.strip("/")
    parts = p.split("/")
    m = method.upper()

    # GET /pairings
    if p == "pairings" and m == "GET":
        return _wrap({**_MOCK, "_note": _UNPAIRED,
                      "list": [{"pairing_id": "pair-0001",
                                "local_vc_server": "kosten-vcf521b-vc.rtolab.local",
                                "remote_vc_server": "kosten-vcf521-vc.rtolab.local",
                                "status": "CONNECTED"}]})

    # .../recovery-management/plans   (list)
    if parts[-1] == "plans" and "recovery-management" in parts and m == "GET":
        return _wrap({**_MOCK, "_note": _UNPAIRED, "list": [
            {"id": "rp-0001", "name": "Failover-App-Tier", "state": _sm("rp-0001")["phase"], "vm_count": 2},
            {"id": "rp-0002", "name": "Failover-DB-Tier", "state": _sm("rp-0002")["phase"], "vm_count": 0},
        ]})

    # .../plans/{id}   (status)
    if "plans" in parts and parts[-2] == "plans" and m == "GET":
        plan_id = parts[-1]
        st = _advance(plan_id)
        return _wrap({**_MOCK, "_note": _UNPAIRED, "id": plan_id, "name": "Failover-App-Tier",
                      "state": st["phase"], "current_mode": st["mode"], "poll": st["polls"],
                      "steps_total": 7, "steps_done": min(7, st["polls"] * 4)})

    # .../plans/{id}/vms   (per-VM status/power)
    if parts[-1] == "vms" and "plans" in parts and m == "GET":
        plan_id = parts[parts.index("plans") + 1]
        on = _vms_powered(plan_id)
        return _wrap({**_MOCK, "_note": _UNPAIRED, "plan_id": plan_id, "vms": [
            {"name": n, "recovery_status": "RECOVERED" if on else "READY",
             "power_state": "POWERED_ON" if on else "POWERED_OFF"}
            for n in _RECOVERED_VMS]})

    # .../plans/{id}/actions/{seg}   (trigger)
    if "actions" in parts and m == "POST":
        plan_id = parts[parts.index("plans") + 1]
        seg = parts[-1]
        st = _sm(plan_id)
        st["polls"] = 0
        if seg == "test":
            st["mode"] = "test"; st["phase"] = "TEST_IN_PROGRESS"
        elif seg == "recovery":
            st["mode"] = "recovery"; st["phase"] = "RECOVERY_IN_PROGRESS"
        elif seg in ("cleanup", "cleanupTest"):
            st["mode"] = None; st["phase"] = "READY"
        elif seg == "reprotect":
            st["phase"] = "REPROTECT_COMPLETE"
        elif seg == "cancel":
            st["mode"] = None; st["phase"] = "CANCELLED"
        return _wrap({**_MOCK, "_note": _UNPAIRED, "plan_id": plan_id, "action": seg,
                      "accepted": True, "state": st["phase"]})

    if parts[-1] in ("protection-groups", "protection-group") and m == "GET":
        return _wrap({**_MOCK, "_note": _UNPAIRED, "list": [
            {"id": "pg-0001", "name": "App-Tier", "type": "vr", "protected_vms": _RECOVERED_VMS},
        ]})

    return _wrap({**_MOCK, "method": method, "path": path, "echo": body,
                  "_note": "generic mock; run with SRM_LIVE=1 to hit the appliance"})


# ── vSphere Replication (best-effort) ────────────────────────────────────────
def vr_api(site: str, method: str, path: str, body) -> dict:
    if "replications" in path.lower():
        return _wrap({**_MOCK, "_note": "SYNTHETIC — VR dr-rest sub-paths undocumented; calibrate on LIVE",
                      "list": [{"vm": "srm-test-vm-01", "status": "OK", "rpo_minutes": 15,
                                "direction": f"{site}->peer"}]})
    return _wrap({**_MOCK, "method": method, "path": path, "echo": body,
                  "_note": "generic VR mock; VR dr-rest endpoints undocumented"})
