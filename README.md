# VCF 9 Lab — Infrastructure as Code

整個 VCF 9 lab 環境的「重建檔」。每層各自獨立、能重跑、不依賴某台機器的記憶。

> **快速查腳本**: [SCRIPTS.md](./SCRIPTS.md) — 所有可執行檔的功能對照表 + 典型工作流

## Layout

```
.
├── inventory/             # 環境拓樸 (hosts, IPs, FQDNs, VLANs)
│   ├── lab.yaml           # 主清單 (明文, 非敏感)
│   └── secrets/           # sops + age 加密過的密碼/憑證
├── layer1-nested/         # Nested ESXi VM 部署 + 部署前準備
├── layer2-bringup/        # VCF Installer JSON + 推送腳本
├── layer3-postbringup/    # SDDC Manager API: commission/workload domain/NSX
├── layer4-day2/           # 升級/補丁/擴增 (PowerCLI)
├── mcp-server/            # FastMCP server，給 Claude (Code/Desktop) 呼叫 lab tool 用
└── scripts/               # 共用 helper (bootstrap, secrets, verify)
```

## Quick start

```bash
# Bootstrap automation host (10.0.0.65)
LAB_USER=labops bash scripts/bootstrap-automation-host.sh

# 切到 labops 帳號
su - labops
source /opt/vcf-lab/.venv/bin/activate    # Python venv

# Layer 1: nested ESXi 部署前準備 (開機後、VCF Installer 前)
pwsh ./layer1-nested/Prepare-NestedESXi.ps1

# Layer 2: VCF bring-up
source scripts/load-secrets.sh
pwsh ./layer2-bringup/New-VcfLab.ps1 -VcfInstaller https://10.0.1.5

# Layer 4: day-2 (例: 批次升級 nested ESXi 到 9.1)
pwsh ./layer4-day2/Run-BatchUpgrade.ps1
```

## Inventory

所有腳本從 `inventory/lab.yaml` 取拓樸資訊（IP、hostname、VM name、datastore）。
密碼從 `inventory/secrets/lab.yaml`（sops 加密）取，不會明文進 git。

```bash
# 編輯密碼檔 (自動解密 -> 編輯 -> 加密)
sops inventory/secrets/lab.yaml

# 在腳本裡讀
source scripts/load-secrets.sh
echo "$ESXI_ROOT_PW"
```

## 各 Layer 狀態

| Layer | 狀態 | 備註 |
|---|---|---|
| 1 Nested infra | 部分 | 部署前 advanced settings + vSAN unicast 修復腳本 ready |
| 2 VCF Bring-up | scaffold | template/generator/submitter ready，等 9.1 OpenAPI 對齊 |
| 3 Post-bringup | TODO | SDDC Manager API |
| 4 Day-2 ops | ✅ | nested ESXi 9.0 → 9.1 升級 + vSAN workaround 已實作 |

## Secrets

密碼放 `inventory/secrets/lab.yaml`（sops + age 加密過再 commit），範本見 `lab.example.yaml`。
Layer2 腳本會自己呼叫 sops 解密，不需要先 source env vars。
