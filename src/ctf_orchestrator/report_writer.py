# -*- coding: utf-8 -*-
"""report_writer.py —— 解题报告自动生成部件（2026-08-19，赛前审查后新增）

用途：平台规则要求赛后在比赛结束前在线提交解题报告（专家审核 解题报告 + 网络流量 +
平台日志）。本部件自动给每道已解出的题生成一份 Markdown 解题报告，素材来自
worker 完整日志（思考过程/工具调用/回复）与 Supervisor 方向看板。

设计原则（不拖慢解题）：
- 挂载在 dashboard 进程的独立后台线程（daemon），与编排器完全隔离；
- 只读 workspace-match 下的 state.json / worker 日志 / observer 看板；
- 报告写到 workspace-match/reports/<cid>.md（新目录，不碰任何运行数据）；
- 每题只在 solved 后生成一次（幂等）；LLM 总结串行、失败自动降级为规则摘要版；
- LLM 调用走本地网关代理（127.0.0.1:8787 → 平台大模型网关，合规审计）。

用法：
    python report_writer.py --workspace D:/ctf-agent/workspace-match --once   # 补生成一次
    python report_writer.py --workspace D:/ctf-agent/workspace-match --loop  # 常驻扫描
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ctf_orchestrator"))
sys.path.insert(0, str(ROOT / "dasctf_client"))

# 比赛场景：LLM 总结固定走本地网关代理（→ 平台大模型网关），合规且不依赖 dashboard env
os.environ.setdefault("DASCTF_LLM_BASE_URL", "http://127.0.0.1:8787")

REPORTS_DIR = "reports"
MAX_TRANSCRIPT_CHARS = 16000   # 喂给 LLM 的全程记录上限（字符）
MAX_REPORT_SIZE = 40000        # 规则降级版报告上限

# 提示词：LLM 从过程记录还原解题思路（不编造，只依据记录）
_SUMMARY_PROMPT = """\
你是一名竞赛复盘分析师。下面是某道 CTF 题的信息，以及 AI 解题 agent 的完整过程记录
（含思考、工具调用、尝试与排除的方向、最终结果）和观察看板。请写一份**中文**解题报告
（Markdown 格式），要求：

1. 客观还原**解题思路**：起始分析 → 尝试的方向 → 失败/排除的路线（一句话带过即可）
   → 最终成功的思路与关键步骤；
2. 写明**最终利用/解法**（漏洞点、关键命令、flag 内容）；
3. 只依据给定记录撰写，**不要编造**记录里没有的步骤或结论；记录不全处如实写"记录未覆盖"。

【题目信息】
{challenge}

【解题过程记录】
{transcript}

【观察看板（方向与已知事实）】
{board}

