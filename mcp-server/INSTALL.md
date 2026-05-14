# VCF Lab MCP Server — 安裝流程

從零到 Claude 能呼叫 lab tool 的完整步驟。每一步都附「**驗證**」段，做完該步驟先確認過再進下一步，**不要一路跑完才回頭找問題**。

跑過一次完整流程約 15-20 分鐘（不含 TLS DNS 設定）。

> 對應的 server 程式碼：[`vcf-lab-mcp-server.py`](./vcf-lab-mcp-server.py)
> 總體說明 / 設計理念：[`README.md`](./README.md)

---

## 0. Prerequisites

| 項目 | 需求 |
|---|---|
| Server OS | Ubuntu 20.04 / 22.04 / 24.04 (其它 systemd 發行版也行) |
| Python | 3.11+（建議 distro 內建版本） |
| 網路 | Server 對 lab vCenter / SDDC Manager / ESXi / vROps 都要走得到 |
| Port | TCP 7000 對 client 端開放（防火牆 / 安全群組）|
| 帳號 | root 或可 sudo |
| 與 IaC repo 關係 | secrets 與此 repo `inventory/secrets/lab.yaml` 一致（sops + age） |

**驗證**：
```bash
python3 --version          # >= 3.11
which systemctl            # /usr/bin/systemctl
curl -k -m 5 https://<vcenter-ip>/sdk  # 走得到、有回應 (HTTP 405 也算 OK)
```

---

## 1. 建立目錄與 venv

```bash
sudo mkdir -p /opt/vcf-mcp
cd /opt/vcf-mcp
sudo python3 -m venv venv
sudo ./venv/bin/pip install --upgrade pip
sudo ./venv/bin/pip install \
    "mcp>=1.27" \
    paramiko \
    requests \
    "uvicorn[standard]" \
    urllib3
```

**驗證**：
```bash
/opt/vcf-mcp/venv/bin/python3 -c "from importlib.metadata import version; print('mcp:', version('mcp'))"
# mcp: 1.27.x 以上
```

---

## 2. 產生 TLS 憑證

Self-signed 就夠用（lab 內網，client 端跳過驗證）。**外部正式環境請改用 Let's Encrypt / 內部 CA。**

```bash
cd /opt/vcf-mcp
sudo openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout key.pem -out cert.pem \
  -days 730 \
  -subj "/CN=mcp-server.home.lab"
sudo chmod 600 key.pem
sudo chmod 644 cert.pem
```

**驗證**：
```bash
sudo openssl x509 -in /opt/vcf-mcp/cert.pem -noout -subject -enddate
# subject=CN = mcp-server.home.lab
# notAfter=Apr 30 09:00:00 2028 GMT
```

---

## 3. 產生 API key、寫 keys.json

每個會連 server 的 client (Claude Code、Claude Desktop、同事的機器) 配一把。

```bash
# 為 admin 產生一把
ADMIN_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
# 為 cowork1 產生一把
COWORK_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

sudo tee /opt/vcf-mcp/keys.json >/dev/null <<EOF
{
  "admin":   "${ADMIN_KEY}",
  "cowork1": "${COWORK_KEY}"
}
EOF
sudo chmod 600 /opt/vcf-mcp/keys.json
```

> ⚠️ 印出來複製到 client 之後**清掉 shell history**：`history -d $(history | tail -2 | head -1 | awk '{print $1}')` 或乾脆 `history -c`。

**驗證**：
```bash
sudo cat /opt/vcf-mcp/keys.json | python3 -m json.tool
# 至少 1 個 key-value pair，token 是 43+ 字符 URL-safe base64
sudo stat -c '%a %n' /opt/vcf-mcp/keys.json
# 600 /opt/vcf-mcp/keys.json
```

---

## 4. 放 server 程式碼

```bash
sudo cp vcf-lab-mcp-server.py /opt/vcf-mcp/
sudo chown root:root /opt/vcf-mcp/vcf-lab-mcp-server.py
sudo chmod 644 /opt/vcf-mcp/vcf-lab-mcp-server.py
```

或直接從這個 repo：

```bash
sudo install -m 644 \
  /path/to/mcp/mcp-server/vcf-lab-mcp-server.py \
  /opt/vcf-mcp/vcf-lab-mcp-server.py
```

**驗證**：
```bash
/opt/vcf-mcp/venv/bin/python3 -m py_compile /opt/vcf-mcp/vcf-lab-mcp-server.py
echo $?  # 0
```

---

## 5. 設定 credentials（sops 模式）

