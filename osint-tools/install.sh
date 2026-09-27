#!/usr/bin/env bash
# OSINT 工具安装/更新脚本（幂等，可重复执行）
# 1) 安装系统级 OSINT 工具  2) 创建 Python venv 并装 MCP SDK  3) 校验
# 用法: sudo bash install.sh
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$BASE_DIR/.venv"
LOG="$BASE_DIR/install.log"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

# ---------- 1. 系统包 ----------
APT_PKGS=(python3-venv python3-pip whois dnsutils libimage-exiftool-perl jq curl)
missing=()
for p in "${APT_PKGS[@]}"; do
  dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p")
done
if [ ${#missing[@]} -gt 0 ]; then
  log "安装系统包: ${missing[*]}"
  apt-get update -qq
  apt-get install -y "${missing[@]}"
else
  log "系统包已就绪"
fi

# ---------- 2. Python venv ----------
if [ ! -x "$VENV/bin/python" ]; then
  log "创建 venv: $VENV"
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip

# ---------- 3. pip 依赖 (MCP SDK + HTTP) ----------
log "同步 pip 依赖: mcp requests python-dotenv sherlock-project"
"$VENV/bin/pip" install --quiet mcp requests python-dotenv sherlock-project

# ---------- 4. 安全测试工具 (apt) ----------
SEC_APT_PKGS=(nmap whatweb wafw00f dirb john hashcat proxychains4 netcat-openbsd socat hydra medusa gobuster sqlmap ffuf)
missing=()
for p in "${SEC_APT_PKGS[@]}"; do
  dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p")
done
if [ ${#missing[@]} -gt 0 ]; then
  log "安装安全工具: ${missing[*]}"
  apt-get update -qq
  apt-get install -y "${missing[@]}"
else
  log "安全工具已就绪"
fi

# ---------- 5. seclists (git) ----------
if [ -d /usr/share/seclists/.git ]; then
  log "更新 seclists"
  git -C /usr/share/seclists pull --ff-only -q || log "seclists 更新失败(忽略)"
elif [ -d /usr/share/seclists ]; then
  log "seclists 已存在(非 git), 跳过"
else
  log "克隆 seclists -> /usr/share/seclists"
  git clone --depth 1 https://github.com/danielmiessler/SecLists.git /usr/share/seclists
fi

# ---------- 6. feroxbuster (GitHub release) ----------
FB_VER=$(curl -s --max-time 20 https://api.github.com/repos/epi052/feroxbuster/releases/latest | jq -r .tag_name)
FB_CUR=$(feroxbuster --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)
if [ -n "$FB_VER" ] && [ "$FB_VER" != "null" ] && [ "$FB_CUR" != "${FB_VER#v}" ]; then
  log "安装 feroxbuster ${FB_VER}"
  curl -sL --max-time 90 -o /tmp/feroxbuster.tar.gz \
    "https://github.com/epi052/feroxbuster/releases/download/${FB_VER}/x86_64-linux-feroxbuster.tar.gz"
  tar -xzf /tmp/feroxbuster.tar.gz -C /tmp feroxbuster
  install -m 755 /tmp/feroxbuster /usr/local/bin/feroxbuster
  rm -f /tmp/feroxbuster.tar.gz /tmp/feroxbuster
else
  log "feroxbuster 已就绪 (${FB_CUR:-${FB_VER}})"
fi

# ---------- 7. mitmproxy (独立 venv, 抓包) ----------
MITM_VENV="$BASE_DIR/.venv-mitm"
if [ ! -x "$MITM_VENV/bin/mitmdump" ]; then
  log "创建 mitm venv 并安装 mitmproxy"
  python3 -m venv "$MITM_VENV"
  "$MITM_VENV/bin/pip" install --quiet --upgrade pip
  "$MITM_VENV/bin/pip" install --quiet mitmproxy mcp
else
  log "mitmproxy 已就绪"
fi

# ---------- 8. burpsuite (手动) ----------
# BurpSuite Community 为 GUI 应用且需同意许可, 不自动安装。
# 手动: https://portswigger.net/burp/communitydownload -> Linux (64-bit) .sh 安装脚本

# ---------- 8. 校验 ----------
log "校验工具..."
for c in whois dig exiftool jq curl nmap whatweb wafw00f dirb john hashcat proxychains4 nc socat hydra medusa gobuster sqlmap ffuf feroxbuster; do
  command -v "$c" >/dev/null || { log "缺失: $c"; exit 1; }
done
[ -d /usr/share/seclists ] || { log "seclists 目录缺失"; exit 1; }
[ -x "$MITM_VENV/bin/mitmdump" ] || { log "mitmdump 缺失"; exit 1; }
"$VENV/bin/python" -c "import mcp, requests" || { log "MCP SDK 校验失败"; exit 1; }

log "完成 ✅  (venv=$VENV)"
