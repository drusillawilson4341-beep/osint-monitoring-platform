#!/usr/bin/env python3
"""mitmproxy MCP Server：启动/停止代理、查看抓包记录（stdio）。

供支持 MCP 的客户端调用；也便于 AI 助手自动化抓包分析。
CA 证书生成于首次启动: ~/.mitmproxy/mitmproxy-ca-cert.pem
"""
import json
import os
import signal
import subprocess
import time
from pathlib import Path

from mcp.server.mcpserver import MCPServer

BASE = Path("/root/test/osint-tools")
VAR = BASE / "var"
FLOWS = VAR / "mitm_flows.jsonl"
PIDFILE = VAR / "mitmdump.pid"
ADDON = BASE / "servers" / "mitm_addon.py"
MITMDUMP = BASE / ".venv-mitm" / "bin" / "mitmdump"

mcp = MCPServer("mitmproxy")


def _read_pid():
    if PIDFILE.exists():
        try:
            return int(PIDFILE.read_text().strip())
        except ValueError:
            return None
    return None


def _is_running(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@mcp.tool()
def mitm_start(port: int = 8080, username: str = "", password: str = "", global_mode: bool = True) -> str:
    """启动 mitmproxy（由 systemd 常驻服务管理）。端口/凭据由 mitm.env 与 mitmproxy.service 配置，此处参数保留兼容。"""
    subprocess.run(["systemctl", "start", "mitmproxy"], check=False)
    time.sleep(1)
    active = subprocess.run(["systemctl", "is-active", "mitmproxy"],
                            capture_output=True, text=True).stdout.strip()
    if active == "active":
        return ("✅ mitmproxy 已启动（systemd 常驻, 端口 8080）\n"
                "CA 证书: ~/.mitmproxy/mitmproxy-ca-cert.pem")
    return "启动失败，查看: journalctl -u mitmproxy"


@mcp.tool()
def mitm_status() -> str:
    """查看代理运行状态与已抓流量条数。"""
    active = subprocess.run(["systemctl", "is-active", "mitmproxy"],
                            capture_output=True, text=True).stdout.strip()
    n = 0
    if FLOWS.exists():
        n = sum(1 for _ in open(FLOWS, encoding="utf-8"))
    return "\n".join([
        f"systemd 服务: {active}",
        f"已抓流量: {n} 条",
        f"流量文件: {FLOWS}",
    ])


@mcp.tool()
def mitm_flows(limit: int = 20) -> str:
    """查看最近抓到的 HTTP 请求/响应摘要。limit: 返回条数(默认20)"""
    if not FLOWS.exists():
        return "暂无抓包记录。先 mitm_start 并在目标设备/客户端配置代理。"
    lines = FLOWS.read_text(encoding="utf-8").strip().splitlines()
    recent = lines[-limit:]
    out = []
    for ln in recent:
        e = json.loads(ln)
        ct = (e.get("content_type") or "").split(";")[0] or "-"
        out.append(f"{e.get('method')} {e.get('url')} -> {e.get('status')} [{ct}]")
    return f"最近 {len(recent)} 条:\n" + "\n".join(out)


@mcp.tool()
def mitm_clear() -> str:
    """清空抓包记录文件。"""
    if FLOWS.exists():
        FLOWS.unlink()
    return "已清空抓包记录。"


@mcp.tool()
def mitm_stop() -> str:
    """停止 mitmproxy（systemd 服务）。"""
    subprocess.run(["systemctl", "stop", "mitmproxy"], check=False)
    return "已停止 mitmproxy 服务。"


if __name__ == "__main__":
    mcp.run()
