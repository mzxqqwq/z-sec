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


# ---------- 全程记录转录（T-T1）：worker 事件流 → 指令/思考/回复/工具 四类条目 ----------
def _clip_text(s: str, n: int) -> str:
    return s[:n] if len(s) > n else s


def parse_transcript(log_path: Path, limit: int = 600) -> list[dict[str, Any]]:
    """把 worker 日志解析为可展示的解题全程记录。

    条目 kind：
      prompt   —— 系统/人工下发的指令（含 plan/续跑/hint/看板，角色 user 的消息）
      think    —— worker 的思考块（assistant thinking 内容）
      reply    —— worker 的文字回复（assistant text 内容）
      call     —— 工具调用
      result   —— 工具结果（isError 标记）
    message_update 的 delta 流不解析（体积巨大），只取 message_start/end 的完整内容。
    """
    out: list[dict[str, Any]] = []
    tool_started: dict[str, dict[str, Any]] = {}
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        ts = str(ev.get("timestamp", ""))[11:19]
        etype = ev.get("type")
        if etype == "tool_execution_start":
            args = ev.get("args") or {}
            text = _clip_text(str(args.get("command") or args.get("path") or ""), 300)
            tool_started[ev.get("toolCallId", "")] = {"ts": ts}
            out.append({"kind": "call", "ts": ts, "tool": ev.get("toolName", ""), "text": text})
        elif etype == "tool_execution_end":
            result = ev.get("result")
            if isinstance(result, dict):
                text = _clip_text(str(result.get("stdout") or result.get("stderr") or ""), 800)
            else:
                text = _clip_text(str(result), 800)
            out.append({"kind": "result", "ts": ts, "tool": ev.get("toolName", ""),
                        "text": text, "isError": bool(ev.get("isError"))})
        elif etype in ("message_start", "message_end"):
            msg = ev.get("message") or {}
            role = msg.get("role", "")
            content = msg.get("content") or []
            if not isinstance(content, list):
                continue
            if role == "user" and etype == "message_start":
                # user 消息在 start/end 内容相同，只取一次
                texts = [c.get("text", "") for c in content
                         if isinstance(c, dict) and c.get("type") == "text"]
                text = _clip_text("\n".join(texts), 3000)
                if text:
                    out.append({"kind": "prompt", "ts": ts, "text": text})
            elif role == "assistant" and etype == "message_end":
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") == "thinking":
                        t = _clip_text(str(c.get("thinking", "")), 1200)
                        if t:
                            out.append({"kind": "think", "ts": ts, "text": t})
                    elif c.get("type") == "text":
                        t = _clip_text(str(c.get("text", "")), 2000)
                        if t:
                            out.append({"kind": "reply", "ts": ts, "text": t})
    return out[-limit:]


def worker_logs(challenge_dir: Path) -> list[Path]:
    """按 worker_N_ 前缀排序的日志列表（N 小的在前 = 强 worker 在前）。"""
    if not challenge_dir.is_dir():
        return []
    logs = sorted(challenge_dir.glob("worker_*.log"),
                  key=lambda p: (p.name.split("_", 2)[1:2] or ["z"])[0])
    return logs
