#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多源业务公告/更新监控 -> Telegram 推送

支持的数据源:
  1. volcengine.com  - 火山引擎官方公告(产品/平台/安全公告等)
  2. douyin.com      - 抖音开放平台公告(小程序/小游戏/需求反馈等)
  3. feishu.cn       - 飞书开放平台更新日志(飞书 CLI / 开放平台能力更新)
  4. assets          - 域名资产动态(证书透明日志 crt.sh, 检索上述域名当月新证书/新子域)

每个数据源只推送"新出现"的条目, 首次运行建立基线(不推送)。
推送前自动删除超过 RETENTION_DAYS 天的已推送消息。

运行:
    python3 monitor.py --once        # 单次检查(默认)
    python3 monitor.py --baseline    # 仅记录当前条目为已见,不推送
    python3 monitor.py --send-now    # 立即推送最近 N 条(测试/补发)
    python3 monitor.py --loop        # 常驻循环检查
    python3 monitor.py --dry-run     # 只打印不推送、不写状态
"""

import argparse
import contextlib
import datetime
import html
import json
import os
import re
import sys
import time

try:
    import fcntl
except ImportError:  # pragma: no cover - 非 Linux 环境
    fcntl = None

try:
    import requests
except ImportError:  # pragma: no cover
    sys.stderr.write("缺少依赖 requests，请执行: pip3 install requests\n")
    sys.exit(1)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BASE_DIR, ".env")
STATE_FILE = os.path.join(BASE_DIR, "state.json")
LOCK_FILE = os.path.join(BASE_DIR, "state.lock")

DEFAULTS = {
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_CHAT_ID": "",
    "TELEGRAM_PARSE_MODE": "HTML",
    "SOURCES": "volcengine,douyin,feishu,assets",
    "MAX_SEND": "10",
    "PREVIEW_LEN": "600",
    "CURRENT_MONTH_ONLY": "1",          # 1=只推送当月消息, 0=推送所有新消息
    "LOOP_INTERVAL": "21600",
    "RETENTION_DAYS": "30",
    "ITERATION_ENABLED": "1",              # 1=每两周询问是否迭代
    "ITERATION_INTERVAL_DAYS": "14",       # 询问周期(天)
    "ITERATION_PROMPT": ("🗓️ <b>迭代提醒</b>\n\n"
                          "是否需要对本监控系统进行迭代更新？（每 2 周自动提醒一次）\n"
                          "回复：<code>1</code> 需要 / <code>0</code> 暂不需要"),
    "STATE_MAX_IDS": "2000",
    # volcengine
    "VOLC_TYPES": "188,189",
    "VOLC_LIMIT": "20",
    # douyin 开放平台公告 Biz(1=小游戏,2=小程序,4=需求反馈)
    "DOUYIN_BIZ": "1,2,4",
    "DOUYIN_LIMIT": "20",
    # feishu 开放平台更新日志
    "FEISHU_LIMIT": "20",
    # 域名资产监控(证书透明日志 crt.sh, 检索这些域名当月新证书/新子域)
    "ASSETS_DOMAINS": "volcengine.com,douyin.com,feishu.cn,arc.io",
    "ASSETS_LIMIT": "30",
}

SOURCE_LABELS = {
    "volcengine": "火山引擎",
    "douyin": "抖音开放平台",
    "feishu": "飞书开放平台",
    "assets": "域名资产(证书)",
}


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


# --------------------------------------------------------------------------- #
# 文本工具
# --------------------------------------------------------------------------- #
def md_to_text(md):
    text = html.unescape(md or "")
    text = re.sub(r"\\([-*_#`>])", r"\1", text)   # Markdown 转义符
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"`{1,3}[^`]*`{1,3}", " ", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s{0,3}[-*+]\s+", "• ", text, flags=re.M)
    text = re.sub(r"^\s{0,3}\d+[.)]\s+", "", text, flags=re.M)
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.M)
    text = re.sub(r"[|]", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def html_to_text(raw):
    text = raw or ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def truncate(text, limit):
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def ts_to_str(ts):
    try:
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return str(ts)


def current_month_start_ts():
    """当月(北京时间)起始时间戳。"""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:  # noqa: BLE001
        now = datetime.datetime.now()
    return int(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())


def current_month_label():
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m")
    except Exception:  # noqa: BLE001
        return datetime.datetime.now().strftime("%Y-%m")


# --------------------------------------------------------------------------- #
# 数据源适配器: 返回 [{source,id,title,content,category,time,ts,link}]
# --------------------------------------------------------------------------- #
def fetch_volcengine(cfg):
    api_base = "https://www.volcengine.com"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36",
        "Accept": "application/json",
        "x-use-bff-version": "1",
    }
    items = []
    type_ids = [t.strip() for t in str(cfg["VOLC_TYPES"]).split(",") if t.strip()]
    limit = int(cfg["VOLC_LIMIT"])
    for tid in type_ids:
        resp = requests.get(
            f"{api_base}/api/notice/noticeList",
            params={"Type": tid, "Limit": limit, "Offset": 0},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        result = (resp.json().get("Result") or {})
        for n in result.get("NoticeList") or []:
            pub = n.get("PublishedTime") or ""
            items.append({
                "source": "volcengine",
                "id": f"volcengine:{n.get('Id')}",
                "title": n.get("Title") or "(无标题)",
                "content": n.get("Content") or "",
                "category": " / ".join(x for x in (n.get("Type"), n.get("SubType")) if x) or "公告",
                "time": pub,
                "ts": parse_time(pub),
                "link": f"{api_base}/notice/{n.get('TypeId')}/{n.get('Id')}",
            })
    return items


def fetch_douyin(cfg):
    api = "https://developer.open-douyin.com/bff_api_v2/AnnouncementV2Service/GetAnnouncementList"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36",
        "Referer": "https://developer.open-douyin.com/announcement",
    }
    items = []
    biz_list = [b.strip() for b in str(cfg["DOUYIN_BIZ"]).split(",") if b.strip()]
    limit = int(cfg["DOUYIN_LIMIT"])
    for biz in biz_list:
        resp = requests.get(
            api,
            params={"Biz": biz, "PageNo": 1, "PageSize": limit, "Sort": 1, "SortOrder": "desc"},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = (resp.json().get("data") or {})
        for a in data.get("Announcements") or []:
            items.append({
                "source": "douyin",
                "id": f"douyin:{a.get('Id')}",
                "title": a.get("Title") or "(无标题)",
                "content": html_to_text(a.get("Content")),
                "category": a.get("TypeName") or f"Biz{biz}",
                "time": ts_to_str(a.get("Ctime") or a.get("OnlineTime")),
                "ts": int(a.get("Ctime") or a.get("OnlineTime") or 0),
                "link": f"https://partner.open-douyin.com/announcement/{a.get('Id')}",
            })
    return items


def fetch_feishu(cfg):
    api = "https://open.feishu.cn/api/tools/change_log/change_log_list"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36",
        "Content-Type": "application/json",
    }
    resp = requests.post(
        api,
        json={"page": 1, "pageSize": int(cfg["FEISHU_LIMIT"])},
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    data = (resp.json().get("data") or {})
    items = []
    for c in data.get("changeLogs") or []:
        content = c.get("content") or ""
        items.append({
            "source": "feishu",
            "id": f"feishu:{c.get('id')}",
            "title": extract_feishu_title(content),
            "content": content,
            "category": c.get("abilityType") or "更新",
            "time": ts_to_str(c.get("changeDate")),
            "ts": int(c.get("changeDate") or 0),
            "link": "https://open.feishu.cn/changelog",
        })
    return items


def extract_feishu_title(content):
    m = re.search(r"^\s*#{1,3}\s*(.+?)\s*$", content or "", re.M)
    if not m:
        return "飞书更新"
    title = html.unescape(m.group(1)).strip()
    return re.sub(r"\\([-*_#`>])", r"\1", title)


def parse_time(s):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return int(datetime.datetime.strptime(s, fmt).timestamp())
        except (ValueError, TypeError):
            continue
    return 0


def parse_crt_time(s):
    """解析 crt.sh 时间(兼容 ISO 带 T/带秒/仅日期)。"""
    if not s:
        return 0
    s = str(s).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return int(datetime.datetime.strptime(s, fmt).timestamp())
        except ValueError:
            continue
    return 0


def expand_crt_names(name_value, domain):
    """从证书 SAN 中提取属于目标域名的子域名(去通配符/邮箱/证书名)。"""
    names = set()
    for raw in (name_value or "").split("\n"):
        n = raw.strip().lower().lstrip("*.")
        if not n or not n.endswith(domain):
            continue
        if re.fullmatch(r"[a-z0-9._-]+", n) and "@" not in n:
            names.add(n)
    return sorted(names)


def fetch_assets(cfg):
    """域名资产动态: 通过 crt.sh 证书透明日志检索目标域名当月新签发的证书(反映新子域名/新资产)。"""
    domains = [d.strip().lower().lstrip("*.")
               for d in str(cfg["ASSETS_DOMAINS"]).split(",") if d.strip()]
    limit = int(cfg["ASSETS_LIMIT"])
    month_start = current_month_start_ts()
    items = []
    seen_certs = set()
    for domain in domains:
        data = None
        for attempt in range(1, 4):
            try:
                resp = requests.get(
                    "https://crt.sh/",
                    params={"q": f"%.{domain}", "output": "json"},
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                    timeout=40,
                )
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] [域名资产] crt.sh 查询 {domain} 第{attempt}次失败: {exc}", file=sys.stderr)
                time.sleep(3 * attempt)
        if data is None:
            continue
        for cert in data:
            entry_ts = parse_crt_time(cert.get("not_before"))
            if not entry_ts or entry_ts < month_start:
                continue
            cert_id = cert.get("id")
            if not cert_id or cert_id in seen_certs:
                continue
            names = expand_crt_names(cert.get("name_value"), domain)
            if not names:
                continue
            seen_certs.add(cert_id)
            na = parse_crt_time(cert.get("not_after"))
            issuer = (cert.get("issuer_name") or "").strip() or "未知"
            title = "、".join(names[:6]) + (" 等" if len(names) > 6 else "")
            content = (
                f"域名: {', '.join(names[:10])}\n"
                f"签发: {ts_to_str(entry_ts)}\n"
                f"到期: {ts_to_str(na) if na else '未知'}\n"
                f"签发机构: {issuer}"
            )
            items.append({
                "source": "assets",
                "id": f"assets:{cert_id}",
                "title": title,
                "content": content,
                "category": domain,
                "time": ts_to_str(entry_ts),
                "ts": entry_ts,
                "link": f"https://crt.sh/?id={cert_id}",
            })
    items = sorted(items, key=lambda x: x["ts"], reverse=True)[:limit]
    return items


FETCHERS = {
    "volcengine": fetch_volcengine,
    "douyin": fetch_douyin,
    "feishu": fetch_feishu,
    "assets": fetch_assets,
}


# --------------------------------------------------------------------------- #
# 状态记忆(去重 + 已推送消息)
# --------------------------------------------------------------------------- #
def load_state():
    with _state_lock():
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (ValueError, OSError):
                return {}
        return {}


@contextlib.contextmanager
def _state_lock():
    if fcntl is None:
        yield
        return
    with open(LOCK_FILE, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def migrate_state(state):
    """兼容旧版 state.json: 旧版 seen_ids 为原始 Id 列表(无源前缀)。"""
    seen = state.get("seen_ids")
    if isinstance(seen, list):
        state["seen_ids"] = {"volcengine": [f"volcengine:{i}" for i in seen]}
    if isinstance(state.get("seen_ids"), dict):
        for src in list(state["seen_ids"].keys()):
            ids = []
            for i in state["seen_ids"][src]:
                s = str(i)
                ids.append(s if s.startswith(f"{src}:") else f"{src}:{s}")
            state["seen_ids"][src] = list(dict.fromkeys(ids))
    else:
        state["seen_ids"] = {}
    return state


def save_state(state):
    with _state_lock():
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)


def record_sent(state, chat_id, message_id):
    sent = state.setdefault("sent", {})
    sent.setdefault(str(chat_id), []).append({"mid": message_id, "ts": int(time.time())})


def delete_message(token, chat_id, message_id):
    return requests.post(
        f"https://api.telegram.org/bot{token}/deleteMessage",
        data={"chat_id": chat_id, "message_id": message_id},
        timeout=30,
    )


def cleanup_old_messages(cfg, state):
    """推送前删除超过保留期(RETENTION_DAYS)的已推送消息。"""
    token = cfg["TELEGRAM_BOT_TOKEN"].strip()
    cutoff = time.time() - int(cfg["RETENTION_DAYS"]) * 86400
    sent = state.get("sent") or {}
    removed = 0
    for cid in list(sent.keys()):
        kept = []
        for m in sent[cid]:
            if m.get("ts", 0) >= cutoff:
                kept.append(m)
                continue
            resp = delete_message(token, cid, m["mid"])
            if resp.status_code == 200 and resp.json().get("ok") is True:
                removed += 1
                print(f"[cleanup] 已删除过期消息 chat={cid} mid={m['mid']}")
                continue
            desc = ""
            try:
                desc = (resp.json().get("description") or "").lower()
            except ValueError:
                pass
            if "not found" in desc or "message to delete" in desc:
                print(f"[cleanup] 消息已不存在,移除记录 chat={cid} mid={m['mid']}")
                continue
            kept.append(m)
            print(f"[cleanup] 删除失败,稍后重试 chat={cid} mid={m['mid']}: {desc or resp.status_code}",
                  file=sys.stderr)
        if kept:
            sent[cid] = kept
        else:
            del sent[cid]
    state["sent"] = sent
    return removed


# --------------------------------------------------------------------------- #
# Telegram 推送
# --------------------------------------------------------------------------- #
def send_telegram(token, chat_id, text, parse_mode="HTML"):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def format_notice(item, preview_len):
    label = SOURCE_LABELS.get(item["source"], item["source"])
    esc = html.escape
    lines = [
        f"🔔 <b>【{esc(label)}】新动态</b>",
        "",
        f"📌 <b>{esc(item['title'])}</b>",
        f"🏷 <b>分类:</b> {esc(item['category'])}",
        f"🕒 <b>时间:</b> {esc(item['time'])}",
    ]
    preview = truncate(md_to_text(item["content"]), preview_len)
    if preview:
        lines += ["", esc(preview)]
    lines += ["", f"🔗 <a href=\"{esc(item['link'])}\">{esc(item['link'])}</a>"]
    return "\n".join(lines)


def maybe_send_iteration_prompt(cfg, state, chat_ids, force=False):
    """每两周询问一次是否迭代(推送迭代提醒)。"""
    if str(cfg["ITERATION_ENABLED"]).strip() in ("0", "false", "no", ""):
        return False
    interval = max(3600, int(cfg["ITERATION_INTERVAL_DAYS"]) * 86400)
    last = state.get("last_iteration_prompt_ts") or 0
    now = int(time.time())
    if not force and last and (now - last) < interval:
        return False
    token = cfg["TELEGRAM_BOT_TOKEN"].strip()
    text = cfg["ITERATION_PROMPT"].strip()
    sent_any = False
    for cid in chat_ids:
        try:
            data = send_telegram(token, cid, text, "HTML")
            mid = (data.get("result") or {}).get("message_id")
            if mid:
                record_sent(state, cid, mid)
            print(f"[ok] 已发送迭代提醒 -> chat {cid}")
            sent_any = True
        except Exception as exc:  # noqa: BLE001
            print(f"[error] 发送迭代提醒失败 chat={cid}: {exc}", file=sys.stderr)
    if sent_any:
        state["last_iteration_prompt_ts"] = now
    return sent_any


def get_updates(token, offset=0, timeout=30):
    resp = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        params={"offset": offset, "timeout": timeout, "allowed_updates": '["message"]'},
        timeout=timeout + 15,
    )
    resp.raise_for_status()
    return resp.json().get("result") or []


def handle_decision(cfg, state, chat_id, text):
    """处理用户消息: 1/0 决策 + 需求清单收集。"""
    token = cfg["TELEGRAM_BOT_TOKEN"].strip()
    text = text.strip()
    collect = state.get("iteration_collect") or {}
    collecting = bool(collect.get("active"))

    if text == "1":
        state["last_decision"] = {"value": "1", "ts": int(time.time()), "chat_id": str(chat_id)}
        state["last_iteration_prompt_ts"] = int(time.time())
        if not collecting:
            state["iteration_collect"] = {
                "active": True, "started_ts": int(time.time()),
                "chat_id": str(chat_id), "items": [],
            }
            _send_reply(token, chat_id,
                        "✅ 已收到：需要迭代。\n\n请逐条发送本次迭代需求，发送完毕后回复「完成」。", state)
        else:
            _send_reply(token, chat_id, "✅ 已收到：需要迭代（继续发送需求，或回复「完成」结束）。", state)
        save_state(state)
        return

    if text == "0":
        state["last_decision"] = {"value": "0", "ts": int(time.time()), "chat_id": str(chat_id)}
        state["last_iteration_prompt_ts"] = int(time.time())
        if collecting:
            state["iteration_collect"] = {"active": False, "chat_id": str(chat_id), "items": []}
            _send_reply(token, chat_id, "✅ 已收到：暂不迭代。本次需求收集已取消。", state)
        else:
            _send_reply(token, chat_id, "✅ 已收到：暂不迭代，下次将在 14 天后再次询问。", state)
        save_state(state)
        return

    if collecting:
        t = text.lower()
        if t in STOP_WORDS:
            finalize_collection(cfg, state, chat_id)
            return
        if t in CANCEL_WORDS:
            state["iteration_collect"] = {"active": False, "chat_id": str(chat_id), "items": []}
            _send_reply(token, chat_id, "已取消本次需求收集。", state)
            save_state(state)
            return
        items = collect.setdefault("items", [])
        items.append(text)
        _send_reply(token, chat_id, f"✅ 已记录第 {len(items)} 条需求：{text}", state)
        save_state(state)
        return

    # 其它消息: 忽略(仅推进 offset)
    save_state(state)


REQUIREMENTS_FILE = os.path.join(BASE_DIR, "requirements.log")
STOP_WORDS = {"完成", "结束", "done", "/done", "finish", "/finish", "好了", "ok"}
CANCEL_WORDS = {"取消", "cancel", "/cancel"}


def _send_reply(token, chat_id, text, state, parse_mode=None):
    try:
        data = send_telegram(token, chat_id, text, parse_mode)
        mid = (data.get("result") or {}).get("message_id")
        if mid:
            record_sent(state, chat_id, mid)
        print(f"[ok] 回复 -> chat {chat_id}: {text[:50]}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[error] 回复失败 chat={chat_id}: {exc}", file=sys.stderr)
        return False


def append_requirements_file(entry):
    when = datetime.datetime.fromtimestamp(entry["ts"]).strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"\n## {when}  (chat {entry['chat_id']})"]
    for i, t in enumerate(entry["items"], 1):
        lines.append(f"{i}. {t}")
    with open(REQUIREMENTS_FILE, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def finalize_collection(cfg, state, chat_id):
    token = cfg["TELEGRAM_BOT_TOKEN"].strip()
    collect = state.get("iteration_collect") or {}
    items = collect.get("items") or []
    if items:
        entry = {"ts": int(time.time()), "chat_id": str(chat_id), "items": items}
        state.setdefault("requirements_history", []).append(entry)
        append_requirements_file(entry)
        summary = "✅ 本次共记录 {} 条需求：\n{}".format(
            len(items), "\n".join(f"{i}. {t}" for i, t in enumerate(items, 1)))
    else:
        summary = "本次未记录任何需求。"
    state["iteration_collect"] = {"active": False, "chat_id": str(chat_id), "items": []}
    _send_reply(token, chat_id, summary, state)
    save_state(state)
    print(f"[ok] 需求收集完成 chat={chat_id}, 共 {len(items)} 条")


def poll_mode(cfg):
    """常驻轮询, 自动读取用户回复的 1/0。"""
    token = cfg["TELEGRAM_BOT_TOKEN"].strip()
    chat_ids = {c.strip() for c in cfg["TELEGRAM_CHAT_ID"].split(",") if c.strip()}
    if not token:
        print("[error] 未配置 TELEGRAM_BOT_TOKEN", file=sys.stderr)
        return 2
    if not chat_ids:
        print("[warn] 未配置 TELEGRAM_CHAT_ID, 仅监听但不会处理回复", file=sys.stderr)

    state = migrate_state(load_state())
    offset = state.get("update_offset") or 0
    if not offset:
        try:
            pending = get_updates(token, 0, timeout=0)
            if pending:
                offset = max(u["update_id"] for u in pending) + 1
                state["update_offset"] = offset
                save_state(state)
                print(f"[info] 首次轮询, 跳过历史 {len(pending)} 条消息 (offset={offset})")
        except Exception as exc:  # noqa: BLE001
            print(f"[error] 初始化 getUpdates 失败: {exc}", file=sys.stderr)

    print("[info] 轮询模式启动, 监听 1/0 回复 (Ctrl+C 退出)")
    while True:
        try:
            updates = get_updates(token, offset, timeout=30)
            for upd in updates:
                offset = upd["update_id"] + 1
                state["update_offset"] = offset
                msg = upd.get("message") or {}
                chat = msg.get("chat") or {}
                cid = str(chat.get("id"))
                text = (msg.get("text") or "").strip()
                if cid in chat_ids and text:
                    handle_decision(cfg, state, cid, text)
                else:
                    save_state(state)
        except Exception as exc:  # noqa: BLE001
            print(f"[error] 轮询异常: {exc}", file=sys.stderr)
            time.sleep(5)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def run_once(cfg, baseline=False, send_now=False, dry_run=False, force_iteration=False):
    token = cfg["TELEGRAM_BOT_TOKEN"].strip()
    chat_ids = [c.strip() for c in cfg["TELEGRAM_CHAT_ID"].split(",") if c.strip()]
    parse_mode = cfg["TELEGRAM_PARSE_MODE"].strip() or "HTML"
    sources = [s.strip() for s in cfg["SOURCES"].split(",") if s.strip()]

    if not token:
        print("[error] 未配置 TELEGRAM_BOT_TOKEN", file=sys.stderr)
        return 2

    all_items = []
    for src in sources:
        fetcher = FETCHERS.get(src)
        if not fetcher:
            print(f"[warn] 未知数据源 {src}, 已跳过", file=sys.stderr)
            continue
        try:
            items = fetcher(cfg)
            all_items.extend(items)
            print(f"[info] [{SOURCE_LABELS.get(src, src)}] 抓取 {len(items)} 条")
        except Exception as exc:  # noqa: BLE001
            print(f"[error] [{SOURCE_LABELS.get(src, src)}] 抓取失败: {exc}", file=sys.stderr)

    # 只保留当月消息
    if str(cfg["CURRENT_MONTH_ONLY"]).strip() not in ("0", "false", "no", ""):
        month_start = current_month_start_ts()
        before = len(all_items)
        all_items = [it for it in all_items if it["ts"] >= month_start]
        if len(all_items) != before:
            print(f"[info] 仅保留当月({current_month_label()})消息: {before} -> {len(all_items)} 条")

    state = migrate_state(load_state())
    seen_map = state.setdefault("seen_ids", {})

    # 推送前清理过期消息
    if not dry_run and chat_ids:
        removed = cleanup_old_messages(cfg, state)
        if removed:
            print(f"[cleanup] 本次共删除 {removed} 条过期消息")

    if baseline:
        for it in all_items:
            seen_map.setdefault(it["source"], []).append(it["id"])
        for src in list(seen_map.keys()):
            seen_map[src] = list(dict.fromkeys(seen_map[src]))[-int(cfg["STATE_MAX_IDS"]):]
        state["updated"] = now_str()
        if not dry_run:
            save_state(state)
        print(f"[info] 已建立基线, 记录 {len(all_items)} 条为已见(未推送)。")
        print("[info] 今后新动态会自动推送; 测试推送: python3 monitor.py --send-now")
        return 0

    # 新数据源首次运行 -> 建立基线(只记录不推送)
    newly_baselined = []
    for src in sources:
        if src not in seen_map:
            ids = [it["id"] for it in all_items if it["source"] == src]
            seen_map[src] = ids
            newly_baselined.append(src)
    if newly_baselined:
        for src in newly_baselined:
            print(f"[info] [{SOURCE_LABELS.get(src, src)}] 首次运行, 已建立基线(记录 {len(seen_map[src])} 条, 未推送)")
        if not dry_run:
            state["updated"] = now_str()
            save_state(state)

    if send_now:
        fresh = sorted(all_items, key=lambda x: x["ts"], reverse=True)[: int(cfg["MAX_SEND"])]
    else:
        fresh = [it for it in all_items if it["id"] not in seen_map.get(it["source"], [])]
        fresh = sorted(fresh, key=lambda x: x["ts"], reverse=True)[: int(cfg["MAX_SEND"])]

    if not fresh:
        print("[info] 无新动态。")
    else:
        print(f"[info] 发现 {len(fresh)} 条新动态。")

    if dry_run:
        for it in fresh:
            print("---")
            print(format_notice(it, int(cfg["PREVIEW_LEN"])))
        return 0

    if not chat_ids:
        print("[warn] 未配置 TELEGRAM_CHAT_ID, 跳过推送(不记录为已见)。", file=sys.stderr)
        print("[info] 请先向机器人发送任意消息,再运行: python3 get_chat_id.py", file=sys.stderr)
        return 3

    sent_ok = []
    for it in fresh:
        text = format_notice(it, int(cfg["PREVIEW_LEN"]))
        if len(text) > 4000:
            text = text[:4000]
        for cid in chat_ids:
            try:
                data = send_telegram(token, cid, text, parse_mode)
                mid = (data.get("result") or {}).get("message_id")
                if mid:
                    record_sent(state, cid, mid)
                print(f"[ok] 已推送 [{it['source']}] {it['title'][:40]} -> chat {cid}")
            except Exception as exc:  # noqa: BLE001
                print(f"[error] 推送失败 chat={cid} [{it['source']}] {it['title'][:40]}: {exc}", file=sys.stderr)
                continue
            sent_ok.append((it["source"], it["id"]))

    for src, iid in sent_ok:
        seen_map.setdefault(src, []).append(iid)
    for src in list(seen_map.keys()):
        seen_map[src] = list(dict.fromkeys(seen_map[src]))[-int(cfg["STATE_MAX_IDS"]):]

    # 每两周询问一次是否迭代
    maybe_send_iteration_prompt(cfg, state, chat_ids, force=force_iteration)

    state["updated"] = now_str()
    save_state(state)
    return 0


def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def loop_mode(cfg):
    interval = max(60, int(cfg["LOOP_INTERVAL"]))
    print(f"[info] 循环模式, 每 {interval}s 检查一次 (Ctrl+C 退出)")
    while True:
        try:
            run_once(cfg)
        except Exception as exc:  # noqa: BLE001
            print(f"[error] 检查失败: {exc}", file=sys.stderr)
        time.sleep(interval)


def main():
    ap = argparse.ArgumentParser(description="多源业务动态监控 -> Telegram 推送")
    ap.add_argument("--once", action="store_true", help="单次检查(默认)")
    ap.add_argument("--baseline", action="store_true", help="仅记录当前条目为已见,不推送")
    ap.add_argument("--send-now", action="store_true", help="立即推送最近 N 条(测试/补发)")
    ap.add_argument("--loop", action="store_true", help="常驻循环检查")
    ap.add_argument("--poll", action="store_true", help="常驻轮询用户回复 1/0")
    ap.add_argument("--iteration", action="store_true", help="立即发送迭代提醒(手动询问)")
    ap.add_argument("--dry-run", action="store_true", help="只打印不推送、不写状态")
    args = ap.parse_args()

    cfg = get_config()

    if args.loop:
        loop_mode(cfg)
    elif args.poll:
        sys.exit(poll_mode(cfg))
    elif args.baseline:
        sys.exit(run_once(cfg, baseline=True, dry_run=args.dry_run))
    elif args.send_now:
        sys.exit(run_once(cfg, send_now=True, dry_run=args.dry_run))
    elif args.iteration:
        sys.exit(run_once(cfg, force_iteration=True, dry_run=args.dry_run))
    else:
        sys.exit(run_once(cfg, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
