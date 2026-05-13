# Layer 4 - Day-2 ops

跑現有環境的補丁/升級/維運. 不負責 bring-up.

## 內容

| 檔案 | 用途 |
|---|---|
| `Upgrade-NestedESXi91.ps1` | 單台 nested ESXi 升級主腳本 (Depot 模式) |
| `Run-BatchUpgrade.ps1` | Wrapper, 對 4 台批次跑 depot 升級 |
| `Exit-MaintenanceMode-All.ps1` | 4 台一鍵退出 maintenance |
| `Apply-NestedVsanWorkarounds.ps1` | 套用 5 個 vSAN/LSOM lab workaround advanced settings |
| `ESXi91-ISO-Upgrade-Steps.md` | ISO 開機升級 console 操作步驟 + lab workaround |

## 跑法

```powershell
# 升 4 台 (PowerCLI)
.\Run-BatchUpgrade.ps1

# 套用 vSAN/LSOM workaround
.\Apply-NestedVsanWorkarounds.ps1

# 看哪幾台已升上 9.1 + 順便退 maintenance
.\Exit-MaintenanceMode-All.ps1
```
