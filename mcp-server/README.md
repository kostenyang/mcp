# VCF Lab MCP Server

一個 [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server，把 VMware Cloud Foundation lab 環境的常見操作（vCenter / SDDC Manager / VCF Installer REST API、SSH、DNS、ping、版本偵測…）包成 tool，讓 Claude (Claude Code / Claude Desktop) 或其他 MCP client 直接呼叫。

跑在 Linux 主機上，HTTPS + SSE transport + Bearer token API key 認證。Client 端用 `.mcp.json` 或 `claude_desktop_config.json` 註冊。

> **要裝看這個**：[`INSTALL.md`](./INSTALL.md) — 9 步從零到 Claude 能呼叫 tool，每步驟有獨立驗證點。

---

## 為什麼用 MCP，不是寫個 LLM gateway？

兩件常被搞混的東西：

| | LLM gateway（例如 LiteLLM） | MCP server |
|---|---|---|
| 目的 | 統一呼叫 OpenAI / Gemini / Claude API | 讓 Claude (或其他 MCP client) 能呼叫**你定義的工具** |
| 客戶 | 你寫的應用程式 | Claude Code、Claude Desktop、Cursor、Cline … |
| 協議 | 各 LLM 廠商自己的 HTTP API | MCP（JSON-RPC 2.0 over stdio / SSE / Streamable HTTP） |

Claude Code / Desktop **只認 MCP 協議**。如果你的目標是「給 Claude 一些自製能力」（讀檔、查 VM、觸發 deploy），那就是 MCP server。

---

## 架構

```
┌─────────────────────┐         HTTPS / SSE          ┌────────────────────────┐
│  Claude Code (IDE)  │ ───────────────────────────► │  vcf-lab MCP server    │
│  Claude Desktop     │   Bearer <api_key>           │  Python + FastMCP      │
│  Cursor / Cline …   │ ◄─── tools/list, tools/call  │  uvicorn on :7000      │
└─────────────────────┘                              └──────────┬─────────────┘
                                                                │
                                          ┌─────────────────────┴────────────────┐
                                          │ HTTPS / SSH / DNS / ICMP             │
                                          ▼                                      ▼
                                   ┌───────────────┐                     ┌───────────────┐
                                   │   vCenter     │                     │  SDDC Manager │
                                   │   ESXi hosts  │                     │  VCF Installer│
                                   └───────────────┘                     └───────────────┘
```

- **Transport**: HTTPS + Server-Sent Events (SSE)
- **Auth**: pure-ASGI middleware，看 `Authorization: Bearer <token>` header 或 `?api_key=<token>` query
- **Tool framework**: [`FastMCP`](https://github.com/modelcontextprotocol/python-sdk) (官方 Python SDK)

---

## 需求

- Linux host（範例用 Ubuntu 20.04 / 22.04）
- Python 3.11+
- 對 lab 內部 vCenter / SDDC Manager / ESXi 的網路可達性
- 一張自簽 TLS 憑證（或正式憑證）

Python 套件：

```
mcp>=1.27
paramiko
requests
uvicorn[standard]
urllib3
```

---

## Quick start

### 1. 建立目錄與 venv

```bash
sudo mkdir -p /opt/vcf-mcp
cd /opt/vcf-mcp
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install "mcp>=1.27" paramiko requests "uvicorn[standard]" urllib3
```

### 2. 產生 TLS 憑證

```bash
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout key.pem -out cert.pem -days 365 \
  -subj "/CN=mcp-server"
chmod 600 key.pem
```

### 3. 產生 API key

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

存到 `/opt/vcf-mcp/keys.json`（**chmod 600**）：

```json
{
  "admin":   "<token-1>",
  "cowork1": "<token-2>"
}
```

格式是 `使用者名稱 → bearer token`。Client 帶 `Authorization: Bearer <token>` 來、token 命中 value 就放行。要 revoke 哪個人，把對應 key 刪掉、重啟 service。

### 4. 放上 server 程式碼

把 `vcf-lab-mcp-server.py` 放到 `/opt/vcf-mcp/`，把裡面寫死的 lab IP / 預設帳密改成你自己的（**別 commit 進 git**——見「安全」段）。

### 5. systemd unit

`/etc/systemd/system/vcf-mcp.service`:

```ini
[Unit]
Description=VCF Lab MCP Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/vcf-mcp
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
sudo systemctl status vcf-mcp
sudo journalctl -u vcf-mcp -f
```

---

## Server 程式碼骨架

完整版見 `vcf-lab-mcp-server.py`。核心結構：

```python
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
import uvicorn

mcp = FastMCP(
    "vcf-lab",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)

# ── Pure ASGI middleware：看 Bearer header 或 ?api_key= query ──────────
class APIKeyMiddleware:
    def __init__(self, app):
        self.app = app
        self._VALID = frozenset(load_api_keys().values())

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            if not self._token_ok(scope):
                await send({"type": "http.response.start", "status": 401, "headers": [...]})
                await send({"type": "http.response.body", "body": b'{"error":"Unauthorized"}'})
                return
        await self.app(scope, receive, send)

# ── 一個 tool 就是一個 @mcp.tool() 裝飾的函式 ──────────────────────────
@mcp.tool()
def ping_host(host: str, count: int = 3) -> str:
    """Ping a lab host to check network reachability."""
    import subprocess
    r = subprocess.run(["ping", "-c", str(count), host],
                       capture_output=True, text=True, timeout=20)
    return (r.stdout + r.stderr).strip()

if __name__ == "__main__":
    uvicorn.run(
        APIKeyMiddleware(mcp.sse_app()),
        host="0.0.0.0",
        port=7000,
        ssl_keyfile="/opt/vcf-mcp/key.pem",
        ssl_certfile="/opt/vcf-mcp/cert.pem",
    )
```

**重點**：
- `FastMCP("vcf-lab")` 裡的字串是 server 名稱，client 看得到
- 用 `mcp.sse_app()` 拿 Starlette ASGI app，再用 `APIKeyMiddleware` 包起來
- TLS 不要省，self-signed 就好——MCP client 通常會接受
- `transport_security` 那個 `enable_dns_rebinding_protection=False` 是因為這台只在 lab 內網跑、不會被外面網站打。**對外服務時請保持預設 (True) 並設好 allowed_hosts**

---

## 工具設計準則

設計 MCP tool 時的幾條經驗：

1. **Type hints 要齊全**——FastMCP 用它們自動產生 JSON schema 給 LLM 看
2. **docstring 寫範例**——Claude 是看 docstring 學怎麼呼叫的，越具體越好（給 1-2 行 example call）
3. **回傳值純文字最穩**——`str` 比複雜物件好讓 LLM 消化；要結構化資料時回 JSON string
4. **參數加合理 default**——`def vcenter_api(method, path, body="", vcenter_ip=DEFAULT_IP)`，常見 case Claude 不用每次都填
5. **寫入類 tool 加 dry-run / 二次確認**——MCP 沒有原生 permission prompt，是你 tool 自己要設計

範例：「先 read 後 write」+「dry-run 預設開」的寫入類 tool 模式：

```python
@mcp.tool()
def vcenter_power_off_vm(vm_id: str, dry_run: bool = True) -> str:
    """
    Power off a VM. Defaults to dry_run=True for safety.
    Set dry_run=False to actually execute.
    """
    if dry_run:
        info = vcenter_api("GET", f"/api/vcenter/vm/{vm_id}")
        return f"DRY RUN: would power off VM {vm_id}\nCurrent state:\n{info}"
    return vcenter_api("POST", f"/api/vcenter/vm/{vm_id}/power/stop")
```

---

## Client 設定

### Claude Code (VSCode extension)

在 workspace 根目錄 `./.mcp.json`：

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

要全域可用就放 `~/.claude.json` 的 top-level `mcpServers`。

### Claude Desktop

`%APPDATA%\Claude\claude_desktop_config.json` (Windows) 或 `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)：

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

> **建議用 `headers` 不要用 `?api_key=` query**——URL query 會被存在 server log / proxy log / browser history。

設好之後重啟 Claude Code session / Desktop，在對話內輸入 `/mcp` 應該看到 `vcf-lab` 是 `connected`。

---

## Troubleshooting

### `-32602 Invalid params` / `Received request before initialization was complete`

MCP SSE 的 init handshake 沒走完。流程：

1. `GET /sse` → 拿 session_id (server 透過 SSE event 推回來)
2. `POST /messages?session_id=X` 送 `initialize` request
3. 等 SSE 回 init result
4. `POST /messages` 送 `initialized` notification ← **這步漏掉最常見**
5. 才能 `tools/list` / `tools/call`

如果你的 client 是 Claude Code / Desktop 並且看到這錯誤，多半是它的 SSE reconnect 沿用了舊 session_id 沒重做 init。**先重啟 client session**（VSCode `Developer: Reload Window` 或關掉重開），通常就好。

如果重啟還是壞，server 端用 [`mcp-cli`](https://github.com/modelcontextprotocol/cli) 或 raw curl 做 handshake 跑一次 smoke test，確認 server 本身對協議是 OK 的。

### 401 Unauthorized

API key 沒帶或帶錯。檢查 `.mcp.json` 的 `headers.Authorization` 是 `Bearer <token>`，不是只填 `<token>`。

### TLS handshake failure

Self-signed cert 沒被 client 接受。Claude Code / Desktop 預設會接受，但其他 MCP client 可能要明確設 `NODE_TLS_REJECT_UNAUTHORIZED=0` 或載入 CA。

### Service 啟動就掛

```bash
sudo journalctl -u vcf-mcp -n 100 --no-pager
```

最常見：
- `keys.json` 不存在 / JSON 格式錯
- TLS cert/key 路徑錯
- Python 套件版本不對

---

## 安全

⚠️ **以下這些東西絕對不要 commit 進 git**：

- `keys.json` — API key
- `key.pem` — TLS private key
- 寫死在 source code 裡的 lab 預設密碼（vCenter / SDDC Manager / Installer）
- 真實 lab IP / hostname / FQDN（如果是內網位址無敏感性可以斟酌）

建議 `.gitignore`：

```
keys.json
*.pem
*.key
.env
__pycache__/
venv/
```

Lab 預設密碼建議改成 env var：

```python
VCENTER_PASS = os.getenv("VCENTER_PASS", "")  # 不要寫死預設密碼
```

API key rotation：

```bash
# 產新 key
NEW=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
# 改 keys.json，加新的或換掉舊的
# 同步改所有 client 端 .mcp.json / claude_desktop_config.json
sudo systemctl restart vcf-mcp
```

---

## 可能擴充的方向

- [ ] **Streamable HTTP transport** 取代 SSE — MCP 新規格，比 SSE 穩
- [ ] **Per-key tool ACL** — 限制 `cowork1` 只能用 read-only tool，admin 才能寫入
- [ ] **Audit log** — 每次 tool call 紀錄到 file，誰、什麼時間、呼叫什麼、回傳什麼
- [ ] **寫入類 tool 全部加 confirm 機制** — `confirm: bool = False` 沒帶就 dry-run
- [ ] **Metrics endpoint** — Prometheus exporter，看 tool 呼叫次數 / 失敗率

---

## 參考

- MCP 官方規格：https://modelcontextprotocol.io/specification
- Python SDK：https://github.com/modelcontextprotocol/python-sdk
- FastMCP 範例：https://github.com/modelcontextprotocol/python-sdk/tree/main/examples
- Claude Code MCP 設定：https://docs.claude.com/claude-code/mcp
- VMware VCF REST API：https://developer.broadcom.com/xapis/vmware-cloud-foundation-api/
