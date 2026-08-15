"""tracing.py —— worker 事件流 → 用量/成本摘要（T14）。

从 pi --mode rpc/json 的 message_update/message_end 事件聚合 usage（input/output/
cacheRead/totalTokens/cost），供 UI 展示每题 token 与费用。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def summarize_usage(log_path: Path) -> dict[str, Any]:
    """读 worker 日志聚合用量。返回 {input,output,cacheRead,totalTokens,cost}。"""
    usage = {"input": 0, "output": 0, "cacheRead": 0, "totalTokens": 0, "cost": 0.0}
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return usage
    for line in lines:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        u = ev.get("usage")
        if isinstance(u, dict):
            for k in ("input", "output", "cacheRead", "totalTokens"):
                v = u.get(k)
                if isinstance(v, (int, float)):
                    usage[k] += int(v)
            c = u.get("cost")
            if isinstance(c, dict) and isinstance(c.get("total"), (int, float)):
                usage["cost"] += float(c["total"])
    return usage


def summarize_challenge(challenge_dir: Path) -> dict[str, Any]:
    """聚合一道题全部 worker 日志的用量。"""
    total = {"input": 0, "output": 0, "cacheRead": 0, "totalTokens": 0, "cost": 0.0,
             "workers": 0}
    if not challenge_dir.is_dir():
        return total
    for log in sorted(challenge_dir.glob("worker_*.log")):
        u = summarize_usage(log)
        for k in ("input", "output", "cacheRead", "totalTokens"):
            total[k] += u[k]
        total["cost"] += u["cost"]
        total["workers"] += 1
    return total
