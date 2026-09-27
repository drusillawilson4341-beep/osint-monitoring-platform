#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
获取 Telegram 机器人对话的 chat_id。

用法:
    1) 在 Telegram 中打开你的机器人 (@OSINTT_Mine_bot) 并发送任意消息(如 /start)
    2) 运行:  python3 get_chat_id.py
    3) 脚本轮询 getUpdates,发现消息后打印 chat_id 并写入 .env
"""

import os
import sys
import time

try:
    import requests
except ImportError:
    sys.stderr.write("缺少依赖 requests\n")
    sys.exit(1)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BASE_DIR, ".env")


def load_token():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("TELEGRAM_BOT_TOKEN="):
                        token = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        except FileNotFoundError:
            pass
    return token


def set_env_value(key, value):
    """把 key=value 写回 .env(不存在则追加)。"""
    lines = []
    found = False
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith(key + "="):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    token = load_token()
    if not token:
        print("[error] 未找到 TELEGRAM_BOT_TOKEN (请检查 .env)")
        return 2

    print("[info] 等待消息... 请在 Telegram 给机器人发送任意消息(/start)")
    print("[info] 最多等待 300 秒, Ctrl+C 退出")

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    deadline = time.time() + 300
    offset = None
    while time.time() < deadline:
        try:
            params = {"timeout": 10}
            if offset is not None:
                params["offset"] = offset
            resp = requests.get(url, params=params, timeout=25)
            resp.raise_for_status()
            data = resp.json()
            for upd in data.get("result") or []:
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("channel_post") or {}
                chat = msg.get("chat") or {}
                cid = chat.get("id")
                if cid:
                    ctype = chat.get("type", "")
                    title = chat.get("title") or chat.get("first_name") or chat.get("username") or ""
                    print(f"[ok] 发现 chat_id: {cid}  (类型: {ctype}, 名称: {title})")
                    set_env_value("TELEGRAM_CHAT_ID", str(cid))
                    print(f"[ok] 已写入 {ENV_FILE}")
                    return 0
        except requests.RequestException as exc:
            print(f"[warn] 轮询失败: {exc}", file=sys.stderr)
        time.sleep(2)

    print("[warn] 超时未发现消息, 请先给机器人发消息再重试")
    return 1


if __name__ == "__main__":
    sys.exit(main())