请直接输出 Markdown 正文（不要输出代码块包裹，不要重复题目信息头）。"""


def _clip(s: str, n: int) -> str:
    return s[:n] if len(s) > n else s


def _fmt_ts(ts: str) -> str:
    return ts if len(ts) >= 5 else ""


def _challenge_text(ch: dict[str, Any]) -> str:
    raw = ch.get("raw") or {}
    category = ch.get("category") or raw.get("category") or "?"
    points = ch.get("points")
    if points is None:
        points = raw.get("points")
    difficulty = raw.get("difficulty") or ch.get("difficulty") or "?"
    lines = [
        f"- 名称：{ch.get('name') or raw.get('name') or ch.get('cid')}（ID {ch.get('cid')}）",
        f"- 分类：{category} | 分值：{points} | 难度：{difficulty}",
    ]
    desc = str(ch.get("description") or raw.get("description") or "").strip()
    if desc:
        lines.append(f"- 题目描述：{_clip(desc, 800)}")
    files = ch.get("files") or []
    if files:
        lines.append(f"- 附件：{', '.join(map(str, files[:8]))}")
    if ch.get("connection"):
        lines.append(f"- 靶机连接：{ch.get('connection')}")
    return "\n".join(lines)


def _transcript_text(entries: list[dict[str, Any]]) -> str:
    """全程记录条目 → 可读文本（供 LLM/降级报告）。"""
    parts: list[str] = []
    for e in entries:
        ts = _fmt_ts(str(e.get("ts", "")))
        kind = e.get("kind")
        text = str(e.get("text", "")).strip()
        if not text:
            continue
        if kind == "think":
            parts.append(f"[{ts} 思考] {_clip(text, 900)}")
        elif kind == "call":
            parts.append(f"[{ts} 调用 {e.get('tool')}] {_clip(text, 300)}")
        elif kind == "result":
            mark = "错误" if e.get("isError") else "输出"
            parts.append(f"[{ts} 工具{mark}] {_clip(text, 600)}")
        elif kind == "prompt":
            parts.append(f"[{ts} 指令] {_clip(text, 500)}")
        elif kind == "reply":
            parts.append(f"[{ts} 回复] {_clip(text, 800)}")
    return "\n".join(parts)


def _board_text(board: dict[str, Any] | None) -> str:
    if not board:
        return "（无）"
    lines: list[str] = []
    for i in (board.get("ideas") or []):
        st = i.get("status") or "pending"
        res = f" → {_clip(str(i.get('result')), 200)}" if i.get("result") else ""
        lines.append(f"- 方向[{st}]：{_clip(str(i.get('content')), 400)}{res}")
    for m in (board.get("memory") or [])[:20]:
        lines.append(f"- 记忆[{m.get('kind')}]：{_clip(str(m.get('content')), 400)}")
    return "\n".join(lines) if lines else "（无）"


def _submission_text(ch: dict[str, Any]) -> str:
    atts = ch.get("attempts") or []
    subs = ch.get("submitted_flags") or []
    lines: list[str] = []
    if subs:
        lines.append(f"- 提交的 flag：{', '.join(map(str, subs))}")
    for a in atts[-5:]:
        t = time.strftime("%H:%M:%S", time.localtime(float(a.get("at") or 0)))
        w = a.get("worker") or "?"
        if a.get("timeout"):
            lines.append(f"- {t} {w} 超时（{a.get('elapsed', 0):.0f}s）")
        else:
            fl = ",".join(map(str, a.get("flags") or []))
            lines.append(f"- {t} {w} 完成（{a.get('elapsed', 0):.0f}s）flags={fl[:60]}")
    return "\n".join(lines) if lines else "（无提交记录）"


def _usage_text(cid: str, ws: Path) -> str:
    try:
        import tracing
        u = tracing.summarize_challenge(ws / "challenges" / cid)
        return (f"- 耗时/成本：workers={u['workers']} token={u['totalTokens']} "
                f"cost=¥{u['cost']:.4f}")
    except Exception:
        return "- 耗时/成本：记录不可用"


def _llm_summary(challenge: str, transcript: str, board: str) -> Optional[str]:
    """LLM 总结（走网关代理；失败返回 None → 调用方规则降级）。"""
    try:
        import agent_config
        import requests
        raw = agent_config.raw_llm("deepseek-v4-pro")
        prompt = _SUMMARY_PROMPT.format(challenge=challenge, transcript=transcript, board=board)
        r = requests.post(raw["base_url"] + "/chat/completions", timeout=180,
                          headers={"Authorization": f"Bearer {raw['api_key']}"},
                          json={"model": "deepseek-v4-pro", "messages": [
                              {"role": "user", "content": prompt}],
                                "max_tokens": 4000, "stream": False})
        if r.status_code != 200:
            return None
        data = r.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        return content.strip() or None
    except Exception:
        return None


def _rule_summary(transcript: str) -> str:
    """LLM 不可用时的规则降级：直接罗列关键过程条目（截断）。"""
    body = _clip(transcript, MAX_REPORT_SIZE)
    return f"```\n{body}\n```\n\n> 注：此报告为过程记录直出（LLM 总结暂不可用）。"


def generate_one(cid: str, ws: Path, force: bool = False) -> tuple[bool, str]:
    """为单题生成报告。返回 (是否生成, 说明)。幂等：已生成且不 force 则跳过。"""
    reports = ws / REPORTS_DIR
    reports.mkdir(parents=True, exist_ok=True)
    out = reports / f"{cid}.md"
    if out.exists() and not force:
        return False, f"{cid} 报告已存在（--force 重新生成）"
    try:
        state = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "state.json 不可读"
    ch = next((c for c in state.get("challenges", []) if str(c.get("cid")) == str(cid)), None)
    if ch is None:
        return False, f"找不到题目 {cid}"
    if ch.get("status") != "solved":
        return False, f"{cid} 状态={ch.get('status')}，仅对 solved 题生成"

    # 素材：全程记录（多 worker 合并，按时间序）+ 看板
    entries: list[dict[str, Any]] = []
    import tracing
    for log in tracing.worker_logs(ws / "challenges" / cid):
        entries.extend(tracing.parse_transcript(log, limit=400))
    entries.sort(key=lambda e: str(e.get("ts", "")))
    transcript = _transcript_text(entries)
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        transcript = transcript[-MAX_TRANSCRIPT_CHARS:]

    board = _board_text(ch.get("board"))
    challenge_txt = _challenge_text(ch)
    sub_txt = _submission_text(ch)
    usage_txt = _usage_text(cid, ws)

    body = _llm_summary(challenge_txt, transcript, board)
    if not body:
        body = _rule_summary(transcript)
    flag_txt = ", ".join(map(str, ch.get("submitted_flags") or []))
    md = (
        f"# 解题报告：{ch.get('name') or cid}\n\n"
        f"## 基本信息\n{challenge_txt}\n\n"
        f"## 解题过程\n{body}\n\n"
        f"## 提交记录\n{sub_txt}\n"
        f"{'- flag：' + flag_txt if flag_txt else ''}\n"
        f"{usage_txt}\n"
        f"---\n_报告由 report_writer 自动生成（{time.strftime('%Y-%m-%d %H:%M:%S')}）_\n"
    )
    out.write_text(md, encoding="utf-8")
    return True, f"已生成 {out.relative_to(ws)}"


def scan(ws: Path, force: bool = False) -> list[tuple[str, bool, str]]:
    """扫描所有 solved 题，生成缺失报告。返回 [(cid, 是否生成, 说明)]。"""
    try:
        state = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    results: list[tuple[str, bool, str]] = []
    for ch in state.get("challenges", []):
        if ch.get("status") == "solved":
            cid = str(ch.get("cid"))
            ok, msg = generate_one(cid, ws, force=force)
            results.append((cid, ok, msg))
            print(f"[report] {msg}")
    return results


def loop(ws: Path, interval: float = 60.0) -> None:
    """常驻扫描（dashboard 后台线程用）。"""
    print(f"[report] 解题报告扫描已启动（{ws}/reports，每 {interval:.0f}s）")
    while True:
        try:
            scan(ws)
        except Exception as e:
            print(f"[report] scan error: {e}")
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="report_writer")
    ap.add_argument("--workspace", default=r"D:\ctf-agent\workspace-match")
    ap.add_argument("--once", action="store_true", help="扫描一次（默认）")
    ap.add_argument("--loop", action="store_true", help="常驻扫描")
    ap.add_argument("--force", action="store_true", help="重新生成已存在的报告")
    ap.add_argument("--cid", default="", help="只生成指定题")
    args = ap.parse_args(argv)
    ws = Path(args.workspace)
    if args.cid:
        ok, msg = generate_one(args.cid, ws, force=args.force)
        print(msg)
        return 0 if ok else 1
    if args.loop:
        loop(ws)
        return 0
    results = scan(ws, force=args.force)
    print(f"扫描完成：{sum(1 for _, ok, _ in results if ok)}/{len(results)} 份报告生成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
