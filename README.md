# VCF 9 Lab - Infrastructure as Code

整個 VCF 9 lab 環境的「重建檔」. 每層各自獨立、能重跑、不依賴某台機器的記憶.

> **快速查腳本**: [SCRIPTS.md](./SCRIPTS.md) — 所有可執行檔的功能對照表 + 典型工作流

## Layout

```
.
├── inventory/             # 環境拓樸 (hosts, IPs, FQDNs, VLANs)
│   ├── lab.yaml           # 主清單 (明文, 非敏感)
│   └── secrets/           # sops + age 加密過的密碼/憑證
├── layer1-nested/         # Nested ESXi VM 部署
├── layer2-bringup/        # VCF Installer JSON + 推送腳本
├── layer3-postbringup/    # SDDC Manager API: commission/workload domain/NSX
└── layer4-day2/           # 升級/補丁/擴增 (PowerCLI)
```

## 各 Layer 狀態

| Layer | 狀態 | 備註 |
|---|---|---|
| 1 Nested infra | TODO | 參考 William Lam vcf-automated-lab-deployment |
| 2 VCF Bring-up | scaffold | template/generator/submitter ready, 等 9.1 OpenAPI 對齊 |
| 3 Post-bringup | TODO | SDDC Manager API |
| 4 Day-2 ops | OK | nested ESXi 9.0 -> 9.1 升級 + vSAN workaround 已實作 |

## Secrets

密碼放 inventory/secrets/lab.yaml (sops + age 加密過再 commit), 範本見 lab.example.yaml.
Layer2 腳本會自己呼叫 sops 解密, 不用先 source env vars.
