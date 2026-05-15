# VCF Lab MCP Server

讓 Claude(Code / Desktop)能直接呼叫 VCF 9.1 nested lab 操作 tool 的 FastMCP server。

跑在 automation host(10.0.0.65:7000,HTTPS + SSE),Bearer token 認證。

## 內容

| 檔案 | 用途 |
|---|---|
| [`mcp-server/vcf-lab-mcp-server.py`](./mcp-server/vcf-lab-mcp-server.py) | FastMCP server 本體,10 個 `@mcp.tool()` 函式 + ASGI APIKeyMiddleware |
| [`mcp-server/README.md`](./mcp-server/README.md) | 架構 / 設計理念 / troubleshoot / security |
| [`mcp-server/INSTALL.md`](./mcp-server/INSTALL.md) | 從零到 Claude 能呼叫 tool 的編號安裝流程(每步有驗證指令) |

## 提供的 tool

`ssh_exec`、`get_ssl_thumbprint`、`vcenter_api`、`vcf_installer_api`、`sddc_manager_api`、
`vrops_api`、`check_dns`、`ping_host`、`vcf_version`、`list_environments`。

細節見 [`mcp-server/README.md`](./mcp-server/README.md)。

## 相關 repo

這個 MCP server 操作的 VCF 9.1 nested lab 本身(Infrastructure as Code:inventory、
bring-up、day-2、故障排除紀錄)在獨立 repo:
**[github.com/kostenyang/vcf9.1-lab](https://github.com/kostenyang/vcf9.1-lab)**
