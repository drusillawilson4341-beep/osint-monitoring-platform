# 工具配置与 MCP 接入 滚动记忆

> 新记录追加到顶部（最新在上），格式：`## YYYY-MM-DD 标题`
> 底部维护「当前工具清单」快照，随接入滚动更新。
> 工具本体与 key 存于 `/root/test/osint-tools/`（`install.sh` / `.env` / `servers/`）。

## 2026-09-27 每日内存自整理
- 状态: 回收缓存(drop_caches)
- used: 800MB → 737MB；available: 166MB → 230MB
- 触发: mem-tidy.timer 每日定时（/etc/systemd/system/mem-tidy.service + .timer）

## 2026-09-27 每日内存自整理
- 状态: 回收缓存(drop_caches)
- used: 783MB → 791MB；available: 184MB → 176MB
- 触发: mem-tidy.timer 每日定时（/etc/systemd/system/mem-tidy.service + .timer）

## 2026-09-27 WAF Auto Fuzzer 接入 MCP（独立 server）
- `/root/scan/Waf-Auto-Fuzzer/waf_mcp_server.py`，stdio，mcp SDK 2.x（复用 `MCPServer`）
- 工具：`waf_fuzz` / `waf_fingerprint` / `waf_payloads` / `waf_encodings`
- 依赖：给其自带 `.venv` 补装 `mcp`（已有 requests/colorama）；导入同目录 `waf_fuzz` 包
- 实测：MCP 握手 + tools/list 通过；本地 HTTP 靶机 FUZZ 30 任务 30 响应正常
- mcp-config.json 增加第三个 server 条目（waf-fuzzer）

## 2026-09-26 新增 mitm 抓包域名漏洞日报（mitm-monitor）
- `monitor.py` 从 `var/mitm_flows.jsonl` 提取域名 → VT(恶意/可疑)+Shodan(CVE) 检测
- `mitm-monitor.timer` 每天 20:00（Asia/Shanghai）推送当月累计有漏洞域名
- Telegram/API key 复用 volc-monitor/.env 与 osint-tools/.env；VT 间隔 5s 避限
- 无漏洞时推送“✅ 当月未发现”

## 2026-09-26 mitmproxy 转 systemd 常驻 + 每两天清理
- `mitmproxy.service`：常驻（Restart=always，开机自启），端口 8080，凭据 `mitm.env`
- `mitm-clean.timer`：每 2 天（OnUnitActiveSec=2d）清空 `var/mitm_flows.jsonl`
- MCP 工具 start/stop/status 改用 systemctl，与 systemd 统一
- 认证实测：无凭据 407 / 带凭据 200

## 2026-09-26 接入 mitmproxy 抓包 MCP（独立 server）
- 独立 venv `.venv-mitm/`（mitmproxy 12.2.3 + mcp）
- `servers/mitm_server.py` + `servers/mitm_addon.py`（流量写 `var/mitm_flows.jsonl`）
- 工具：`mitm_start/status/flows/clear/stop`；实测 HTTP 抓包通过
- CA 证书 `~/.mitmproxy/`；抓其他设备用 global_mode=true（block_global=false）
- mcp-config.json 增加第二个 server 条目

## 2026-09-26 接入 CLI 安全测试工具集（install.sh 扩展）
- apt 安装 14 项：nmap / whatweb / wafw00f / dirb / john / hashcat / proxychains4 / netcat-openbsd / socat / hydra / medusa / gobuster / sqlmap / ffuf
- seclists：git clone 到 `/usr/share/seclists`（2.5G），幂等 pull 更新
- feroxbuster：GitHub 最新 release 下载二进制到 `/usr/local/bin`，自动升级
- burpsuite(Community)：不自动装（GUI+许可），README 留手动说明
- 校验通过；随每日 04:30 UTC 定时幂等更新

## 2026-09-26 域名资产监控接入每日推送（volc-monitor）
- 每日推送新增 `assets` 数据源：用 **crt.sh 证书透明日志**检索 volcengine.com / douyin.com / feishu.cn **当月新证书/新子域**
- 关键：crt.sh 已不再返回 `entry_timestamp`，改用证书 `not_before`（生效时间）作为时间基准
- crt.sh 间歇性 404 → 加 3 次重试（指数退避）
- 配置：`SOURCES` 加 `assets`；新增 `ASSETS_DOMAINS` / `ASSETS_LIMIT`(30)
- 已建基线 16 条；此后每天只推新增证书

## 2026-09-26 接入 Hunter.io 与 GitHub OSINT（+6 工具）
- 新增：`hunter_domain_search` / `hunter_email_verify`（HUNTER_API_KEY）
- 新增：`github_user` / `github_repos` / `github_search_repos` / `github_search_code`（GITHUB_TOKEN，限额 5000/h）
- 全部实测通过；工具总数 17 → **23**

## 2026-09-26 接入 Shodan / VirusTotal / URLScan（+7 工具）
- 新增：`shodan_host`；`virustotal_ip/domain/hash/url`；`urlscan_scan/result`
- 三个 key 均已填并验证有效
- 注意：Shodan DNS API 免费 key 返回 403，已移除（本地 dig 覆盖）；曾建议轮换 Shodan key

## 2026-09-26 接入 sherlock + crt.sh + hackertarget（+4 工具）
- `sherlock_search`（pip 包 sherlock-project，需清理 OSC 转义）
- `crt_subdomains`（crt.sh 证书透明，替代 theHarvester 空壳包）
- `hackertarget_hostsearch` / `hackertarget_dnslookup`（替代 dnsdumpster，后者已加 Cloudflare 质询无法脚本化）
- 工具总数 13 → **17**

## 2026-09-26 搭建框架 + 首批无 key 工具（+6 工具）
- systemd：`osint-tools.timer` 每天 04:30 UTC 幂等安装（install.sh）
- MCP server：`servers/osint_server.py`，stdio 模式，mcp SDK **2.x**（用 `MCPServer` 非 FastMCP）
- 首批：`whois_lookup` / `dns_lookup` / `reverse_dns` / `http_probe` / `exif_read` / `url_expand`
- 关键坑：①目录名 `mcp/` 与 SDK 包名冲突 → 改名 `servers/`；②`_run` 需注入 venv/bin 到 PATH（否则找不到 sherlock）；③错误信息须脱敏避免泄露 API key

---

## 当前工具清单（23 个 · 2026-09-26）

**零 key（10）**：`whois_lookup` `dns_lookup` `reverse_dns` `http_probe` `exif_read` `url_expand` `sherlock_search` `crt_subdomains` `hackertarget_hostsearch` `hackertarget_dnslookup`

**需 key（13）**：`shodan_host`(Shodan) · `virustotal_ip/domain/hash/url`(VT) · `urlscan_scan/result`(URLScan) · `hunter_domain_search/email_verify`(Hunter) · `github_user/repos/search_repos/search_code`(GitHub)

**待接入（key 已预留未填）**：Censys / SecurityTrails / BinaryEdge / GreyNoise / AbuseIPDB / IPinfo

**WAF MCP（独立 server，4 工具）**：`waf_fuzz` / `waf_fingerprint` / `waf_payloads` / `waf_encodings`，主程序 `/root/scan/Waf-Auto-Fuzzer/waf_mcp_server.py`
