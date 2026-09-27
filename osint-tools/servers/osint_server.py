#!/usr/bin/env python3
"""OSINT MCP Server — 将常用 OSINT 工具封装为标准 MCP 接口（stdio）。

第一批工具（无需 API key）:
  - whois_lookup   域名 WHOIS 注册信息
  - dns_lookup     DNS 解析 (A/AAAA/MX/TXT/NS/CNAME/SOA)
  - reverse_dns    IP -> 域名
  - http_probe     HTTP 探测（状态码/响应头/服务器指纹/重定向）
  - exif_read      文件元数据（图片 EXIF/GPS）
  - url_expand     短链接展开（跟随重定向取最终 URL）

后续阶段可扩展（需 API key）: shodan / virustotal / urlscan 等。
"""
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

# 加载 /root/test/osint-tools/.env（不依赖客户端 cwd）
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

mcp = MCPServer("osint-tools")


def _get_key(name: str) -> str:
    """读取 API key；为空时抛错，提示用户填写位置。"""
    val = (os.getenv(name) or "").strip()
    if not val:
        raise RuntimeError(
            f"缺少 {name}，请填写 /root/test/osint-tools/.env 后重试"
        )
    return val


def _run(cmd: list[str], timeout: int = 30) -> str:
    """执行命令，返回 stdout；失败时返回 stderr 与退出码。自动注入 venv/bin 到 PATH。"""
    env = os.environ.copy()
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        out = (r.stdout or "").strip()
        if r.returncode != 0:
            err = (r.stderr or "").strip()
            return f"[exit {r.returncode}] {err or out or '命令失败'}"
        return out or "(空结果)"
    except subprocess.TimeoutExpired:
        return "[超时]"
    except FileNotFoundError as e:
        return f"[未找到命令] {e}"


@mcp.tool()
def whois_lookup(domain: str) -> str:
    """查询域名 WHOIS 注册信息。domain: 目标域名，如 example.com"""
    return _run(["whois", domain], timeout=40)


@mcp.tool()
def dns_lookup(domain: str, record_type: str = "A") -> str:
    """DNS 解析。record_type 可选: A, AAAA, MX, TXT, NS, CNAME, SOA, ANY"""
    rt = record_type.upper()
    if rt not in {"A", "AAAA", "MX", "TXT", "NS", "CNAME", "SOA", "ANY"}:
        return f"不支持的记录类型: {record_type}"
    return _run(["dig", "+short", domain, rt], timeout=20)


@mcp.tool()
def reverse_dns(ip: str) -> str:
    """反向 DNS 解析：由 IP 查询对应域名（PTR 记录）。ip: IPv4/IPv6 地址"""
    return _run(["dig", "+short", "-x", ip], timeout=20)


@mcp.tool()
def http_probe(url: str) -> str:
    """HTTP 探测目标 URL：返回状态码、响应头、Server 指纹与重定向链。"""
    return _run(
        ["curl", "-sSIL", "--max-time", "20", "-A", "Mozilla/5.0", url],
        timeout=35,
    )


@mcp.tool()
def exif_read(path: str) -> str:
    """读取本地文件元数据（图片 EXIF/GPS、文档属性等）。path: 本地文件绝对路径"""
    return _run(["exiftool", path], timeout=20)


@mcp.tool()
def url_expand(url: str) -> str:
    """展开短链接，跟随重定向并返回最终目标 URL。"""
    return _run(
        ["curl", "-sS", "-o", "/dev/null", "-w", "%{url_effective}", "--max-time", "20", "-L", url],
        timeout=30,
    )

