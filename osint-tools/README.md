# OSINT Tools — 工具供应 + MCP 服务

为服务器幂等安装 OSINT 工具，并以 **MCP (Model Context Protocol)** 标准接口对外暴露，供支持 MCP 的 AI 客户端（Claude Desktop / Cursor 等）调用。

## 目录结构

```
osint-tools/
├── install.sh            # 幂等安装脚本（系统包 + venv + MCP SDK）
├── setup.sh              # 安装 systemd 定时单元并立即执行一次
├── osint-tools.service   # systemd oneshot 单元
├── osint-tools.timer     # 每天 04:30 UTC 触发
├── mcp-config.json       # MCP 客户端接入配置
├── install.log           # 安装日志
├── .venv/                # Python 虚拟环境（含 mcp SDK）
└── servers/
    └── osint_server.py   # MCP Server 主程序（stdio）
```

## 已接入工具（23 个）

### 无需 API key

| MCP 工具 | 底层命令 | 说明 |
|----------|----------|------|
| `whois_lookup` | `whois` | 域名注册信息 |
| `dns_lookup` | `dig` | A/AAAA/MX/TXT/NS/CNAME/SOA/ANY |
| `reverse_dns` | `dig -x` | IP → 域名（PTR） |
| `http_probe` | `curl -sSIL` | 状态码/响应头/Server 指纹/重定向链 |
| `exif_read` | `exiftool` | 图片 EXIF/GPS、文档元数据 |
| `url_expand` | `curl -L` | 短链接展开 |
| `sherlock_search` | `sherlock` | 用户名跨平台搜索 |
| `crt_subdomains` | `crt.sh` | 证书透明日志子域名枚举 |
| `hackertarget_hostsearch` | `api.hackertarget.com` | 子域名+IP |
| `hackertarget_dnslookup` | `api.hackertarget.com` | 完整 DNS 记录 (A/AAAA/MX/NS/TXT/SOA) |

### 需 API key

| MCP 工具 | 说明 | 变量 |
|----------|------|------|
| `shodan_host` | IP 资产测绘（端口/服务/OS/CVE） | `SHODAN_API_KEY` |
| `virustotal_ip` | IP 多引擎信誉 | `VIRUSTOTAL_API_KEY` |
| `virustotal_domain` | 域名多引擎信誉 + 注册商 | `VIRUSTOTAL_API_KEY` |
| `virustotal_hash` | 文件哈希信誉 (MD5/SHA1/SHA256) | `VIRUSTOTAL_API_KEY` |
| `virustotal_url` | URL 多引擎信誉 | `VIRUSTOTAL_API_KEY` |
| `urlscan_scan` | 提交 URL 扫描 | `URLSCAN_API_KEY` |
| `urlscan_result` | 查询扫描结果 | `URLSCAN_API_KEY` |
| `hunter_domain_search` | 域名邮箱枚举 | `HUNTER_API_KEY` |
| `hunter_email_verify` | 邮箱验证 | `HUNTER_API_KEY` |
| `github_user` | GitHub 用户信息 | `GITHUB_TOKEN` |
| `github_repos` | 用户公开仓库 | `GITHUB_TOKEN` |
| `github_search_repos` | 仓库搜索 | `GITHUB_TOKEN` |
| `github_search_code` | 代码搜索 | `GITHUB_TOKEN` |

## CLI 安全测试工具（已接入，非 MCP，直接用 bash 调用）

| 类别 | 工具 |
|------|------|
| 扫描/指纹 | `nmap` `whatweb` `wafw00f` |
| 目录/路径爆破 | `ffuf` `feroxbuster` `gobuster` `dirb` |
| 注入 | `sqlmap` |
| 口令爆破 | `hydra` `medusa` |
| 哈希破解 | `john` `hashcat` |
| 网络/通道 | `netcat`(nc) `socat` `proxychains4` |
| DNS | `dnsutils`(dig/nslookup/host) |
| 字典 | `seclists`(2.5G, `/usr/share/seclists`) |
| 抓包平台 | `burpsuite`(Community, **手动安装**, GUI+许可) |

- 安装方式：`install.sh` 幂等安装（apt + git + GitHub release），随每日定时更新
- feroxbuster 从 GitHub 最新 release 自动升级

## mitmproxy 抓包 MCP（独立 server + systemd 常驻）

- MCP 工具：`mitm_start` / `mitm_status` / `mitm_flows` / `mitm_clear` / `mitm_stop`（底层用 systemctl）
- 独立 venv：`.venv-mitm/`（mitmproxy 12.x + mcp）
- 流量记录：`var/mitm_flows.jsonl`；CA 证书：`~/.mitmproxy/mitmproxy-ca-cert.pem`
- **常驻**：`mitmproxy.service`（开机自启 + 崩溃自动重启），端口 8080，凭据在 `mitm.env`
- **清理**：`mitm-clean.timer` 每 2 天清空流量文件（`cleanup_flows.sh`）
- 抓其他设备流量：block_global=false（已在 service 中设置）

## 定时任务

- 单元：`osint-tools.timer` → 每天 **04:30 UTC** 触发 `osint-tools.service`
- 行为：幂等安装（缺啥补啥），日志写 `install.log`
- 手动执行：`systemctl start osint-tools.service`
- 查看：`systemctl list-timers osint-tools.timer`

## API key 填写位置

统一填 `/root/test/osint-tools/.env`（模板见 `.env.example`，值不加引号）。
接入每个需 key 的工具时，只需填写对应变量：

| 工具 | 变量 | 获取方式 |
|------|------|----------|
| Shodan | `SHODAN_API_KEY` | shodan.io 账号页 |
| VirusTotal | `VIRUSTOTAL_API_KEY` | virustotal.com 账号页 |
| URLScan | `URLSCAN_API_KEY` | urlscan.io 账号页 |
| Hunter.io | `HUNTER_API_KEY` | hunter.io 账号页 |
| GitHub | `GITHUB_TOKEN` | github.com → Settings → Tokens |

填完无需重启定时任务，MCP 客户端重连即生效。

## MCP 接入方式

将 `mcp-config.json` 内容合并到客户端的 MCP 配置：

**Claude Desktop**（`claude_desktop_config.json`）：
```json
{ "mcpServers": { "osint-tools": {
    "command": "/root/test/osint-tools/.venv/bin/python",
    "args": ["/root/test/osint-tools/servers/osint_server.py"]
}}}
```

**本机 pi**：无需 MCP，直接用 `bash` 调用底层命令即可；也可复用 `servers/` 里的工具函数。

## 后续扩展计划（按需接入）

| 工具 | 能力 | 依赖 |
|------|------|------|
| theHarvester | 完整邮箱/主机收集 | GitHub 安装 + 独立 venv |
| Censys | 资产测绘 | `CENSYS_API_ID` + `CENSYS_API_SECRET` |
| SecurityTrails | DNS/子域历史 | `SECURITYTRAILS_API_KEY` |
| BinaryEdge | 资产测绘 | `BINARYEDGE_API_KEY` |
| GreyNoise | 扫描噪声情报 | `GREYNOISE_API_KEY` |
| AbuseIPDB | IP 滥用信誉 | `ABUSEIPDB_API_KEY` |
| IPinfo | IP 归属/ASN | `IPINFO_API_KEY` |

> 注：dnsdumpster 已加 Cloudflare 人机质询，无法脚本接入，其核心能力由 `crt_subdomains` + `hackertarget_*` 覆盖。

新增工具 = 在 `servers/osint_server.py` 加一个 `@mcp.tool()` 函数 + 在 `install.sh` 补依赖。