Server 讀的 env vars：

| Env var | 對應 lab 元件 |
|---|---|
| `VCENTER_PASS` | vCenter Server (administrator@vsphere.local) |
| `VCF_INSTALLER_PASS` | VCF Installer (admin@local) |
| `SDDC_MANAGER_PASS` | SDDC Manager (administrator@vsphere.local) |
| `VROPS_PASS` | vRealize Ops / Aria Ops |
| `DEFAULT_SSH_PASS` | ESXi / SDDC Mgr / Installer 預設 SSH 密碼 |

### 5a. 把密碼加進 sops 加密的 secrets

跟此 repo 既有 `inventory/secrets/lab.yaml` 對齊。在那個檔案加上：

```yaml
# inventory/secrets/lab.yaml (sops 加密前)
mcp_server:
  vcenter_pass:       "<your-vcenter-pass>"
  vcf_installer_pass: "<your-installer-pass>"
  sddc_manager_pass:  "<your-sddc-pass>"
  vrops_pass:         "<your-vrops-pass>"
  default_ssh_pass:   "<your-ssh-pass>"
```

```bash
sops inventory/secrets/lab.yaml   # 編輯，存檔自動加密
```

### 5b. 寫一個 systemd EnvironmentFile

`scripts/load-secrets.sh` 解出來之後寫到 `/etc/vcf-mcp/env`（**這個檔不要 commit**）：

```bash
sudo mkdir -p /etc/vcf-mcp
sudo install -m 600 /dev/null /etc/vcf-mcp/env

# 從 sops 拿密碼填進去
source <(sops -d inventory/secrets/lab.yaml | yq -r '
  "VCENTER_PASS=\(.mcp_server.vcenter_pass)",
  "VCF_INSTALLER_PASS=\(.mcp_server.vcf_installer_pass)",
  "SDDC_MANAGER_PASS=\(.mcp_server.sddc_manager_pass)",
  "VROPS_PASS=\(.mcp_server.vrops_pass)",
  "DEFAULT_SSH_PASS=\(.mcp_server.default_ssh_pass)"
' | sudo tee /etc/vcf-mcp/env)
sudo chmod 600 /etc/vcf-mcp/env
sudo chown root:root /etc/vcf-mcp/env
```

**驗證**：
```bash
sudo stat -c '%a %n' /etc/vcf-mcp/env  # 必須 600
sudo grep -c '^[A-Z_]*=' /etc/vcf-mcp/env  # >=5
```

> 不想用 sops 的話，直接 `sudo nano /etc/vcf-mcp/env` 寫純文字也可以，反正權限 600。但 secrets 進不了 git 的對應問題就要自己想辦法（共用團隊時尤其麻煩）。

---

## 6. 建立 systemd unit

`/etc/systemd/system/vcf-mcp.service`：

```ini
[Unit]
Description=VCF Lab MCP Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/vcf-mcp
EnvironmentFile=/etc/vcf-mcp/env
ExecStart=/opt/vcf-mcp/venv/bin/python3 /opt/vcf-mcp/vcf-lab-mcp-server.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vcf-mcp
```

**驗證**：
```bash
sudo systemctl status vcf-mcp --no-pager -l
# Active: active (running)
sudo journalctl -u vcf-mcp -n 5 --no-pager
# 看到 "Uvicorn running on https://0.0.0.0:7000"
# 看到 "API keys loaded: ['admin', 'cowork1']"
sudo ss -tlnp | grep 7000
# LISTEN ... 0.0.0.0:7000 ... python3
```

---

## 7. Server-side smoke test

確認 server 對 MCP 協議行為正確。用 venv 內 Python，直接打自己：

```bash
/opt/vcf-mcp/venv/bin/python3 <<'PY'
import asyncio, httpx
from mcp import ClientSession
from mcp.client.sse import sse_client

KEY = "<admin-key-from-keys.json>"
orig = httpx.AsyncClient.__init__
def patched(self, *a, **kw):
    kw["verify"] = False
    return orig(self, *a, **kw)
httpx.AsyncClient.__init__ = patched

async def main():
    async with sse_client(
        "https://localhost:7000/sse",
        headers={"Authorization": f"Bearer {KEY}"},
    ) as streams:
        async with ClientSession(*streams) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("tools:", [t.name for t in tools.tools])
            r = await s.call_tool("ping_host", {"host": "127.0.0.1", "count": 1})
            for c in r.content:
                if hasattr(c, "text"):
                    print(c.text[:400])

asyncio.run(main())
PY
```

