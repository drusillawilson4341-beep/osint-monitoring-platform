#!/usr/bin/env bash
# 安装 systemd 单元并立即执行一次工具安装
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="/etc/systemd/system"

install -m 644 "${SRC_DIR}/osint-tools.service" "${UNIT_DIR}/osint-tools.service"
install -m 644 "${SRC_DIR}/osint-tools.timer" "${UNIT_DIR}/osint-tools.timer"

systemctl daemon-reload
systemctl enable --now osint-tools.timer

echo "[ok] 已安装 systemd 单元，并立即执行一次安装:"
systemctl start osint-tools.service
systemctl --no-pager --lines=5 status osint-tools.service --no-legend || true
echo ""
echo "定时: 每天 04:30 (UTC) 幂等更新工具"
echo "查看: systemctl list-timers osint-tools.timer"
echo "日志: tail -f ${SRC_DIR}/install.log"
