"""digest.py —— worker 日志 → 3 行中文摘要（LLM 翻译）。

输入：workspace/challenges/<cid>/worker_*.log（pi json 事件流）
输出：3 行中文：①正在尝试什么 ②最近结果如何 ③卡点/风险提示（无卡点则"进展正常"）

实现：从事件流尾部抽取最近工具调用（tool_execution_start 的 toolName+command）
与结果摘要（turn_end.toolResults 的 name+content 头 160 字+isError），
调 DeepSeek（deepseek-chat）翻译；按 cid 缓存 60s；任何失败降级为"摘要生成失败"。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

API_URL = "https://api.deepseek.com/chat/completions"
KEY_FILE = Path(r"D:\ctf-agent\secrets\deepseek.key")
MODEL = "deepseek-chat"
CACHE_SECONDS = 60

_cache: dict[str, tuple[float, str]] = {}

DIGEST_PROMPT = """你是 CTF 比赛盯盘助手。下面是一名 AI 解题员最近的工具活动记录。
请用 3 行中文总结（严格 3 行，每行一句话，不要多余内容）：
第 1 行：它正在尝试什么（攻击方向/在跑什么）
第 2 行：最近的结果如何（成功/失败/进展到什么程度）
第 3 行：卡点或风险提示（重复尝试、错误率高、方向可疑等；如果没有明显卡点就写"进展正常"）

工具活动记录：
{activity}
"""


def _load_key() -> str:
    if KEY_FILE.exists():
        return KEY_FILE.read_text(encoding="ascii").strip()
    import os
    return os.environ.get("DEEPSEEK_API_KEY", "")


def extract_activity(log_text: str, max_tail: int = 40000) -> str:
    """从 json 事件流尾部抽最近活动：工具调用命令 + 结果摘要。"""
    lines = log_text.splitlines()[-2000:]  # 只看尾部，避免全量解析
    calls: list[str] = []
    results: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type")
        if etype == "tool_execution_start":
            name = ev.get("toolName", "?")
            args = ev.get("args") or {}
            if isinstance(args, dict):
                cmd = str(args.get("command") or args.get("path") or "")
            else:
                cmd = str(args)
            calls.append(f"{name}: {cmd[:160]}")
        elif etype == "turn_end":
            for r in ev.get("toolResults") or []:
                if not isinstance(r, dict):
                    continue
                name = r.get("toolName", "?")
                content = r.get("content")
                if isinstance(content, list):
                    text = " ".join(
                        str(c.get("text", "")) for c in content if isinstance(c, dict))
                else:
                    text = str(content or "")
                flag = "错误" if r.get("isError") else "正常"
                results.append(f"[{flag}] {name}: {text[:160]}")
    parts: list[str] = []
    if calls:
        parts.append("最近调用:\n" + "\n".join(calls[-8:]))
    if results:
        parts.append("最近结果:\n" + "\n".join(results[-5:]))
    if not parts:
        return "(无工具活动记录)"
    return "\n".join(parts)[:max_tail]


def digest(workspace: Path, cid: str) -> str:
    """返回 cid 的 3 行中文摘要（带 60s 缓存）。"""
    key = f"{workspace}#{cid}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]

    wd = workspace / "challenges" / cid
    text = ""
    if wd.exists():
        logs = sorted(wd.glob("worker_*.log"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
        if logs:
            text = logs[0].read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        out = "尚无日志（worker 未开始）"
        _cache[key] = (time.time(), out)
        return out

    activity = extract_activity(text)
    api_key = _load_key()
    if not api_key:
        out = "摘要生成失败（缺 API key）"
        _cache[key] = (time.time(), out)
        return out
    try:
        r = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content":
                              DIGEST_PROMPT.format(activity=activity)}],
                "max_tokens": 300,
                "temperature": 0.3,
            },
            timeout=60,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"].strip()
        # 防御：要求 3 行，多了截断
        lines = [ln.strip() for ln in content.splitlines() if ln.strip()][:3]
        out = "\n".join(lines) if lines else "摘要生成失败（空响应）"
    except Exception as e:
        out = f"摘要生成失败（{type(e).__name__}）"
    _cache[key] = (time.time(), out)
    return out


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(prog="digest")
    p.add_argument("workspace")
    p.add_argument("cid")
    args = p.parse_args()
    print(digest(Path(args.workspace), args.cid))
