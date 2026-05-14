# Scripts & Functions — Reference

整個 repo 所有可執行檔的功能對照表. 跟各 layer 的 README 對應, 這份只看「我要幹某件事該跑哪支」.

---

## Layer 4 — Day-2 Ops

位置: `layer4-day2/`

| 腳本 | 功能 | 跑法 |
|---|---|---|
| `Upgrade-NestedESXi91.ps1` | 單台升級主腳本 (Depot 模式: esxcli software profile update). 自動進/退 maintenance, reboot 後重連 + 印版本 | 不直接跑, 由 wrapper 呼叫 |
| `Run-BatchUpgrade.ps1` | 4 台批次升級. 掃 `E:\9.1\` 找 depot zip; 共用一次密碼; 產 CSV log | `.\Run-BatchUpgrade.ps1` |
| `Exit-MaintenanceMode-All.ps1` | 4 台一鍵退 maintenance, 順便當「fleet 版本檢查表」 | `.\Exit-MaintenanceMode-All.ps1` |
| `Apply-NestedVsanWorkarounds.ps1` | 套 5 個 vSAN/LSOM lab workaround advanced settings (idempotent) | `.\Apply-NestedVsanWorkarounds.ps1 -DryRun` -> 實跑 |
| `ESXi91-ISO-Upgrade-Steps.md` | Console 互動升級步驟 + CPU/HCL/TPM workaround 對照 (文件, 不可執行) | 參考文件 |

### Apply-NestedVsanWorkarounds 套用的 5 個 setting

| Option | Value | 用途 |
|---|---|---|
| `/LSOM/VSANDeviceMonitoring` | 0 | 關 device monitoring (nested 假硬碟誤報) |
| `/LSOM/lsomSlowDeviceUnmount` | 0 | 關 slow device 自動 unmount |
| `/VSAN/SwapThickProvisionDisabled` | 1 | 允許 thin swap (nested 空間吃緊) |
| `/VSAN/Vsan2ZdomCompZstd` | 0 | 關 zstd 壓縮 (nested CPU 不夠力) |
| `/VSAN/GuestUnmap` | 1 | 開 guest unmap (回收空間) |

---

## Layer 2 — VCF 9.1 Auto Bring-up

位置: `layer2-bringup/`

| 檔案 | 功能 | 跑法 |
|---|---|---|
| `vcf91-bringup.template.json` | VCF Installer JSON template, 用 `{{ var.path \| filter }}` 從 inventory 取值 | 不直接編輯 |
| `Generate-BringupSpec.ps1` | 讀 inventory + sops 自解密 secrets, 渲染 template -> `generated-bringup.json`. 支援 `-LabMode` 注入 skipChecks | `pwsh Generate-BringupSpec.ps1 -LabMode` |
| `Submit-Bringup.ps1` | 推 JSON 給 VCF Installer API. 先 validation 再 bring-up, 每 60 秒 poll | `pwsh Submit-Bringup.ps1 -VcfInstaller https://<x> -SpecFile <json>` |
| `New-VcfLab.ps1` | 一鍵 wrapper: 產 spec -> validate -> 你打 YES -> bring-up + poll | `pwsh New-VcfLab.ps1 -VcfInstaller https://<x>` |

### -LabMode 預設注入的 skipChecks

`NESTED_CPU_CHECK`, `NIC_COUNT_CHECK`, `MIN_HOST_CHECK`, `VSAN_ESA_HCL_CHECK`, `ESX_THUMBPRINT_CHECK`

(正式環境加 `-SkipLabMode` 不要跳)

---

## Inventory & Secrets

| 檔案 | 用途 |
|---|---|
| `inventory/lab.yaml` | lab 全部拓樸 (明文, 非敏感): outer vCenter / AD / 4 台 nested ESXi / network VLAN/CIDR / artifact 路徑 |
| `inventory/secrets/lab.example.yaml` | 密碼欄位範本. `cp lab.example.yaml lab.yaml` -> 填值 -> `sops -e -i lab.yaml` 加密 |

---

## MCP Server (Claude 整合)

位置: `mcp-server/`. 跑在 10.0.0.65:7000 (HTTPS + SSE), Bearer token 認證, 讓 Claude (Code / Desktop) 能呼叫 lab 操作.

| 檔案 | 用途 |
|---|---|
| `vcf-lab-mcp-server.py` | FastMCP server 本體, 10 個 `@mcp.tool()` 函式 + ASGI APIKeyMiddleware |
| `README.md` | 架構/設計理念/troubleshoot/security |
| `INSTALL.md` | 從零到 Claude 能呼叫 tool 的 9 步驟編號流程 (每步有驗證指令) |

### `vcf-lab-mcp-server.py` 提供的 10 個 tool

