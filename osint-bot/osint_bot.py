#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
osint_bot —— Telegram <-> Pi 对话桥接

把 @OSINTT_Mine_bot 变成 Pi 的对话入口:
  - 用户在 Telegram 发消息 -> 转发给 Pi(RPC 模式) -> Pi 回复流式回传 Telegram
  - 支持控制命令: /help /new /abort /status /model
  - 仅响应授权 chat_id; 持久会话(按 session-id 落盘, 重启不丢上下文)

运行:
    python3 osint_bot.py            # 前台常驻(long polling)
    python3 osint_bot.py --once     # 只处理一轮 pending 更新后退出(调试用)
"""

import argparse
import base64
import html
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

try:
    import requests
except ImportError:  # pragma: no cover
    sys.stderr.write("缺少依赖 requests，请执行: pip3 install requests\n")
    sys.exit(1)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.environ.get("OSINT_BOT_ENV", os.path.join(BASE_DIR, ".env"))

DEFAULTS = {
    # Telegram
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_CHAT_ID": "",          # 授权 chat_id, 逗号分隔(仅这些 id 可对话)
    "TELEGRAM_PARSE_MODE": "HTML",   # HTML: 用 <tg-spoiler> 折叠思考过程; 所有动态内容均转义
    "TELEGRAM_API": "https://api.telegram.org",
    # Pi (注意: 用 BOT_ 前缀, 避免与 harness 注入的 PI_MODEL/PI_SESSION_ID 等环境变量冲突)
    "BOT_MODEL": "deepseek-v4-pro",
    "BOT_WORKDIR": "/root/test",      # Pi 的工作目录(工具在这里执行)
    "BOT_SESSION_ID": "osint_bot",    # 会话 id, 持久化上下文
    "BOT_APPROVE": "1",               # 1=信任项目本地配置/资源(--approve)
    "BOT_THINKING": "",               # 空=模型默认; 可选 off/minimal/low/medium/high/max
    "BOT_PI_BIN": "",                 # pi 可执行文件路径; 空=自动探测(which pi, 回退 /root/.pi/agent/bin/pi)
    # 附加系统提示词(让回答更精简); 空=不附加
    "BOT_APPEND_PROMPT": "你是运行在 Telegram 聊天机器人中的助手。回复务必精简高效: 直接给出结论, 少铺垫, 不重复; 用短句和要点列表; 代码只给必要片段; 默认不展开背景与原理, 除非用户明确要求。",
    # 行为
    "STREAM_EDIT_INTERVAL": "1.2",   # 秒, 编辑流式消息的最小间隔
    "STREAM_TAIL": "3800",           # 流式阶段答案只显示末尾 N 字符(会按思考长度动态收缩)
    "THINKING_TAIL": "1500",         # 流式阶段思考过程只显示末尾 N 字符
    "THINKING_FINAL_MAX": "3000",    # 定稿时思考过程(spoiler)最大字符数
    "MSG_LIMIT": "4096",             # Telegram 单条消息上限
    "TYPING_INTERVAL": "4.0",        # 秒, 重复发送 typing 的心跳间隔
    "PROMPT_TIMEOUT": "1800",        # 秒, 单次请求超时(到点自动 abort)
    "POLL_TIMEOUT": "25",            # 秒, getUpdates 长轮询超时
}

HELP_TEXT = (
    "🤖 <b>osint_bot · Pi 对话桥接</b>\n\n"
    "直接把消息发给我，我会交给 Pi 处理并流式回传结果。\n\n"
    "<b>命令</b>\n"
    "/new    开启新会话(清空上下文)\n"
    "/abort  中止当前任务\n"
    "/status 查看会话状态(模型/消息数)\n"
    "/model  查看可用模型\n"
    "/model &lt;模型id&gt;  切换模型\n"
    "/help   显示本帮助\n\n"
    "⚠️ Pi 可在工作目录执行命令/读写文件，请只在受信任的会话中使用。"
)


def log(msg):
    sys.stderr.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    sys.stderr.flush()


def esc(text):
    """转义 HTML 特殊字符, 用于在 parse_mode=HTML 下安全展示动态内容。"""
    return html.escape(text or "", quote=False)


def md_to_plain(md):
    """把 Markdown 语法剥成纯文本(回复不采用 .md 格式)。"""
    text = html.unescape(md or "")
    text = re.sub(r"\\([-*_#`>])", r"\1", text)              # 转义符
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)        # 图片
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)     # 链接 -> 文本
    text = re.sub(r"```[^\n`]*\n?", "", text)                  # 代码块开围栏(含语言标记)
    text = re.sub(r"`{1,3}", "", text)                         # 剩余代码围栏/行内代码 -> 保留内容
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)  # 标题
    text = re.sub(r"^\s{0,3}>\s?", "", text, flags=re.M)       # 引用
    text = re.sub(r"^\s{0,3}[-*+]\s+", "• ", text, flags=re.M)  # 无序列表
    text = re.sub(r"^\s{0,3}\d+[.)]\s+", "", text, flags=re.M)  # 有序列表
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)       # 粗体/斜体/粗斜体
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.M)     # 分隔线
    text = re.sub(r"[|]", " ", text)                            # 表格竖线
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# 可选: 复用 volc-monitor/monitor.py 的迭代决策(1/0)处理, 保持原迭代提醒功能可用
MONITOR_DIR = os.path.join(os.path.dirname(BASE_DIR), "volc-monitor")
if MONITOR_DIR not in sys.path:
    sys.path.insert(0, MONITOR_DIR)
try:
    import monitor as _monitor
except Exception as _exc:  # noqa: BLE001
    _monitor = None
    log(f"[warn] 无法导入 volc-monitor/monitor.py, 迭代 1/0 回复功能禁用: {_exc}")


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
def load_env(path=ENV_FILE):
    env = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k:
                    env[k] = v
    except FileNotFoundError:
        pass
    return env


def get_config():
    cfg = dict(DEFAULTS)
    cfg.update(load_env())
    cfg.update({k: v for k, v in os.environ.items() if k in DEFAULTS})
    return cfg


def parse_chat_ids(raw):
    ids = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            continue
    return ids


def split_text(text, limit):
    """把长文本按上限切块, 尽量在换行处断开。"""
    if len(text) <= limit:
        return [text]
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0 or cut < limit // 2:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:]
    chunks.append(text)
    return chunks


# --------------------------------------------------------------------------- #
# Telegram 客户端
# --------------------------------------------------------------------------- #
class Telegram:
    def __init__(self, token, api_base, parse_mode=""):
        self.token = token
        self.base = f"{api_base.rstrip('/')}/bot{token}"
        self.parse_mode = parse_mode
        self.session = requests.Session()
        self.flood_until = 0.0   # Telegram 429 限流退避截止时间戳

    def _retry_after(self, data):
        """从 Telegram 429 响应解析退避秒数(优先 parameters.retry_after)。"""
        params = data.get("parameters") or {}
        if isinstance(params, dict):
            try:
                ra = int(params.get("retry_after") or 0)
                if ra > 0:
                    return ra
            except (TypeError, ValueError):
                pass
        desc = data.get("description") or ""
        m = re.search(r"retry after (\d+)", desc)
        if m:
            return int(m.group(1))
        return 5

    def call(self, method, **params):
        try:
            resp = self.session.post(f"{self.base}/{method}", json=params, timeout=70)
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        if not data.get("ok"):
            desc = data.get("description") or data.get("error") or "unknown"
            if resp.status_code == 429 or "Too Many Requests" in desc:
                self.flood_until = time.time() + self._retry_after(data)
                log(f"[telegram] {method} 触发限流, 退避至 {time.strftime('%H:%M:%S', time.localtime(self.flood_until))}")
            if "not modified" not in desc and "Too Many Requests" not in desc:
                log(f"[telegram] {method} 失败: {desc}")
        return data

    def _with_parse(self, params):
        if self.parse_mode and "parse_mode" not in params:
            params["parse_mode"] = self.parse_mode
        return params

    def send_message(self, chat_id, text):
        data = self.call("sendMessage", **self._with_parse(
            {"chat_id": chat_id, "text": text}))
        if data.get("ok"):
            return data["result"]["message_id"]
        return None

    def edit_message(self, chat_id, message_id, text):
        if time.time() < self.flood_until:  # 限流退避期内直接跳过, 不请求 Telegram
            return False
        data = self.call("editMessageText", **self._with_parse(
            {"chat_id": chat_id, "message_id": message_id, "text": text}))
        if data.get("ok"):
            return True
        # 内容未变化 = 已是最新, 视为成功
        return "not modified" in (data.get("description") or "")

    def send_chat_action(self, chat_id, action="typing"):
        self.call("sendChatAction", chat_id=chat_id, action=action)

    def get_updates(self, offset=None, timeout=25):
        params = {
            "timeout": timeout,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            params["offset"] = offset
        data = self.call("getUpdates", **params)
        return data.get("result") or []

    def download_file(self, file_id):
        info = self.call("getFile", file_id=file_id)
        if not info.get("ok"):
            return None
        file_path = info["result"].get("file_path")
        if not file_path:
            return None
        url = f"{self.base.rsplit('/bot', 1)[0]}/file/bot{self.token}/{file_path}"
        try:
            resp = self.session.get(url, timeout=70)
            resp.raise_for_status()
            return resp.content
        except Exception as exc:  # noqa: BLE001
            log(f"[telegram] 下载文件失败: {exc}")
            return None


# --------------------------------------------------------------------------- #
# Pi RPC 客户端
# --------------------------------------------------------------------------- #
class PiClient:
    def __init__(self, cfg, on_event):
        self.cfg = cfg
        self.on_event = on_event
        self._id = 0
        self._lock = threading.Lock()
        self._pending = {}          # id -> {"event": Event, "data": record}
        self._proc = None
        self._start()

    # ---- 进程管理 ---------------------------------------------------------
    def _pi_bin(self):
        explicit = (self.cfg.get("BOT_PI_BIN") or "").strip()
        if explicit:
            return explicit
        found = shutil.which("pi")
        if found:
            return found
        return "/root/.pi/agent/bin/pi"

    def _start(self):
        cmd = [
            self._pi_bin(), "--mode", "rpc",
            "--model", self.cfg["BOT_MODEL"],
            "--session-id", self.cfg["BOT_SESSION_ID"],
        ]
        if self.cfg.get("BOT_THINKING"):
            cmd += ["--thinking", self.cfg["BOT_THINKING"]]
        if str(self.cfg.get("BOT_APPROVE", "1")).strip().lower() in ("1", "true", "yes", "on"):
            cmd += ["--approve"]
        if (self.cfg.get("BOT_APPEND_PROMPT") or "").strip():
            cmd += ["--append-system-prompt", self.cfg["BOT_APPEND_PROMPT"].strip()]
        env = dict(os.environ)
        if not env.get("HOME"):
            env["HOME"] = "/root"
        log(f"[pi] 启动: {' '.join(cmd)} (cwd={self.cfg['BOT_WORKDIR']})")
        self._proc = subprocess.Popen(
            cmd,
            cwd=self.cfg["BOT_WORKDIR"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        threading.Thread(target=self._read_loop, args=(self._proc.stdout,), daemon=True).start()
        threading.Thread(target=self._err_loop, args=(self._proc.stderr,), daemon=True).start()

    def is_alive(self):
        return self._proc is not None and self._proc.poll() is None

    def restart(self):
        log("[pi] 重启进程")
        try:
            if self._proc is not None:
                if self._proc.stdin:
                    try:
                        self._proc.stdin.close()
                    except Exception:  # noqa: BLE001
                        pass
                if self._proc.poll() is None:
                    self._proc.terminate()
                    try:
                        self._proc.wait(timeout=5)
                    except Exception:  # noqa: BLE001
                        self._proc.kill()
        except Exception:  # noqa: BLE001
            pass
        self._start()

    def shutdown(self):
        try:
            if self._proc and self._proc.stdin:
                self._proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass

    # ---- 读写 -------------------------------------------------------------
    def _next_id(self):
        with self._lock:
            self._id += 1
            return f"req-{self._id}"

    def _write(self, obj):
        if not self.is_alive():
            self.restart()
        try:
            self._proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
            self._proc.stdin.flush()
            return True
        except Exception as exc:  # noqa: BLE001
            log(f"[pi] 写入命令失败: {exc}")
            return False

    def _read_loop(self, stream):
        try:
            for raw in stream:
                line = raw.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._resolve_waiter(record)
                try:
                    self.on_event(record)
                except Exception as exc:  # noqa: BLE001
                    log(f"[pi] on_event 异常: {exc}")
        except Exception as exc:  # noqa: BLE001
            log(f"[pi] stdout 读取结束: {exc}")
        finally:
            log("[pi] stdout 通道关闭")

    def _err_loop(self, stream):
        try:
            for raw in stream:
                line = raw.decode("utf-8", "replace").rstrip("\n")
                if line.strip():
                    log(f"[pi:stderr] {line[:400]}")
        except Exception:  # noqa: BLE001
            pass

    def _resolve_waiter(self, record):
        if record.get("type") != "response":
            return
        rid = record.get("id")
        with self._lock:
            waiter = self._pending.pop(rid, None)
        if waiter:
            waiter["data"] = record
            waiter["event"].set()

    # ---- 命令 -------------------------------------------------------------
    def send_command(self, obj, wait=False, timeout=30):
        rid = self._next_id()
        obj = dict(obj)
        obj["id"] = rid
        waiter = None
        if wait:
            waiter = {"event": threading.Event(), "data": None}
            with self._lock:
                self._pending[rid] = waiter
        ok = self._write(obj)
        if not ok and wait:
            return {"success": False, "error": "写入失败"}
        if wait:
            if waiter["event"].wait(timeout):
                return waiter["data"]
            with self._lock:
                self._pending.pop(rid, None)
            return {"success": False, "error": "等待响应超时"}
        return None

    def prompt(self, message, images=None):
        obj = {"type": "prompt", "message": message}
        if images:
            obj["images"] = images
        return self.send_command(obj)

    def abort(self):
        return self.send_command({"type": "abort"})

    def new_session(self):
        return self.send_command({"type": "new_session"})

    def get_state(self):
        return self.send_command({"type": "get_state"}, wait=True, timeout=20)

    def get_available_models(self):
        return self.send_command({"type": "get_available_models"}, wait=True, timeout=20)

    def set_model(self, provider, model_id):
        return self.send_command(
            {"type": "set_model", "provider": provider, "modelId": model_id},
            wait=True, timeout=20)


# --------------------------------------------------------------------------- #
# 桥接主逻辑
# --------------------------------------------------------------------------- #
class StreamState:
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.text = ""            # 累计的助手文本(答案)
        self.thinking = ""        # 累计的思考过程文本
        self.phase = None         # 当前阶段: None / "thinking" / "tool"
        self.msg_id = None        # 正在编辑的 Telegram 消息 id
        self.tool = None          # 当前工具名
        self.last_body = None      # 上次已发送/编辑的消息正文(去重)
        self.last_edit = 0.0
        self.last_type = 0.0
        self.settled = threading.Event()
        self.finished = False


class Bridge:
    def __init__(self, cfg):
        self.cfg = cfg
        self.tg = Telegram(cfg["TELEGRAM_BOT_TOKEN"], cfg["TELEGRAM_API"], cfg["TELEGRAM_PARSE_MODE"])
        self.authorized = parse_chat_ids(cfg["TELEGRAM_CHAT_ID"])
        self.monitor = _monitor
        self.pi = PiClient(cfg, self.handle_event)
        self.prompt_queue = queue.Queue()
        self.state_lock = threading.Lock()
        self.current = None
        self.worker = threading.Thread(target=self.prompt_worker, daemon=True)
        self.worker.start()

    # ---- 数值便捷读取 -----------------------------------------------------
    def _f(self, key, default):
        try:
            return float(self.cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    # ---- 事件处理(来自 Pi reader 线程) ------------------------------------
    def handle_event(self, record):
        t = record.get("type")
        if t == "response":
            if record.get("command") == "prompt" and not record.get("success"):
                st = self._current_state()
                if st:
                    self._finalize(st, error=record.get("error") or "提示词被拒绝")
            return

        st = self._current_state()
        if st is None:
            return

        if t == "message_update":
            e = record.get("assistantMessageEvent") or {}
            et = e.get("type")
            if et == "text_delta":
                st.text += e.get("delta", "")
                st.phase = None
                self._maybe_refresh(st)
            elif et == "text_end":
                st.text = e.get("content") or st.text
                self._maybe_refresh(st, force=True)
            elif et == "toolcall_start":
                st.tool = e.get("toolName")
                st.phase = "tool"
                self._maybe_refresh(st, force=True)
            elif et == "toolcall_end":
                st.tool = None
                st.phase = None
                self._maybe_refresh(st, force=True)
            elif et == "thinking_delta":
                st.thinking += e.get("delta", "")
                st.phase = "thinking"
                self._maybe_refresh(st)
            elif et == "thinking_end":
                st.thinking = e.get("content") or st.thinking
                st.phase = None
                self._maybe_refresh(st, force=True)
        elif t == "tool_execution_start":
            st.tool = record.get("toolName")
            st.phase = "tool"
            self._maybe_refresh(st, force=True)
        elif t == "tool_execution_end":
            st.tool = None
            st.phase = None
            self._maybe_refresh(st, force=True)
        elif t == "agent_settled":
            self._finalize(st)
        elif t == "message_end":
            # 权威完整消息(可选): 结束前修正文本
            msg = record.get("message") or {}
            if msg.get("role") == "assistant":
                content = msg.get("content") or []
                full = ""
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        full += block.get("text", "")
                if full:
                    st.text = full

    def _current_state(self):
        with self.state_lock:
            return self.current

    def _set_current(self, st):
        with self.state_lock:
            self.current = st

    # ---- 流式渲染 ---------------------------------------------------------
    def _thinking_block(self, thinking, tail=None, final_max=None):
        if not thinking:
            return ""
        t = esc(md_to_plain(thinking))
        if final_max:
            if len(t) > final_max:
                t = t[:final_max] + "\n…(思考过长, 已省略)"
        else:
            tail = tail or 1500
            if len(t) > tail:
                t = "…(前文已省略)\n" + t[-tail:]
        return f"<tg-spoiler>💭 思考过程\n{t}</tg-spoiler>\n"

    def _build_body(self, st, cursor=True):
        parts = []
        if st.phase == "thinking":
            parts.append("💭 深度思考中…\n")
        elif st.phase == "tool" and st.tool:
            parts.append(f"🔧 {esc(st.tool)}\n")
        if st.thinking:
            parts.append(self._thinking_block(st.thinking, tail=int(self._f("THINKING_TAIL", 1500))))
        # 答案尾部长度按思考块长度动态收缩, 确保总长不超过 Telegram 上限
        prefix_len = len("".join(parts))
        tail = int(self._f("STREAM_TAIL", 3800))
        tail = min(tail, max(200, 4000 - prefix_len - 8))
        text = esc(md_to_plain(st.text))
        if len(text) > tail:
            text = "…（前文已省略）\n" + text[-tail:]
        parts.append(text)
        body = "".join(parts)
        if cursor and not st.finished:
            body += " ▌"
        return body

    def _maybe_refresh(self, st, force=False):
        if st.finished:
            return
        now = time.time()
        if now - st.last_type > self._f("TYPING_INTERVAL", 4.0):
            self.tg.send_chat_action(st.chat_id, "typing")
            st.last_type = now

        interval = self._f("STREAM_EDIT_INTERVAL", 1.2)
        body = self._build_body(st)
        if st.msg_id is None:
            mid = self.tg.send_message(st.chat_id, body)
            if mid:
                st.msg_id = mid
                st.last_body = body
                st.last_edit = now
            return
        if body == st.last_body:
            return
        if not force and now - st.last_edit < interval:
            return
        if self.tg.edit_message(st.chat_id, st.msg_id, body):
            st.last_body = body
        st.last_edit = now  # 失败也更新时间, 避免限流时每个事件都立即重试

    def _finalize(self, st, error=None):
        if st.finished:
            return
        st.finished = True
        # 若 Telegram 仍在限流退避期, 等待其结束再定稿, 确保最终答案可靠送达
        wait = self.tg.flood_until - time.time()
        if wait > 0:
            log(f"[bridge] 限流退避中, 等待 {wait:.1f}s 后定稿")
            time.sleep(wait + 0.3)
        try:
            limit = int(self._f("MSG_LIMIT", 4096))
            thinking = self._thinking_block(st.thinking, final_max=int(self._f("THINKING_FINAL_MAX", 3000)))
            if error:
                answer = f"⚠️ {esc(error)}"
            else:
                answer = esc(md_to_plain(st.text or "（无文本输出）"))
            # 思考(spoiler)固定放在首条, 答案可跨多条切分
            head_limit = max(1000, limit - len(thinking))
            chunks = split_text(answer, head_limit)
            if thinking:
                chunks[0] = thinking + chunks[0]
            for i, chunk in enumerate(chunks):
                if i == 0 and st.msg_id is not None:
                    if not self.tg.edit_message(st.chat_id, st.msg_id, chunk):
                        self.tg.send_message(st.chat_id, chunk)
                else:
                    self.tg.send_message(st.chat_id, chunk)
        except Exception as exc:  # noqa: BLE001
            log(f"[bridge] finalize 异常: {exc}")
        finally:
            st.settled.set()

    # ---- 提示词队列 -------------------------------------------------------
    def enqueue_prompt(self, chat_id, text, images=None):
        self.prompt_queue.put((chat_id, text, images))
        qsize = self.prompt_queue.qsize()
        if qsize > 1:
            self.tg.send_message(chat_id, f"⏳ 已排队(前面还有 {qsize - 1} 条)")

    def prompt_worker(self):
        while True:
            chat_id, text, images = self.prompt_queue.get()
            st = StreamState(chat_id)
            self._set_current(st)
            try:
                self.tg.send_chat_action(chat_id, "typing")
                st.last_type = time.time()
                self.pi.prompt(text, images=images)
                timeout = self._f("PROMPT_TIMEOUT", 1800)
                deadline = time.time() + timeout
                while not st.finished:
                    st.settled.wait(timeout=1.0)
                    if st.finished:
                        break
                    if not self.pi.is_alive():
                        self.pi.restart()
                        self._finalize(st, error="Pi 进程异常退出, 已自动重启; 请重试")
                        break
                    if time.time() > deadline:
                        self.pi.abort()
                        self._finalize(st, error=f"请求超时({int(timeout)}s), 已中止")
                        break
            except Exception as exc:  # noqa: BLE001
                log(f"[bridge] worker 异常: {exc}")
                self._finalize(st, error=f"桥接异常: {exc}")
            finally:
                self._set_current(None)

    # ---- 命令 -------------------------------------------------------------
    def handle_command(self, chat_id, text):
        cmd, _, arg = text.partition(" ")
        cmd = cmd.lower()
        arg = arg.strip()

        if cmd in ("/start", "/help"):
            # 帮助文本含 HTML, 单独用 HTML 发送
            self.tg.call("sendMessage", chat_id=chat_id, text=HELP_TEXT, parse_mode="HTML")
        elif cmd == "/new":
            self.pi.new_session()
            self.tg.send_message(chat_id, "✅ 已开启新会话")
        elif cmd == "/abort":
            self.pi.abort()
            self.tg.send_message(chat_id, "🛑 已发送中止指令")
        elif cmd == "/status":
            data = self.pi.get_state()
            self._reply_status(chat_id, data)
        elif cmd == "/model":
            if arg:
                self._reply_set_model(chat_id, arg)
            else:
                self._reply_models(chat_id)
        else:
            self.tg.send_message(chat_id, "未知命令，输入 /help 查看用法")

    def _reply_status(self, chat_id, data):
        if not data or not data.get("success"):
            self.tg.send_message(chat_id, "⚠️ 无法获取状态")
            return
        d = data.get("data") or {}
        model = d.get("model") or {}
        mid = esc(model.get("id") or model.get("name") or "-")
        lines = [
            f"模型: {mid}",
            f"思考级别: {esc(str(d.get('thinkingLevel', '-')))}",
            f"消息数: {d.get('messageCount', 0)}",
            f"待处理: {d.get('pendingMessageCount', 0)}",
            f"运行中: {'是' if d.get('isStreaming') else '否'}",
            f"会话 id: {esc(str(d.get('sessionId', '-')))}",
        ]
        self.tg.send_message(chat_id, "📊 " + "\n".join(lines))

    def _reply_models(self, chat_id):
        data = self.pi.get_available_models()
        if not data or not data.get("success"):
            self.tg.send_message(chat_id, "⚠️ 无法获取模型列表")
            return
        models = (data.get("data") or {}).get("models") or []
        if not models:
            self.tg.send_message(chat_id, "⚠️ 无可用模型")
            return
        lines = []
        for m in models:
            mid = esc(m.get("id") or m.get("name") or "?")
            ctx = m.get("contextWindow", "-")
            img = "🖼" if m.get("input") and "image" in m.get("input", []) else "📝"
            lines.append(f"{img} {mid}  (ctx {ctx})")
        self.tg.send_message(chat_id, "可用模型:\n" + "\n".join(lines) + "\n\n切换: /model <模型id>")

    def _reply_set_model(self, chat_id, model_id):
        data = self.pi.get_available_models()
        models = ((data or {}).get("data") or {}).get("models") or []
        target = None
        for m in models:
            mid = m.get("id") or ""
            if mid == model_id or model_id in mid or (mid and mid in model_id):
                target = m
                break
        if target is None:
            self.tg.send_message(chat_id, f"⚠️ 未找到模型: {model_id} (用 /model 查看可用列表)")
            return
        provider = target.get("provider") or "deepseek"
        res = self.pi.set_model(provider, target.get("id"))
        if res and res.get("success"):
            m = (res.get("data") or {}).get("model") or {}
            name = esc(m.get("id") or m.get("name") or model_id)
            self.tg.send_message(chat_id, f"✅ 已切换模型: {name}")
        else:
            err = esc((res or {}).get("error") or "未知错误")
            self.tg.send_message(chat_id, f"⚠️ 切换失败: {err}")

    # ---- 消息入口 ---------------------------------------------------------
    def handle_message(self, msg):
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id not in self.authorized:
            log(f"[auth] 忽略未授权 chat_id={chat_id}")
            return

        text = (msg.get("text") or "").strip()
        photos = msg.get("photo") or []

        if text.startswith("/"):
            self.handle_command(chat_id, text)
            return

        # 迭代提醒的 1/0 回复交给 monitor 处理(保持原迭代功能), 不走 Pi
        if self._maybe_handle_decision(chat_id, text):
            return

        # 需求收集进行中时, 普通文本也交给 monitor 记录(不走 Pi)
        if self._maybe_handle_collection(chat_id, text):
            return

        images = self._extract_images(photos)
        if images and not text:
            text = "请描述这张图片，或告诉我你想让我做什么。"
        if not text and not images:
            return

        self.enqueue_prompt(chat_id, text, images=images)

    def _extract_images(self, photos):
        """下载 Telegram 图片并转成 Pi 的 image 输入。"""
        images = []
        for size_list in photos or []:
            if not size_list:
                continue
            # 取最大尺寸
            largest = max(size_list, key=lambda s: (s.get("width", 0) * s.get("height", 0)))
            fid = largest.get("file_id")
            if not fid:
                continue
            content = self.tg.download_file(fid)
            if content:
                images.append({
                    "type": "image",
                    "data": base64.b64encode(content).decode("ascii"),
                    "mimeType": "image/jpeg",
                })
        return images or None

    def _maybe_handle_decision(self, chat_id, text):
        """处理迭代提醒的 1/0 回复; 返回 True 表示已处理。"""
        if text not in ("1", "0") or self.monitor is None:
            return False
        try:
            state = self.monitor.migrate_state(self.monitor.load_state())
            last_prompt = state.get("last_iteration_prompt_ts") or 0
            last_decision = state.get("last_decision") or {}
            if not last_prompt:
                return False
            # 已回复过该次提醒, 不再拦截
            if last_decision and last_decision.get("ts", 0) >= last_prompt:
                return False
            # 提醒时间过久(>30 天)视为过期, 不拦截
            if time.time() - last_prompt > 30 * 86400:
                return False
            cfg = self.monitor.get_config()
            self.monitor.handle_decision(cfg, state, str(chat_id), text)
            return True
        except Exception as exc:  # noqa: BLE001
            log(f"[decision] 处理迭代决策失败: {exc}")
            return False

    def _maybe_handle_collection(self, chat_id, text):
        """需求收集进行中时, 把普通文本转发给 monitor 记录(不走 Pi)。"""
        if not text or self.monitor is None:
            return False
        try:
            state = self.monitor.migrate_state(self.monitor.load_state())
            collect = state.get("iteration_collect") or {}
            if not collect.get("active"):
                return False
            if str(collect.get("chat_id")) != str(chat_id):
                return False
            cfg = self.monitor.get_config()
            self.monitor.handle_decision(cfg, state, str(chat_id), text)
            return True
        except Exception as exc:  # noqa: BLE001
            log(f"[collect] 处理需求记录失败: {exc}")
            return False

    # ---- 轮询 -------------------------------------------------------------
    def poll_loop(self, once=False):
        offset = None
        while True:
            try:
                updates = self.tg.get_updates(offset=offset, timeout=int(self._f("POLL_TIMEOUT", 25)))
            except Exception as exc:  # noqa: BLE001
                log(f"[poll] getUpdates 异常: {exc}")
                time.sleep(3)
                continue
            for upd in updates:
                offset = upd["update_id"] + 1
                msg = upd.get("message")
                if msg:
                    self.handle_message(msg)
            if once:
                break


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Telegram <-> Pi 对话桥接")
    ap.add_argument("--once", action="store_true", help="处理一轮 pending 更新后退出")
    args = ap.parse_args()

    cfg = get_config()
    if not cfg["TELEGRAM_BOT_TOKEN"]:
        sys.stderr.write("缺少 TELEGRAM_BOT_TOKEN (请检查 .env)\n")
        return 2
    if not parse_chat_ids(cfg["TELEGRAM_CHAT_ID"]):
        sys.stderr.write("缺少授权 TELEGRAM_CHAT_ID (请检查 .env)\n")
        return 2

    bridge = Bridge(cfg)

    def _on_signal(signum, _frame):
        log(f"收到信号 {signum}, 退出")
        bridge.pi.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    log(f"osint_bot 启动, 授权 chat_id: {sorted(bridge.authorized)}")
    try:
        bridge.poll_loop(once=args.once)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.pi.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
