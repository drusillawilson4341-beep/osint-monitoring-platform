# 核心记忆文档（CORE MEMORY）

> 本文件为 `/root/test` 工作区的**核心记忆**，记录需求清单、系统架构与关键状态。
> 与 `OSINT/` 下的**滚动记忆**（按时间追加、最新在上）不同，本文件保持稳定、精炼。
> 最后更新：2026-09-26

## 一、需求清单

### 已实现 ✅
1. 每天自动检索 **volcengine.com** 最新业务公告 → 推送 Telegram
2. 扩展数据源：**douyin.com**（抖音开放平台公告）、**feishu.cn**（飞书开放平台更新日志）
3. 每天 **北京时间 10:00** 固定推送（systemd timer）
4. 推送前自动删除超过 **30 天** 的已推送消息
5. 每次只推送**当月**最新消息
6. 每 **14 天** 自动询问是否迭代
7. 机器人自动读取 **`1`/`0`** 回复
8. 回复 `1` 后自动询问并记录**需求清单**（逐条记录，回复「完成」生成清单）
9. 每日推送时，通过 **crt.sh 证书透明日志**检索监控域名（volcengine/douyin/feishu）**当月新证书/新子域**（域名资产动态）

### 待办 / 后续迭代
- 以 `volc-monitor/requirements.log` 与 `state.json` 的 `requirements_history` 为准，按需追加。

## 二、系统架构

| 组件 | 路径 | 作用 |
|------|------|------|
| 多源监控 | `/root/test/volc-monitor/` | 抓取公告/更新 → 去重 → 推送 Telegram |
| 对话桥接 | `/root/test/osint-bot/` | Telegram ↔ Pi 对话（getUpdates 长轮询，独占） |
| 滚动记忆 | `/root/test/OSINT/` | 按执行分类的滚动记忆（ROLLING.md） |
| 核心记忆 | `/root/test/CORE-MEMORY.md` | 本文件 |
| 工具供应+MCP | `/root/test/osint-tools/` | 幂等安装 OSINT 工具 + MCP Server（stdio） |
| mitm 漏洞日报 | `/root/test/mitm-monitor/` | 抓包域名 → VT/Shodan 漏洞检测 → 每天 20:00 推送 |

## 三、关键状态与配置

- **数据源**：
  - volcengine：产品公告 `188`、平台公告 `189`
  - douyin：小游戏 `1`、小程序 `2`、需求反馈 `4`
  - feishu：开放平台更新日志（全量）
  - assets：域名资产动态（crt.sh 证书透明日志，检索上述域名当月新证书/新子域）
- **定时**：每天 10:00（Asia/Shanghai）
- **去重**：`state.json` 的 `seen_ids`（按源记录，首次运行建立基线）
- **消息清理**：已推送消息 > `RETENTION_DAYS`(30 天) 自动删除
- **迭代提醒**：每 `ITERATION_INTERVAL_DAYS`(14 天) 一次；收到回复后重置倒计时
- **需求记录**：`state.json` 的 `requirements_history` + `volc-monitor/requirements.log`
- **迭代 1/0 处理**：`osint_bot.py` 转发给 `monitor.handle_decision`（有迭代提醒且未回复时）
- **域名资产**：`ASSETS_DOMAINS`(volcengine.com,douyin.com,feishu.cn,arc.io) + `ASSETS_LIMIT`(30)；时间基准用证书 `not_before`（crt.sh 已不再返回 entry_timestamp）
- **工具供应**：`osint-tools.timer` 每天 04:30 UTC 幂等安装 OSINT 工具（日志 `install.log`）
- **MCP 服务**：`servers/osint_server.py`（stdio；已接入 23 工具：whois/dns/curl/exiftool/crt/hackertarget/sherlock + Shodan/VirusTotal/URLScan/Hunter/GitHub，详见 `osint-tools/README.md`）
- **WAF MCP 服务**：`/root/scan/Waf-Auto-Fuzzer/waf_mcp_server.py`（stdio；已接入 4 工具：waf_fuzz/waf_fingerprint/waf_payloads/waf_encodings，依赖其自带 `.venv` 已装 `mcp`）
- **mitmproxy**：systemd 常驻（端口 8080，凭据 `mitm.env`）；`mitm-clean.timer` 每 2 天清空 `var/mitm_flows.jsonl`
- **mitm 漏洞日报**：`mitm-monitor.timer` 每天 20:00（Asia/Shanghai）从 `var/mitm_flows.jsonl` 提取域名，VT(恶意/可疑)+Shodan(CVE) 检测，推送当月累计有漏洞域名

## 四、关键路径

- 监控主程序：`/root/test/volc-monitor/monitor.py`
- 配置：`/root/test/volc-monitor/.env`
- 桥接主程序：`/root/test/osint-bot/osint_bot.py`
- 需求清单文件：`/root/test/volc-monitor/requirements.log`
- 工具安装脚本：`/root/test/osint-tools/install.sh`
- MCP 主程序：`/root/test/osint-tools/servers/osint_server.py`
- WAF MCP 主程序：`/root/scan/Waf-Auto-Fuzzer/waf_mcp_server.py`
- MCP 接入配置：`/root/test/osint-tools/mcp-config.json`
- 工具滚动记忆：`/root/test/OSINT/06-tools/ROLLING.md`
- mitm 漏洞日报主程序：`/root/test/mitm-monitor/monitor.py`
