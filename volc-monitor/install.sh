#!/usr/bin/env bash
# 安装 systemd 单元: 每日定时推送 + Telegram<->Pi 对话桥(osint_bot)
# 说明: osint_bot 取代旧的 volc-monitor-poll.service
#       (其"1/0 迭代回复"由 osint_bot 转发给 monitor.handle_decision 处理)
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_DIR="${SRC_DIR}/../osint-bot"
UNIT_DIR="/etc/systemd/system"

install -m 644 "${SRC_DIR}/volc-monitor.service" "${UNIT_DIR}/volc-monitor.service"
install -m 644 "${SRC_DIR}/volc-monitor.timer" "${UNIT_DIR}/volc-monitor.timer"
if [ -f "${BOT_DIR}/osint-bot.service" ]; then
  install -m 644 "${BOT_DIR}/osint-bot.service" "${UNIT_DIR}/osint-bot.service"
fi

# 移除旧轮询服务(与 osint_bot 的 getUpdates 互斥, 同一 bot token 只允许一个消费者)
systemctl disable --now volc-monitor-poll.service 2>/dev/null || true
rm -f "${UNIT_DIR}/volc-monitor-poll.service"

systemctl daemon-reload
systemctl enable --now volc-monitor.timer
if [ -f "${UNIT_DIR}/osint-bot.service" ]; then
  systemctl enable --now osint-bot.service
fi

echo "[ok] 已安装并启用:"
echo "     - volc-monitor.timer   每天 10:00(北京) 推送公告"
echo "     - osint-bot.service    Telegram <-> Pi 对话桥(含迭代 1/0 回复)"
echo ""
echo "     查看状态: systemctl status volc-monitor.timer osint-bot.service"
echo "     对话测试: Telegram 给 @OSINTT_Mine_bot 发任意消息"