**驗證**：能看到 `tools: [...]` 列出 10 個 tool，且 `ping_host` 實際回 ping 結果。

如果失敗：

| 症狀 | 多半是 |
|---|---|
| `401 Unauthorized` | API key 沒帶或帶錯 |
| `SSL: CERTIFICATE_VERIFY_FAILED` | smoke test 沒 patch verify=False |
| `Connection refused` | service 沒起來，回去查 `journalctl -u vcf-mcp` |
| `tools: []` | server.py 解析失敗、所有 `@mcp.tool()` 都沒註冊上 |

---

## 8. Client 設定（從 client 機器做）

### 8a. Claude Code (VSCode extension)

在 workspace 根目錄寫 `.mcp.json`：

```json
{
  "mcpServers": {
    "vcf-lab": {
      "type": "sse",
      "url": "https://<mcp-server-ip>:7000/sse",
      "headers": {
        "Authorization": "Bearer <your-api-key>"
      }
    }
  }
}
```

或全域：`~/.claude.json` 的 top-level `mcpServers`。

### 8b. Claude Desktop

`%APPDATA%\Claude\claude_desktop_config.json` (Win) / `~/Library/Application Support/Claude/claude_desktop_config.json` (mac)：

```json
{
  "mcpServers": {
    "vcf-lab": {
      "type": "sse",
      "url": "https://<mcp-server-ip>:7000/sse",
      "headers": {
        "Authorization": "Bearer <your-api-key>"
      }
    }
  }
}
```

> 用 `headers` 不要用 `?api_key=` query — URL 會被存 server log / proxy log。

---

## 9. 從 Client 端驗證

1. **重啟 Claude Code session / Claude Desktop**（很重要 — config 是啟動時讀的）
2. 在對話內輸入 `/mcp` — 應該看到 `vcf-lab` 是 `connected`
3. 問 Claude 一個會用到 tool 的問題：
   - 「幫我 ping 10.0.0.111」→ 會呼叫 `ping_host`
   - 「列出 vCenter 的 cluster」→ 會呼叫 `vcenter_api("GET", "/api/vcenter/cluster")`
   - 「lab 環境有哪些」→ 會呼叫 `list_environments`

**全綠才算裝完。**

---

## 升級 / 修改

改 `vcf-lab-mcp-server.py` 之後：

```bash
# 同步新版到 /opt/vcf-mcp/
sudo install -m 644 vcf-lab-mcp-server.py /opt/vcf-mcp/

# 重啟
sudo systemctl restart vcf-mcp
sudo journalctl -u vcf-mcp --since '30 seconds ago' --no-pager
```

加新 tool 之後 **client 端也要重啟 session**（tool list 是 init handshake 拿的，session 開著看不到新 tool）。

---

## Rotate API key

```bash
NEW=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
sudo python3 -c "
import json, sys
p='/opt/vcf-mcp/keys.json'
d=json.load(open(p))
d['admin']='${NEW}'
open(p,'w').write(json.dumps(d, indent=2))
"
sudo systemctl restart vcf-mcp
# 同時更新所有 client 的 .mcp.json / claude_desktop_config.json
```

---

## Rotate lab credentials

1. 在 vCenter / SDDC / Installer / vROps UI 改密碼
2. `sops inventory/secrets/lab.yaml` 改裡面對應的值
3. 重新產生 `/etc/vcf-mcp/env`（步驟 5b 的 yq one-liner 再跑一次）
4. `sudo systemctl restart vcf-mcp`

---

## 整套移除

```bash
sudo systemctl disable --now vcf-mcp
sudo rm /etc/systemd/system/vcf-mcp.service
sudo rm -rf /opt/vcf-mcp /etc/vcf-mcp
sudo systemctl daemon-reload
```

---

## 安裝順序總覽（懶人 cheat-sheet）

```
0. prerequisites check (python, systemctl, network reachable)
1. /opt/vcf-mcp + venv + pip install mcp paramiko requests uvicorn urllib3
2. openssl x509 self-signed cert
3. python -c 'secrets.token_urlsafe(32)' → keys.json (chmod 600)
4. cp vcf-lab-mcp-server.py /opt/vcf-mcp/
5. sops decrypt → /etc/vcf-mcp/env (chmod 600)
6. systemd unit + daemon-reload + enable --now
7. server-side smoke test (initialize + tools/list + ping_host)
8. client .mcp.json / claude_desktop_config.json
9. restart client → /mcp shows connected → call a tool
```
