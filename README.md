# VCF 9 Lab — Infrastructure as Code

整個 VCF 9 lab 環境的「重建檔」。每層各自獨立、能重跑、不依賴某台機器的記憶。

## Layout

```
.
├── inventory/             # 環境拓樸 (hosts, IPs, FQDNs, VLANs)
│   ├── lab.yaml           # 主清單 (明文, 非敏感)
│   └── secrets/           # sops + age 加密過的密碼/憑證
├── layer1-nested/         # Nested ESXi VM 部署 (William Lam 風格 JSON)
├── layer2-bringup/        # VCF Installer JSON + 推送腳本
├── layer3-postbringup/    # SDDC Manager API: commission/workload domain/NSX
├── layer4-day2/           # 升級/補丁/擴增 (PowerCLI)
└── scripts/               # 共用 helper (bootstrap, secrets, verify)
```

## Quick start (on 10.0.0.65)

```bash
# Bootstrap automation host
LAB_USER=labops bash scripts/bootstrap-automation-host.sh

# 切到 labops 帳號
su - labops
source /opt/vcf-lab/.venv/bin/activate    # Python venv

# 跑 layer4 day-2 動作 (例: 批次升級 nested ESXi)
pwsh ./layer4-day2/Run-BatchUpgrade.ps1
```

## Inventory

所有腳本從 `inventory/lab.yaml` 取拓樸資訊（IP、hostname、VM name、datastore）。
密碼從 `inventory/secrets/lab.yaml`（sops 加密）取，不會明文進 git。

範例：

```bash
# 編輯密碼檔 (會自動解密 -> 編輯 -> 加密)
sops inventory/secrets/lab.yaml

# 在腳本裡讀
source scripts/load-secrets.sh
echo "$ESXI_ROOT_PW"
```

## 各 Layer 狀態

| Layer | 狀態 | 備註 |
|---|---|---|
| 1 Nested infra | TODO | 參考 William Lam vcf-automated-lab-deployment |
| 2 VCF Bring-up | TODO | VCF Installer JSON |
| 3 Post-bringup | TODO | SDDC Manager API |
| 4 Day-2 ops | ✅ | nested ESXi 9.0 → 9.1 升級已實作 |
