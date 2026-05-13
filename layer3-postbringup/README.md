# Layer 3 — Post bring-up

Bring-up 完之後的 SDDC Manager 動作: commission hosts / create workload domains / deploy NSX edges.

## 預計實作

- `Commission-Hosts.ps1` — 把多餘 ESXi 加進 SDDC Manager 的 host pool
- `New-WorkloadDomain.ps1` — 建第二個 workload domain
- `Deploy-NsxEdge.ps1` — 自動 deploy NSX edge cluster

## TODO

- [ ] SDDC Manager API auth helper (token cache)
- [ ] Host commissioning JSON
- [ ] Workload domain JSON
