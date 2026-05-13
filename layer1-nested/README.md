# Layer 1 — Nested ESXi 部署

從零開始把 4 台 nested ESXi VM 開到外層 vCenter (10.0.0.101) 上。

## 預計實作

- `deploy-config.json` — nested VM 規格 (vCPU/RAM/disk/網卡/VLAN/IP)
- `deploy.ps1` — 讀 JSON 用 PowerCLI 建 VM, 掛 ISO, 第一次開機自動 kickstart
- 參考 William Lam 的 `vcf-automated-lab-deployment` 改寫

## TODO

- [ ] 寫 deploy-config.json template
- [ ] 寫 deploy.ps1
- [ ] Kickstart cfg (自動填 root pw / network / hostname)
- [ ] 拆掉/重建 idempotent
