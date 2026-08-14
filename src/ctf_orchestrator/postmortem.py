#!/usr/bin/env python3
"""
postmortem.py —— 训练闭环的复盘脚本（Phase 2 评测后运行）

输入：workspace/state.json + challenges/*/worker_*.log
输出：Markdown 短板报告（失败模式统计 + 题型矩阵 + 改进建议），
     同时打印 git 版本信息（技能包/代码的当前提交），形成版本-成绩对应记录。

用法：python postmortem.py --workspace D:/ctf-agent/workspace --out report.md
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workers import parse_worker_output  # noqa: E402


def git_head(repo: Path) -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=str(repo), capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or "?"
    except Exception:
        return "?"


def analyze_logs(workdir: Path) -> dict[str, Any]:
    """统计 worker 日志里的工具调用/错误/僵局痕迹。"""
    stats = {"tool_calls": Counter(), "tool_errors": Counter(), "stuck_reasons": Counter()}
    for log in workdir.glob("worker_*.log"):
        text = log.read_text(encoding="utf-8", errors="replace")
        # 从 json 事件里提工具名与错误（复用 stuck 的解析思路做一次全量扫描）
        for line in text.splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict) or ev.get("type") != "turn_end":
                continue
            for r in ev.get("toolResults") or []:
                if not isinstance(r, dict):
                    continue
                name = r.get("toolName", "?")
                stats["tool_calls"][name] += 1
                if r.get("isError"):
                    stats["tool_errors"][name] += 1
    return stats


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="postmortem")
    p.add_argument("--workspace", default="D:/ctf-agent/workspace")
    p.add_argument("--out", default=None)
    p.add_argument("--repo", default="D:/ctf-agent", help="git 仓库根（版本记录）")
    args = p.parse_args(argv)

    ws = Path(args.workspace)
    state_file = ws / "state.json"
    if not state_file.exists():
        print("no state.json")
        return 1
    data = json.loads(state_file.read_text(encoding="utf-8"))
    challenges = data.get("challenges", [])

    lines: list[str] = []
    lines.append("# CTF Agent 复盘报告")
    lines.append("")
    lines.append(f"- 代码版本（src+skills）: `{git_head(Path(args.repo))}`")
    lines.append(f"- 题目数: {len(challenges)}")
    solved = [c for c in challenges if c.get("status") == "solved"]
    lines.append(f"- 已解: {len(solved)}/{len(challenges)}")
    lines.append("")

    lines.append("## 题目明细")
    lines.append("")
    lines.append("| cid | 题型 | 状态 | 尝试 | 错误提交 | 耗时(s) | flag |")
    lines.append("|---|---|---|---|---|---|---|")
    stuck_reasons: Counter = Counter()
    cat_matrix: dict[str, dict[str, int]] = {}
    for c in challenges:
        cat = (c.get("raw") or {}).get("category", "?")
        status = c.get("status", "?")
        cat_matrix.setdefault(cat, {"solved": 0, "total": 0})
        cat_matrix[cat]["total"] += 1
        if status == "solved":
            cat_matrix[cat]["solved"] += 1
        attempts = c.get("attempts", [])
        elapsed = sum(a.get("elapsed", 0) for a in attempts)
        flags = []
        for a in attempts:
            if a.get("flags"):
                flags = a["flags"][:1]
            if a.get("stuck_reason"):
                stuck_reasons[a["stuck_reason"]] += 1
        flag_txt = flags[0][:36] if flags else "-"
        lines.append(
            f"| {c['cid']} | {cat} | {status} | {len(attempts)} | "
            f"{c.get('wrong_submits', 0)} | {elapsed:.0f} | {flag_txt} |")

    lines.append("")
    lines.append("## 题型矩阵")
    lines.append("")
    lines.append("| 题型 | 已解/总数 | 解题率 |")
    lines.append("|---|---|---|")
    for cat, m in sorted(cat_matrix.items()):
        lines.append(f"| {cat} | {m['solved']}/{m['total']} | "
                     f"{m['solved']/max(1,m['total'])*100:.0f}% |")

    lines.append("")
    lines.append("## 僵局原因统计")
    lines.append("")
    for reason, count in stuck_reasons.most_common():
        lines.append(f"- {reason}: {count} 次")

    lines.append("")
    lines.append("## 工具使用统计（全部 worker 日志）")
    lines.append("")
    lines.append("| 工具 | 调用 | 错误 | 错误率 |")
    lines.append("|---|---|---|---|")
    total_stats = {"tool_calls": Counter(), "tool_errors": Counter()}
    for c in challenges:
        wd = ws / "challenges" / str(c["cid"])
        if not wd.exists():
            continue
        s = analyze_logs(wd)
        total_stats["tool_calls"] += s["tool_calls"]
        total_stats["tool_errors"] += s["tool_errors"]
    for name, count in total_stats["tool_calls"].most_common(12):
        errs = total_stats["tool_errors"].get(name, 0)
        lines.append(f"| {name} | {count} | {errs} | {errs/max(1,count)*100:.0f}% |")

    lines.append("")
    lines.append("## 自动建议（规则生成，供人工判断）")
    lines.append("")
    worst = sorted(total_stats["tool_calls"].items(),
                   key=lambda kv: total_stats["tool_errors"].get(kv[0], 0) / max(1, kv[1]),
                   reverse=True)
    if worst:
        name, count = worst[0]
        errs = total_stats["tool_errors"].get(name, 0)
        if errs > 0:
            lines.append(f"- 错误率最高的工具是 `{name}`（{errs}/{count}）——检查该工具的用法提示或技能包说明")
    failed = [c for c in challenges if c.get("status") in ("dead", "needs_hint", "open", "queued")]
    if failed:
        cats = Counter((c.get("raw") or {}).get("category", "?") for c in failed)
        lines.append(f"- 未解题集中在: {dict(cats)} —— 优先补充对应题型技能包/模型路由")
    if stuck_reasons:
        top = stuck_reasons.most_common(1)[0]
        lines.append(f"- 最常见僵局: {top[0]}（{top[1]} 次）——调僵局阈值或对应提示词")

    report = "\n".join(lines) + "\n"
    out_path = Path(args.out) if args.out else (ws / "postmortem.md")
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nreport saved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
