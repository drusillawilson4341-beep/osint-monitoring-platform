# OSINT 监控平台

一套基于 Python + systemd 的多源 OSINT 监控与自动化平台，包含业务公告推送、Telegram 对话桥接、OSINT 工具供应与 MCP 服务、MITM 抓包与漏洞日报。

## 架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                          /root/test                             │
├───────────────┬───────────────┬───────────────┬────────────────┤
│  volc-monitor │   osint-bot   │  osint-tools  │  mitm-monitor  │
│  多源公告监控 │  TG↔Pi 对话   │  工具供应+MCP │ 抓包漏洞日报   │
├───────────────┴───────────────┴───────────────┴────────────────┤
│   OSINT/  滚动记忆（目标范围 / 授权 / POC / 复现 / 发布 / 工具）  │
└─────────────────────────────────────────────────────────────────┘
```

| 组件 | 路径 | 作用 |
|------|------|------|
| 多源监控 | `volc-monitor/` | 抓取 volcengine / douyin / feishu 公告与 crt.sh 域名资产 → 去重 → 推送 Telegram |
| 对话桥接 | `osint-bot/` | Telegram ↔ Pi 对话（getUpdates 长轮询，流式回传） |
| 工具供应 + MCP | `osint-tools/` | 幂等安装 OSINT 工具 + MCP Server（stdio，23+ 工具） |
| mitm 漏洞日报 | `mitm-monitor/` | 抓包域名 → VT/Shodan 漏洞检测 → 每天 20:00 推送 |
| 滚动记忆 | `OSINT/` | 按执行分类的滚动记忆（ROLLING.md） |
| 核心记忆 | `CORE-MEMORY.md` | 需求清单、架构与关键状态 |

## 快速开始

1. 按需复制配置模板并填写密钥（**绝不提交 `.env`**）：

   ```bash
   cp volc-monitor/.env.example   volc-monitor/.env
   cp osint-bot/.env.example      osint-bot/.env
   cp osint-tools/.env.example    osint-tools/.env
   cp mitm-monitor/.env.example   mitm-monitor/.env
   ```

2. 安装依赖与定时任务（详见各子目录 README）：

   ```bash
   bash osint-tools/setup.sh
   bash volc-monitor/install.sh
   ```

3. 更多用法见 `volc-monitor/README.md`、`osint-bot/README.md`、`osint-tools/README.md`。

## 安全提示

- 所有 `.env`、`state.json`、`mitm.env`、抓包流量（`var/`）均已被 `.gitignore` 排除，请勿手动 `git add -f` 提交。
- 若怀疑密钥曾泄露，请及时到各平台（BotFather / Shodan / VirusTotal / URLScan / Hunter / GitHub）吊销并重新生成。
- `osint-bot` 具备执行命令与读写文件能力，仅授权受信任的 `TELEGRAM_CHAT_ID`。
