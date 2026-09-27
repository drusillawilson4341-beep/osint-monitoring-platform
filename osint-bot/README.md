# osint_bot —— Telegram ↔ Pi 对话桥接

把 `@OSINTT_Mine_bot` 变成 **Pi（终端编码代理）的对话入口**：在 Telegram 里发消息，
桥接进程转发给 Pi（RPC 模式），Pi 的回复**流式**回传到 Telegram。

与 `volc-monitor/`（单向推送公告）互补：
- `monitor.py --once`（systemd timer 每日推送）只调用 `sendMessage`，不消费更新，可与本服务同时运行；
- 本服务独占 `getUpdates` 长轮询，**取代** `monitor.py --poll`（后者同样占用 `getUpdates`，请先停掉）；
- 本服务会转发迭代提醒的 `1/0` 回复给 `monitor.handle_decision`，原“每两周询问是否迭代”功能不受影响。

## 工作原理

```
Telegram 用户
   │ 发消息 (getUpdates 长轮询)
   ▼
osint_bot.py ──prompt──▶ pi --mode rpc (常驻子进程)
   │                         │ 流式事件(text_delta / tool_execution / agent_settled)
   ▼ ◀───────────────────────┘
Telegram 编辑同一条消息(typing + 实时刷新)
```

- **流式回传**：Pi 的文本增量通过 `editMessageText` 实时刷新到同一条消息，避免刷屏。
- **持久会话**：`pi --session-id osint_bot` 落盘，重启桥接进程后上下文不丢失。
- **自动重启**：Pi 子进程异常退出时自动拉起；单次请求超时自动 `abort`。
- **工具透传**：Pi 可执行 `bash / read / edit / write` 等工具，执行期间消息会显示 `🔧 工具名`。
- **授权隔离**：仅响应 `.env` 中 `TELEGRAM_CHAT_ID` 列出的 chat_id。

## 快速开始

```bash
cd /root/test/osint-bot

# 1. 配置(已从 volc-monitor/.env 同步 token 与 chat_id, 如需调整见 .env.example)
chmod 600 .env

# 2. 前台试运行
python3 osint_bot.py

# 3. 或安装为 systemd 服务常驻
cp osint-bot.service /etc/systemd/system/osint-bot.service
systemctl daemon-reload
systemctl enable --now osint-bot
systemctl status osint-bot
```

## 命令

| 命令 | 作用 |
|------|------|
| `/help` `/start` | 帮助 |
| `/new` | 开启新会话（清空上下文） |
| `/abort` | 中止当前任务 |
| `/status` | 会话状态（模型 / 思考级别 / 消息数） |
| `/model` | 列出可用模型 |
| `/model <模型id>` | 切换模型（如 `deepseek-flash`） |

除命令外，任意文本都会作为提示词交给 Pi；发送图片则作为图像输入（需模型支持，如 `deepseek-flash`）。

> 💭 Pi 的**深度思考过程**会以 `<tg-spoiler>` 折叠块实时展示在回答上方（点击可展开），回答正文同步流式刷新。

## 配置项（`.env`）

| 键 | 默认 | 说明 |
|----|------|------|
| `TELEGRAM_BOT_TOKEN` | - | Bot token |
| `TELEGRAM_CHAT_ID` | - | 授权 chat_id，逗号分隔 |
| `TELEGRAM_PARSE_MODE` | `HTML` | 用于折叠思考过程；所有动态内容已转义 |
| `BOT_MODEL` | `deepseek-v4-pro` | 默认模型 |
| `BOT_WORKDIR` | `/root/test` | Pi 工作目录（工具执行处） |
| `BOT_SESSION_ID` | `osint_bot` | 会话 id（持久化） |
| `BOT_THINKING` | 空 | 思考级别 `off/minimal/low/medium/high/max` |
| `BOT_APPEND_PROMPT` | 精简指令 | 附加系统提示词（精简回答）；空=不附加 |
| `STREAM_EDIT_INTERVAL` | `1.2` | 流式消息最小编辑间隔（秒） |
| `THINKING_TAIL` | `1500` | 流式阶段思考过程显示尾部字符数 |
| `THINKING_FINAL_MAX` | `3000` | 定稿时思考过程(spoiler)最大字符数 |
| `PROMPT_TIMEOUT` | `1800` | 单次请求超时（秒），到点自动 abort |

## 安全

- Pi 在 `PI_WORKDIR` 内具备执行命令、读写文件能力；请只给**受信任的 chat_id** 授权。
- `.env` 权限设为 `600`，含 token，勿提交到 git。
- 生产环境建议在 Telegram `@BotFather` 关闭“把机器人加入群组”，限制为私聊使用。

## 文件

```
osint-bot/
├── osint_bot.py        # 主程序(单文件, 无第三方依赖除 requests)
├── .env                # 实际配置(600, 含 token)
├── .env.example        # 配置模板
├── osint-bot.service   # systemd 常驻单元
└── README.md
```
