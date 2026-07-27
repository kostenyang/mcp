# srm-mcp — Docker (stdio) 版

給 **MCP client（Claude Code / Claude Desktop）直接啟動**的容器版：client 用
`docker run -i` 拉起容器，透過 stdin/stdout 講 MCP（JSON-RPC），不需要 HTTP、不需要
Bearer token、不需要開埠。

跟另外兩種形態同一份程式碼，用 `MCP_TRANSPORT` 環境變數切換：

| 形態 | transport | 用途 |
|---|---|---|
| k8s (`manifests/`) | streamable-HTTP :8080 | 多人共用 / VKS |
| vm (`deploy/`) | streamable-HTTP + mcpo + OpenWebUI | 瀏覽器聊天實測 |
| **docker (這裡)** | **stdio** | Claude Code / Desktop 直接接 |

## 建 image

```bash
cd <repo root>            # 有 Dockerfile 的地方
docker build -t srm-mcp:0.1.0 .
```

## 直接跑（冒煙測試，MOCK）

```bash
docker run -i --rm -e MCP_TRANSPORT=stdio srm-mcp:0.1.0
# 這會等 stdin 的 JSON-RPC；正常沒輸出（開機訊息在 stderr）
```

## 接進 Claude Code（專案 `.mcp.json`）

```json
{
  "mcpServers": {
    "srm": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-e", "MCP_TRANSPORT=stdio", "srm-mcp:0.1.0"]
    }
  }
}
```

## 接進 Claude Desktop（`claude_desktop_config.json`）

同上，放在 Desktop 設定檔的 `mcpServers` 下即可。

## 打真 appliance（LIVE）

加上 env，並確保**執行 docker 的主機能連到 192.168.114.x**：

```json
{
  "mcpServers": {
    "srm": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-e", "MCP_TRANSPORT=stdio",
        "-e", "SRM_LIVE=1",
        "-e", "SRM_ALLOW_ACTIONS=0",
        "-e", "SRM_SSO_PASS=<your-vcenter-sso-password>",
        "-e", "SRM_APPLIANCE_PASS=<your-appliance-vami-password>",
        "--network", "host",
        "srm-mcp:0.1.0"
      ]
    }
  }
}
```

- stdio 是本機管道，沒有 auth 層——安全來自「只有能執行 `docker run` 的人能起它」。
- `SRM_ALLOW_ACTIONS=1` 才會開破壞性工具；預設關＝唯讀。
- 密碼是以環境變數傳入容器，別把含密碼的設定檔 commit。
