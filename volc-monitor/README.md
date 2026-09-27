# 多源业务动态监控 → Telegram

每天自动抓取多个平台的官方最新业务公告/更新，推送到 Telegram 机器人。

| 数据源 | 站点 | 内容 | 接口 |
|--------|------|------|------|
| volcengine | volcengine.com | 火山引擎产品/平台/安全公告 | `GET /api/notice/noticeList` |
| douyin | developer.open-douyin.com | 抖音开放平台公告（小程序/小游戏/需求反馈） | `GET /bff_api_v2/AnnouncementV2Service/GetAnnouncementList` |
| feishu | open.feishu.cn | 飞书开放平台更新日志（飞书 CLI 等） | `POST /api/tools/change_log/change_log_list` |

## 工作原理

- 每个数据源只推送**新出现**且**属于当月**的条目；首次运行该源时建立基线（记录现有条目、不推送），避免刷屏历史内容。
- `state.json` 按源记录已推送条目 ID 做去重，并记录已推送消息的 `message_id`。
- 推送前自动删除超过 `RETENTION_DAYS`（默认 30 天）的已推送消息。
  > Telegram Bot API 无法枚举历史消息，因此只能清理**本机器人自己发过**的消息。
- 每 `ITERATION_INTERVAL_DAYS`（默认 14 天）自动发送一次「迭代提醒」，询问是否需要迭代更新。
- 常驻轮询进程（`--poll`）自动读取用户回复并进入对话流程：
  - 回复 `1`（需要迭代）→ 机器人询问需求清单，逐条记录需求（回复「完成」生成清单）
  - 回复 `0`（暂不需要）→ 取消本次，14 天后再次询问
  - 需求清单记录到 `state.json` 的 `requirements_history`，并追加写入 `requirements.log`

## 数据源说明

### volcengine.com（火山引擎）
公告分类 `VOLC_TYPES`（父级 TypeId，逗号分隔）：

| TypeId | 名称 | 子类 |
|--------|------|------|
| 188 | 产品公告 | 产品上线 / 产品升级 / 产品下线 |
| 189 | 平台公告 | 平台更新 / 平台动态 |
| 187 | 安全公告 | 漏洞预警 |
| 198 | 备案公告 | 备案 |
| 208 | 安全态势 | 安全态势 |

### douyin.com（抖音开放平台）
公告业务线 `DOUYIN_BIZ`：

| Biz | 业务线 |
|-----|--------|
| 1 | 小游戏 |
| 2 | 小程序 |
| 4 | 需求反馈 |

### feishu.cn（飞书开放平台）
飞书开放平台更新日志（`abilityType` 如 AI / APIs / 小程序 / 网页应用 / 机器人 / 扩展 / 工具 / 平台更新等），全部合并抓取。

## 使用步骤

### 1. 配置 `.env`

```bash
cd /root/test/volc-monitor
chmod 600 .env
# 编辑 .env: 确认 TELEGRAM_BOT_TOKEN、TELEGRAM_CHAT_ID、SOURCES 等
```

### 2. 获取 chat_id

在 Telegram 给机器人 `@OSINTT_Mine_bot` 发任意消息（如 `/start`），然后：

```bash
python3 get_chat_id.py
```

### 3. 测试

```bash
python3 monitor.py --baseline                 # 建立基线(记录现有,不推送)
python3 monitor.py --send-now                 # 立即推送最近 10 条
python3 monitor.py --send-now --dry-run       # 只看内容不推送
```

### 4. 安装定时任务与回复监听

```bash
bash install.sh
```

- `volc-monitor.timer`：每天北京时间 10:00 准时推送
- `volc-monitor-poll.service`：常驻监听用户回复的 `1`/`0`
- 查看状态：`systemctl status volc-monitor.timer volc-monitor-poll.service`
- 查看下次触发：`systemctl list-timers volc-monitor.timer`
- 手动触发：`systemctl start volc-monitor.service`

> 无 systemd 时可用常驻模式替代：`python3 monitor.py --loop`（默认每 6 小时检查，`LOOP_INTERVAL` 可调）；
> 回复监听可改用 `python3 monitor.py --poll` 常驻运行。

## 常用命令

| 命令 | 说明 |
|------|------|
| `python3 monitor.py --once` | 单次检查（默认），有新动态则推送 |
| `python3 monitor.py --baseline` | 仅记录当前条目为已见，不推送 |
| `python3 monitor.py --send-now` | 立即推送最近 N 条 |
| `python3 monitor.py --dry-run` | 只打印，不推送、不写状态 |
| `python3 monitor.py --loop` | 常驻循环模式 |
| `python3 monitor.py --iteration` | 立即发送迭代提醒（手动询问） |
| `python3 monitor.py --poll` | 常驻轮询，自动读取回复 `1`/`0` 并收集需求清单 |

## 环境变量（.env）

| 变量 | 默认 | 说明 |
|------|------|------|
| `TELEGRAM_BOT_TOKEN` | — | 机器人 token（必填） |
| `TELEGRAM_CHAT_ID` | — | 接收推送的 chat_id，逗号分隔多个 |
| `TELEGRAM_PARSE_MODE` | `HTML` | 消息格式 `HTML` / `Markdown` |
| `SOURCES` | `volcengine,douyin,feishu` | 启用的数据源 |
| `MAX_SEND` | `10` | 单次最多推送条数 |
| `PREVIEW_LEN` | `600` | 正文预览长度 |
| `CURRENT_MONTH_ONLY` | `1` | `1`=只推送当月消息，`0`=推送所有新消息 |
| `RETENTION_DAYS` | `30` | 推送前自动删除超过该天数的已推送消息 |
| `ITERATION_ENABLED` | `1` | `1`=每两周询问是否迭代，`0`=关闭 |
| `ITERATION_INTERVAL_DAYS` | `14` | 询问周期（天） |
| `LOOP_INTERVAL` | `21600` | `--loop` 模式检查间隔（秒） |
| `VOLC_TYPES` | `188,189` | 火山引擎公告分类，`0`=全部 |
| `VOLC_LIMIT` | `20` | 火山引擎每分类抓取条数 |
| `DOUYIN_BIZ` | `1,2,4` | 抖音公告业务线 |
| `DOUYIN_LIMIT` | `20` | 抖音每业务线抓取条数 |
| `FEISHU_LIMIT` | `20` | 飞书更新日志抓取条数 |

## 安全提示

- `.env` 含机器人 token，请勿提交到公开仓库（已在 `.gitignore` 排除）。
- token 泄露后任何人都能控制机器人，可在 [@BotFather](https://t.me/BotFather) 用 `/revoke` 重置。
