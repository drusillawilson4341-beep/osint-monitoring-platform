#!/usr/bin/env python3
"""mitmproxy addon：把 HTTP 流量摘要记录到 JSONL 文件。"""
import json
import time

OUTFILE = "/root/test/osint-tools/var/mitm_flows.jsonl"


class FlowLogger:
    def response(self, flow):
        req = flow.request
        resp = flow.response
        entry = {
            "ts": time.time(),
            "method": req.method,
            "url": req.pretty_url,
            "host": req.pretty_host,
            "path": req.path,
            "status": resp.status_code if resp else None,
            "content_type": resp.headers.get("content-type", "") if resp else "",
            "content_length": int(resp.headers.get("content-length", 0) or 0) if resp else 0,
        }
        with open(OUTFILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


addons = [FlowLogger()]
