#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mitm 抓包域名漏洞日报
- 从 var/mitm_flows.jsonl 提取访问过的域名(去重)
- 用 VirusTotal + Shodan 检测漏洞/恶意
- 每天 20:00 推送当月累计"有漏洞"域名清单
运行: python3 monitor.py [--once]
"""
import argparse
import datetime
import json
import os
import socket
import sys
import time

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FLOWS = "/root/test/osint-tools/var/mitm_flows.jsonl"
OSINT_ENV = "/root/test/osint-tools/.env"      # VIRUSTOTAL_API_KEY / SHODAN_API_KEY
VOLC_ENV = "/root/test/volc-monitor/.env"      # TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
STATE_FILE = os.path.join(BASE_DIR, "state.json")


def load_env(path):
    env = {}
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return env


def get_cfg():
    cfg = {}
    cfg.update(load_env(OSINT_ENV))
    cfg.update(load_env(VOLC_ENV))
    cfg.update(load_env(os.path.join(BASE_DIR, ".env")))
    return cfg


def extract_hosts():
    hosts = set()
    try:
        with open(FLOWS, encoding="utf-8") as f:
            for ln in f:
                try:
                    h = json.loads(ln).get("host")
                    if h:
                        hosts.add(h)
                except (ValueError, AttributeError):
                    continue
    except FileNotFoundError:
        pass
    return sorted(hosts)


def check_vt(domain, key):
    """VirusTotal 域名信誉。返回统计 dict 或 None。"""
    if not key:
        return None
    try:
        r = requests.get(f"https://www.virustotal.com/api/v3/domains/{domain}",
                         headers={"x-apikey": key}, timeout=15)
        if r.status_code == 200:
            a = r.json().get("data", {}).get("attributes", {})
            s = a.get("last_analysis_stats") or {}
            return {"malicious": s.get("malicious", 0),
                    "suspicious": s.get("suspicious", 0),
                    "harmless": s.get("harmless", 0),
                    "total": sum(s.values()),
                    "reputation": a.get("reputation")}
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] VT {domain}: {exc}", file=sys.stderr)
    return None


def resolve_ips(domain):
    try:
        return sorted({i[4][0] for i in socket.getaddrinfo(domain, None)})
    except Exception:  # noqa: BLE001
        return []


def check_shodan(domain, key):
    """Shodan 主机 CVE。返回 {ip, cves, ports} 或 None。"""
    if not key:
        return None
    for ip in resolve_ips(domain)[:2]:
        try:
            r = requests.get(f"https://api.shodan.io/shodan/host/{ip}",
                             params={"key": key}, timeout=15)
            if r.status_code == 200:
                d = r.json()
                vulns = d.get("vulns") or []
                if vulns:
                    return {"ip": ip, "cves": vulns, "ports": d.get("ports", [])}
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] Shodan {domain}/{ip}: {exc}", file=sys.stderr)
    return None


def send_telegram(token, chat_id, text):
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                          timeout=15)
        return r.status_code == 200
    except Exception as exc:  # noqa: BLE001
        print(f"[error] 推送失败 chat={chat_id}: {exc}", file=sys.stderr)
        return False


def fmt_entry(host, e):
    vt = e.get("vt") or {}
    sh = e.get("shodan") or {}
    detail = []
    if vt.get("malicious"):
        detail.append(f"VT {vt['malicious']}恶意/{vt.get('total', 0)}引擎")
    if vt.get("suspicious"):
        detail.append(f"VT {vt['suspicious']}可疑")
    if sh and sh.get("cves"):
        detail.append(f"Shodan CVE: {', '.join(sh['cves'][:3])}")
    line = f"• <b>{host}</b>"
    if detail:
        line += "\n  " + "; ".join(detail)
    return line


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = get_cfg()
    token = cfg.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids = [c.strip() for c in cfg.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]
    vt_key = cfg.get("VIRUSTOTAL_API_KEY", "").strip()
    shodan_key = cfg.get("SHODAN_API_KEY", "").strip()

    if not token or not chat_ids:
        print("[error] 未配置 Telegram (检查 volc-monitor/.env)", file=sys.stderr)
        return 2

    hosts = extract_hosts()
    print(f"[info] 提取域名 {len(hosts)} 个: {', '.join(hosts) or '(空)'}")

    month = datetime.datetime.now().strftime("%Y-%m")
    state = {}
    if os.path.exists(STATE_FILE):
        try:
            state = json.load(open(STATE_FILE, encoding="utf-8"))
        except (ValueError, OSError):
            state = {}
    if state.get("month") != month:
        state = {"month": month, "vuln_domains": {}}
    vuln = state.setdefault("vuln_domains", {})

    new_found = []
    for host in hosts:
        vt = check_vt(host, vt_key)
        time.sleep(5)  # VT 免费 4 req/min，保守间隔
        sh = check_shodan(host, shodan_key)

        is_vuln = bool((vt and (vt["malicious"] > 0 or vt["suspicious"] > 0))
                       or (sh and sh.get("cves")))
        if is_vuln:
            entry = {"vt": vt, "shodan": sh,
                     "last_seen": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
            if host not in vuln:
                new_found.append(host)
            vuln[host] = entry
            print(f"[info] 发现漏洞域名: {host}")

    if not args.dry_run:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    if not vuln:
        print("[info] 当月无漏洞域名, 不推送")
        return 0

    lines = ["🔔 <b>抓包域名漏洞日报</b>",
             f"📅 {month}",
             f"当月累计 {len(vuln)} 个域名存在漏洞/恶意：", ""]
    lines += [fmt_entry(h, e) for h, e in vuln.items()]
    text = "\n".join(lines)

    if args.dry_run:
        print("--- 推送预览 ---")
        print(text)
        return 0

    ok = 0
    for cid in chat_ids:
        ok += send_telegram(token, cid, text)
    print(f"[ok] 已推送 ({ok}/{len(chat_ids)}), 漏洞域名 {len(vuln)} 个, 新发现 {len(new_found)} 个")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
