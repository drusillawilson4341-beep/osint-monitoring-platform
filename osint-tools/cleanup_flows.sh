#!/usr/bin/env bash
# 每两天清空 mitmproxy 抓包记录(截断,保留文件)
FLOWS=/root/test/osint-tools/var/mitm_flows.jsonl
LOG=/root/test/osint-tools/var/cleanup.log
mkdir -p "$(dirname "$FLOWS")"
if [ -f "$FLOWS" ]; then
  : > "$FLOWS"
  echo "[$(date '+%F %T')] 已清空 mitm_flows.jsonl" >> "$LOG"
else
  echo "[$(date '+%F %T')] mitm_flows.jsonl 不存在, 跳过" >> "$LOG"
fi
