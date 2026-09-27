# 目标域名范围 滚动记忆

> 新记录追加到顶部（最新在上），格式：`## YYYY-MM-DD 标题`
> 记录每日监控任务的目标域名清单与变更。

## 2026-09-26 每日任务监控域名（当前清单）

**业务公告/更新源**（volc-monitor 抓取）：
- volcengine.com — 火山引擎公告
- douyin.com — 抖音开放平台公告
- feishu.cn — 飞书开放平台更新日志

**域名资产监控**（crt.sh 证书透明日志，`ASSETS_DOMAINS`）：
- volcengine.com
- douyin.com
- feishu.cn
- **arc.io**（本次新增 `*.arc.io`，当月已有 18 条证书已建基线）

> 配置位置：`/root/test/volc-monitor/.env` 的 `ASSETS_DOMAINS`；改动后每日推送自动生效。