def _get_json(url: str, headers: dict | None = None, params: dict | None = None, timeout: int = 20) -> dict:
    """GET 请求并解析 JSON；失败时抛出不泄露 URL/密钥的错误。"""
    try:
        r = requests.get(url, headers=headers, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        raise RuntimeError(f"HTTP {e.response.status_code} {e.response.reason}") from None
    except requests.RequestException as e:
        raise RuntimeError(f"请求失败: {type(e).__name__}") from None


def _vt_stats(attrs: dict) -> str:
    s = attrs.get("last_analysis_stats") or {}
    return (
        f"引擎判定: {s.get('malicious', 0)} 恶意 / {s.get('suspicious', 0)} 可疑 / "
        f"{s.get('harmless', 0)} 无害 / {s.get('undetected', 0)} 未检出 / 共 {sum(s.values())}"
    )


# ---------- Shodan (资产测绘) ----------
@mcp.tool()
def shodan_host(ip: str) -> str:
    """Shodan 资产测绘：查询 IP 的开放端口、服务指纹、OS、归属与 CVE。ip: 目标 IP"""
    key = _get_key("SHODAN_API_KEY")
    d = _get_json(f"https://api.shodan.io/shodan/host/{ip}", params={"key": key}, timeout=20)
    lines = [
        f"IP: {d.get('ip_str')}",
        f"Org: {d.get('org') or '未知'}",
        f"ASN: {d.get('asn') or '未知'}",
        f"ISP: {d.get('isp') or '未知'}",
        f"OS: {d.get('os') or '未知'}",
        f"Hostnames: {', '.join(d.get('hostnames', [])) or '无'}",
        f"开放端口: {', '.join(map(str, d.get('ports', []))) or '无'}",
    ]
    if d.get("vulns"):
        lines.append(f"CVE: {', '.join(d['vulns'])}")
    lines.append("服务:")
    for s in d.get("data", []):
        lines.append(
            f"  - {s.get('port')}/{s.get('transport')} "
            f"{s.get('product') or ''} {s.get('version') or ''}".strip()
        )
    return "\n".join(lines)


# ---------- VirusTotal (威胁情报) ----------
@mcp.tool()
def virustotal_ip(ip: str) -> str:
    """VirusTotal IP 信誉：多引擎检测结果与归属。ip: 目标 IP"""
    key = _get_key("VIRUSTOTAL_API_KEY")
    d = _get_json(f"https://www.virustotal.com/api/v3/ip_addresses/{ip}",
                  headers={"x-apikey": key}, timeout=20)
    a = d.get("data", {}).get("attributes", {})
    lines = [f"IP: {ip}", _vt_stats(a)]
    if a.get("reputation") is not None:
        lines.append(f"Reputation: {a['reputation']}")
    lines += [
        f"AS Owner: {a.get('as_owner') or '未知'}",
        f"Country: {a.get('country') or '未知'}",
        f"Network: {a.get('network') or '未知'}",
    ]
    return "\n".join(lines)


@mcp.tool()
def virustotal_domain(domain: str) -> str:
    """VirusTotal 域名信誉：多引擎检测、注册商与 DNS 信息。domain: 目标域名"""
    key = _get_key("VIRUSTOTAL_API_KEY")
    d = _get_json(f"https://www.virustotal.com/api/v3/domains/{domain}",
                  headers={"x-apikey": key}, timeout=20)
    a = d.get("data", {}).get("attributes", {})
    lines = [f"域名: {domain}", _vt_stats(a)]
    if a.get("registrar"):
        lines.append(f"注册商: {a['registrar']}")
    if a.get("creation_date"):
        lines.append(f"创建时间: {a['creation_date']}")
    if a.get("last_dns_records"):
        lines.append(f"DNS 记录数: {len(a['last_dns_records'])}")
    return "\n".join(lines)


@mcp.tool()
def virustotal_hash(file_hash: str) -> str:
    """VirusTotal 文件哈希信誉：传入 MD5/SHA1/SHA256。"""
    key = _get_key("VIRUSTOTAL_API_KEY")
    d = _get_json(f"https://www.virustotal.com/api/v3/files/{file_hash}",
                  headers={"x-apikey": key}, timeout=20)
    a = d.get("data", {}).get("attributes", {})
    lines = [f"哈希: {file_hash}", _vt_stats(a)]
    if a.get("meaningful_name"):
        lines.append(f"文件名: {a['meaningful_name']}")
    if a.get("size"):
        lines.append(f"大小: {a['size']} bytes")
    if a.get("type_description"):
        lines.append(f"类型: {a['type_description']}")
    return "\n".join(lines)


@mcp.tool()
def virustotal_url(url: str) -> str:
    """VirusTotal URL 信誉：查询已扫描 URL 的检测结果。"""
    key = _get_key("VIRUSTOTAL_API_KEY")
    url_id = base64.urlsafe_b64encode(url.encode()).rstrip(b"=").decode()
    try:
        r = requests.get(f"https://www.virustotal.com/api/v3/urls/{url_id}",
                         headers={"x-apikey": key}, timeout=20)
    except requests.RequestException as e:
        return f"请求失败: {type(e).__name__}"
    if r.status_code == 404:
        return "该 URL 尚未被 VirusTotal 扫描过。"
    if not r.ok:
        return f"HTTP {r.status_code} {r.reason}"
    a = r.json().get("data", {}).get("attributes", {})
    lines = [f"URL: {url}", _vt_stats(a)]
    if a.get("last_final_url"):
        lines.append(f"最终URL: {a['last_final_url']}")
    return "\n".join(lines)


# ---------- URLScan (页面分析) ----------
@mcp.tool()
def urlscan_scan(url: str, visibility: str = "public") -> str:
    """提交 URL 到 urlscan.io 扫描。visibility: public/unlisted/private"""
    key = _get_key("URLSCAN_API_KEY")
    try:
        r = requests.post("https://urlscan.io/api/v1/scan/",
                          headers={"API-Key": key, "Content-Type": "application/json"},
                          json={"url": url, "visibility": visibility}, timeout=30)
    except requests.RequestException as e:
        return f"请求失败: {type(e).__name__}"
    if not r.ok:
        return f"HTTP {r.status_code} {r.reason}"
    d = r.json()
    return (f"已提交扫描\nUUID: {d.get('uuid')}\n"
            f"结果页: {d.get('result')}\n"
            f"提示: 用 urlscan_result 工具 + UUID 查询结果")


@mcp.tool()
def urlscan_result(uuid: str) -> str:
    """查询 urlscan.io 扫描结果。uuid: urlscan_scan 返回的 UUID"""
    key = _get_key("URLSCAN_API_KEY")
    d = _get_json(f"https://urlscan.io/api/v1/result/{uuid}/",
                  headers={"API-Key": key}, timeout=20)
    task = d.get("task", {})
    page = d.get("page", {})
    stats = d.get("stats", {})
    lines = [
        f"URL: {task.get('url')}",
        f"扫描时间: {task.get('time')}",
        f"最终页面: {page.get('url')}",
        f"标题: {page.get('title') or '无'}",
        f"Server: {page.get('server') or '未知'}",
        f"IP: {page.get('ip')}",
        f"ASN: {page.get('asn')}",
        f"状态码: {page.get('status')}",
    ]
    if stats:
        lines.append(f"统计: requests={stats.get('requests', 0)} links={stats.get('links', 0)}")
    return "\n".join(lines)


# ---------- Sherlock (用户名跨平台搜索) ----------
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*(?:\x07|\x1b\\)")


@mcp.tool()
def sherlock_search(username: str, sites: str = "", timeout: int = 8) -> str:
    """跨平台搜索用户名在哪些社交/网站注册。
    username: 目标用户名；sites: 可选逗号分隔的站点名(如 GitHub,Twitter)缩小范围，空则查全部(较慢)；
    timeout: 每站请求超时秒数(默认 8)。"""
    cmd = ["sherlock", username, "--print-found", "--no-color", "--timeout", str(timeout)]
    if sites:
        for s in [x.strip() for x in sites.split(",") if x.strip()]:
            cmd += ["--site", s]
    # 全量查询耗时约 1-3 分钟，预留 240s
    out = _run(cmd, timeout=max(240, timeout * 30))
    return _ANSI_RE.sub("", out)


# ---------- 证书透明日志子域名枚举 (crt.sh, 无需 key) ----------
@mcp.tool()
def crt_subdomains(domain: str) -> str:
    """通过 crt.sh 证书透明日志枚举子域名。domain: 目标域名，如 example.com"""
    try:
        r = requests.get("https://crt.sh/",
                         params={"q": f"%.{domain}", "output": "json"}, timeout=30)
    except requests.RequestException as e:
        return f"请求失败: {type(e).__name__}"
    if not r.ok:
        return f"HTTP {r.status_code} {r.reason}"
    try:
        data = r.json()
    except ValueError:
        return "crt.sh 返回非 JSON（可能无结果或需重试）"
    subs = set()
    for item in data:
        for name in (item.get("name_value") or "").split("\n"):
            name = name.strip().lstrip("*.")
            if not name or not name.endswith(domain):
                continue
            # 只保留纯域名，过滤证书名/邮箱/带空格条目
            if re.fullmatch(r"[A-Za-z0-9._-]+", name) and "@" not in name:
                subs.add(name)
    if not subs:
        return "未找到子域名"
    return f"共 {len(subs)} 个子域名:\n" + "\n".join(sorted(subs))


# ---------- HackerTarget (DNS/子域名测绘, 无需 key) ----------
def _hackertarget(ep: str, domain: str) -> str:
    try:
        r = requests.get(f"https://api.hackertarget.com/{ep}/",
                         params={"q": domain}, timeout=20)
    except requests.RequestException as e:
        return f"请求失败: {type(e).__name__}"
    if not r.ok:
        return f"HTTP {r.status_code} {r.reason}"
    txt = r.text.strip()
    if not txt:
        return "无结果"
    lines = txt.splitlines()
    return f"共 {len(lines)} 条:\n" + "\n".join(lines)


@mcp.tool()
def hackertarget_hostsearch(domain: str) -> str:
    """HackerTarget 子域名搜索：返回子域名与对应 IP。domain: 目标域名"""
    return _hackertarget("hostsearch", domain)


@mcp.tool()
def hackertarget_dnslookup(domain: str) -> str:
    """HackerTarget DNS 完整记录：A/AAAA/MX/NS/TXT/SOA。domain: 目标域名"""
    return _hackertarget("dnslookup", domain)


# ---------- Hunter.io (邮箱枚举) ----------
@mcp.tool()
def hunter_domain_search(domain: str, limit: int = 20) -> str:
    """Hunter.io 域名邮箱搜索：查找某域名关联的公开邮箱。domain: 目标域名"""
    key = _get_key("HUNTER_API_KEY")
    try:
        r = requests.get("https://api.hunter.io/v2/domain-search",
                         params={"domain": domain, "api_key": key, "limit": limit}, timeout=20)
    except requests.RequestException as e:
        return f"请求失败: {type(e).__name__}"
    if not r.ok:
        return f"HTTP {r.status_code} {r.reason}"
    d = r.json().get("data", {})
    emails = d.get("emails") or []
    if not emails:
        return "未找到关联邮箱"
    lines = [f"域名: {d.get('domain')}  共 {len(emails)} 个邮箱:"]
    for e in emails:
        lines.append(
            f"- {e.get('value')}  (类型:{e.get('type')} 可信度:{e.get('confidence')}% "
            f"{e.get('first_name') or ''} {e.get('last_name') or ''})".rstrip()
        )
    return "\n".join(lines)


@mcp.tool()
def hunter_email_verify(email: str) -> str:
    """Hunter.io 邮箱验证：校验邮箱是否存在/可送达。email: 目标邮箱"""
    key = _get_key("HUNTER_API_KEY")
    try:
        r = requests.get("https://api.hunter.io/v2/email-verifier",
                         params={"email": email, "api_key": key}, timeout=20)
    except requests.RequestException as e:
        return f"请求失败: {type(e).__name__}"
    if not r.ok:
        return f"HTTP {r.status_code} {r.reason}"
    d = r.json().get("data", {})
    lines = [
        f"邮箱: {d.get('email')}",
        f"状态: {d.get('status')}  评分: {d.get('score')}",
        f"格式有效: {d.get('regexp')}  一次性邮箱: {d.get('disposable')}  邮箱提供商: {d.get('webmail')}",
        f"MX 记录: {d.get('mx_records')}",
    ]
    if d.get("smtp_server"):
        lines.append(f"SMTP: {d['smtp_server']}")
    return "\n".join(lines)


# ---------- GitHub OSINT ----------
@mcp.tool()
def github_user(username: str) -> str:
    """GitHub 用户信息：账号、公司、位置、博客、仓库数等。username: 用户名"""
    key = _get_key("GITHUB_TOKEN")
    try:
        r = requests.get(f"https://api.github.com/users/{username}",
                         headers={"Authorization": f"token {key}"}, timeout=20)
    except requests.RequestException as e:
        return f"请求失败: {type(e).__name__}"
    if r.status_code == 404:
        return "用户不存在"
    if not r.ok:
        return f"HTTP {r.status_code} {r.reason}"
    u = r.json()
    return "\n".join([
        f"登录名: {u.get('login')}",
        f"姓名: {u.get('name') or '无'}",
        f"公司: {u.get('company') or '无'}",
        f"位置: {u.get('location') or '无'}",
        f"博客: {u.get('blog') or '无'}",
        f"邮箱: {u.get('email') or '无'}",
        f"简介: {u.get('bio') or '无'}",
        f"公开仓库: {u.get('public_repos')}  关注者: {u.get('followers')}  关注: {u.get('following')}",
        f"注册时间: {u.get('created_at')}",
        f"主页: {u.get('html_url')}",
    ])


@mcp.tool()
def github_repos(username: str, per_page: int = 30) -> str:
    """GitHub 用户公开仓库列表（按星标排序）。username: 用户名"""
    key = _get_key("GITHUB_TOKEN")
    try:
        r = requests.get(f"https://api.github.com/users/{username}/repos",
                         headers={"Authorization": f"token {key}"},
                         params={"sort": "updated", "per_page": per_page}, timeout=20)
    except requests.RequestException as e:
        return f"请求失败: {type(e).__name__}"
    if r.status_code == 404:
        return "用户不存在"
    if not r.ok:
        return f"HTTP {r.status_code} {r.reason}"
    repos = r.json()
    if not repos:
        return "无公开仓库"
    lines = [f"共 {len(repos)} 个仓库（最近更新优先）:"]
    for x in repos:
        lines.append(
            f"- {x.get('full_name')}  ★{x.get('stargazers_count')}  "
            f"{(x.get('description') or '')[:60]}  [{x.get('language') or ''}]".rstrip()
        )
    return "\n".join(lines)


@mcp.tool()
def github_search_repos(query: str, per_page: int = 20) -> str:
    """GitHub 仓库搜索。query: 关键词或高级语法(如 org:xxx topic:xxx)"""
    key = _get_key("GITHUB_TOKEN")
    try:
        r = requests.get("https://api.github.com/search/repositories",
                         headers={"Authorization": f"token {key}"},
                         params={"q": query, "per_page": per_page}, timeout=20)
    except requests.RequestException as e:
        return f"请求失败: {type(e).__name__}"
    if not r.ok:
        return f"HTTP {r.status_code} {r.reason}"
    items = r.json().get("items") or []
    if not items:
        return "无结果"
    lines = [f"共 {r.json().get('total_count')} 个结果，展示前 {len(items)}:"]
    for x in items:
        lines.append(
            f"- {x.get('full_name')}  ★{x.get('stargazers_count')}  "
            f"{(x.get('description') or '')[:60]}".rstrip()
        )
    return "\n".join(lines)


@mcp.tool()
def github_search_code(query: str, per_page: int = 20) -> str:
    """GitHub 代码搜索。query: 关键词/文件名(如 filename:config.py password)"""
    key = _get_key("GITHUB_TOKEN")
    try:
        r = requests.get("https://api.github.com/search/code",
                         headers={"Authorization": f"token {key}"},
                         params={"q": query, "per_page": per_page}, timeout=20)
    except requests.RequestException as e:
        return f"请求失败: {type(e).__name__}"
    if not r.ok:
        return f"HTTP {r.status_code} {r.reason}"
    items = r.json().get("items") or []
    if not items:
        return "无结果"
    lines = [f"共 {r.json().get('total_count')} 个结果，展示前 {len(items)}:"]
    for x in items:
        lines.append(
            f"- {x.get('repository', {}).get('full_name')} → {x.get('path')}  "
            f"{x.get('html_url')}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