| Tool | 幹嘛 | 打哪個 endpoint | 範例 |
|---|---|---|---|
| `ssh_exec` | SSH 到任意 host 跑指令 (用 paramiko) | TCP 22 | `ssh_exec("10.0.1.10", "esxcli system version get")` |
| `get_ssl_thumbprint` | 抓 host 的 SHA-256 SSL cert thumbprint (VCF bringup 前要) | TCP 443 (TLS handshake only) | `get_ssl_thumbprint("10.0.1.10")` |
| `vcenter_api` | 任意 vCenter REST API call (自動拿 session token) | `https://<vcenter>/api/...` | `vcenter_api("GET", "/api/vcenter/cluster")` |
| `vcf_installer_api` | 任意 VCF Installer API call | `https://<installer>/v1/...` | `vcf_installer_api("GET", "/v1/sddcs")` |
| `sddc_manager_api` | 任意 SDDC Manager API call | `https://<sddc>/v1/...` | `sddc_manager_api("GET", "/v1/domains")` |
| `vrops_api` | 任意 vROps / Aria Ops API call | `https://<vrops>/suite-api/api/...` | `vrops_api("GET", "/suite-api/api/alerts?activeOnly=true")` |
| `check_dns` | 用 lab DNS 解析 FQDN (`nslookup`) | UDP/TCP 53 → DNS_SERVER | `check_dns("vcf-m01-vc01.home.lab")` |
| `ping_host` | ICMP ping 一台 host | ICMP | `ping_host("10.0.1.10", count=3)` |
| `vcf_version` | 自動偵測 SDDC/Installer/vCenter 版本 (依序試) | 同上三個 | `vcf_version("10.0.1.5")` → "SDDC Manager: VCF 9.0.0.0" |
| `list_environments` | 列出 lab 已知 VCF 環境 (M01/M02/M03/VCF5-M02), 可選 `probe=True` 即時測連線 | local data + optional 443 probe | `list_environments(probe=True)` |

### Server 內部結構 (`vcf-lab-mcp-server.py`)

| 區塊 | 在做什麼 |
|---|---|
| `LAB_ENVIRONMENTS` dict | 寫死的 lab 拓樸表 (M01/M02/M03/VCF5-M02 的 IP / FQDN / deploy script 名稱). Claude 透過 `list_environments` 看到 |
| `APIKeyMiddleware` (ASGI) | Bearer header 或 `?api_key=` query 比對 `keys.json` 內 token, 不過就 401 |
| `_vcenter_token / _vcf_token / _sddc_token / _vrops_token` | 4 個 helper, 用 lab 密碼換 access token, 給對應 `*_api` tool 用 |
| `FastMCP("vcf-lab")` + `mcp.sse_app()` | 起 Starlette ASGI app, 註冊上面那 10 個 `@mcp.tool()` 函式 |
| `uvicorn.run(...)` | HTTPS (key.pem/cert.pem), 0.0.0.0:7000, 提供 `/sse` 跟 `/messages` 兩個 endpoint |

### 認證 (`keys.json` + middleware)

```
Client →  Authorization: Bearer <token>          (header, 推薦)
          或 ?api_key=<token>                    (query, 不推薦, 會進 log)
Server →  比對 /opt/vcf-mcp/keys.json values     (chmod 600)
          無效 → 401 Unauthorized
          有效 → 走進 FastMCP SSE handler
```

---

## 典型工作流 (從 4 台 9.0 nested 到 VCF 9.1 起來)

```
1. push vcf-lab-repo 到 GitHub
2. 編 inventory/lab.yaml + sops 加密 inventory/secrets/lab.yaml
3. 對 4 台 nested ESXi (在執行機器上, 要有 PowerCLI 13):
   .\layer4-day2\Run-BatchUpgrade.ps1               # 升 9.1
   .\layer4-day2\Apply-NestedVsanWorkarounds.ps1    # vSAN lab workaround
4. pwsh .\layer2-bringup\New-VcfLab.ps1 -VcfInstaller https://<installer>
   - 先 validation, 看哪些欄位不對 (William Lam 9.1 schema 對齊)
   - 過了打 YES 真的 bring-up
5. (TODO) layer3-postbringup commission / domain / NSX
```

---

## 各 Layer 狀態

| Layer | 狀態 | 內容 |
|---|---|---|
| 1 Nested infra | TODO | 參考 William Lam vcf-automated-lab-deployment |
| 2 VCF Bring-up | scaffold ready | template/generator/submitter 都 ready, 等 9.1 OpenAPI 對齊欄位 |
| 3 Post-bringup | TODO | SDDC Manager API: commission hosts / new workload domain / NSX edge |
| 4 Day-2 ops | OK | nested ESXi 9.0 -> 9.1 升級 + vSAN/LSOM workaround 已實作 |

---

## 通用注意事項

- **PowerShell 7 (`pwsh`)** 跑, 不要 5.1 (中文字串編碼會壞)
- **PowerCLI 13.x**, 第一次跑會自動裝
- 所有 wrapper 都會跳一次 `Get-Credential` 共用 4 台密碼
- 每支批次腳本都會產 `*-yyyyMMdd-HHmmss.csv` log
- Idempotent: 已是目標狀態的 host/setting/VM 會 skip, 不會重複動作
