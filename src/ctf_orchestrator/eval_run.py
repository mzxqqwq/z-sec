#!/usr/bin/env python3
"""
eval_run.py —— benchmark 评测入口

流程：构建 CtftinyPlatform（或 mock）→ 编排器多轮运行直至稳定 → 成绩单 + 复盘报告。

用法：
    # L1 冒烟：10 道 very_easy/easy 静态题
    python eval_run.py --difficulty very_easy,easy --categories crypto,misc,rev \
        --workspace D:/ctf-agent/eval-workspace --max-rounds 3
    # L2 全量
    python eval_run.py --workspace D:/ctf-agent/eval-workspace --max-rounds 4
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ctf_orchestrator import Orchestrator, DEFAULT_PI_CMD, DEFAULT_MODEL_CONFIG  # noqa: E402
from eval_platform import CtftinyPlatform  # noqa: E402
from platform import MockHttpPlatform  # noqa: E402

L1_CONFIG = {
    "category_routing": {
        "crypto": [{"model": "deepseek-v4-flash", "thinking": "low"}],
        "misc":   [{"model": "deepseek-v4-flash", "thinking": "low"}],
        "rev":    [{"model": "deepseek-v4-flash", "thinking": "low"}],
        "pwn":    [{"model": "deepseek-v4-flash", "thinking": "low"}],
        "web":    [{"model": "deepseek-v4-flash", "thinking": "low"}],
        "default":[{"model": "deepseek-v4-flash", "thinking": "low"}],
    },
    "max_parallel_challenges": 2,
    "race_workers_per_challenge": 1,
    "planning_enabled": True,
    "triage_order": "easy-first",
}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="eval_run")
    p.add_argument("--platform", choices=["ctftiny", "mock"], default="ctftiny")
    p.add_argument("--workspace", default="D:/ctf-agent/eval-workspace")
    p.add_argument("--difficulty", default="", help="逗号分隔: very_easy,easy,moderate,hard")
    p.add_argument("--categories", default="", help="逗号分隔: crypto,misc,rev,pwn,web")
    p.add_argument("--exclude", default="", help="逗号分隔 cid，跳过指定题")
    p.add_argument("--max-rounds", type=int, default=3)
    p.add_argument("--max-attempts", type=int, default=2)
    p.add_argument("--config", default="", help="模型路由配置 JSON（默认 L1 配置）")
    p.add_argument("--kali-url", default="http://10.174.153.128:5000")
    p.add_argument("--mock-url", default="http://127.0.0.1:7788")
    args = p.parse_args(argv)

    difficulties = [d.strip() for d in args.difficulty.split(",") if d.strip()] or None
    categories = [c.strip() for c in args.categories.split(",") if c.strip()] or None

    if args.platform == "ctftiny":
        platform = CtftinyPlatform(kali_url=args.kali_url,
                                   difficulties=difficulties,
                                   categories=categories,
                                   exclude=[c.strip() for c in args.exclude.split(",") if c.strip()] or None)
    else:
        platform = MockHttpPlatform(args.mock_url)

    ws = Path(args.workspace)
    ws.mkdir(parents=True, exist_ok=True)

    model_config = L1_CONFIG
    if args.config and Path(args.config).exists():
        import json as _json
        model_config = _json.loads(Path(args.config).read_text(encoding="utf-8"))

    orch = Orchestrator(ws, platform, DEFAULT_PI_CMD, model_config,
                        max_attempts=args.max_attempts)

    t0 = time.time()
    for round_no in range(1, args.max_rounds + 1):
        before = json.dumps({c: s.status for c, s in orch.board.challenges.items()},
                            sort_keys=True)
        orch.run_round()
        after = json.dumps({c: s.status for c, s in orch.board.challenges.items()},
                           sort_keys=True)
        open_left = len(orch.board.open_cids())
        print(f"== round {round_no} done, open left: {open_left}, elapsed {time.time()-t0:.0f}s")
        if open_left == 0 or before == after:
            break

    # 成绩单
    solved = [cs for cs in orch.board.challenges.values() if cs.status == "solved"]
    total = len(orch.board.challenges)
    print(f"\n======== 评测成绩 ========")
    print(f"solved: {len(solved)}/{total}")
    per_cat: dict[str, list[int]] = {}
    for cs in orch.board.challenges.values():
        cat = (cs.raw or {}).get("category", "?")
        per_cat.setdefault(cat, [0, 0])
        per_cat[cat][1] += 1
        if cs.status == "solved":
            per_cat[cat][0] += 1
    for cat, (s, t) in sorted(per_cat.items()):
        print(f"  {cat}: {s}/{t}")
    elapsed_total = time.time() - t0
    print(f"elapsed: {elapsed_total:.0f}s")

    report = {"total": total, "solved": len(solved),
              "by_category": {k: {"solved": v[0], "total": v[1]} for k, v in per_cat.items()},
              "elapsed": elapsed_total,
              "unsolved": [{"cid": cs.cid, "category": (cs.raw or {}).get("category", "?"),
                            "status": cs.status, "attempts": len(cs.attempts)}
                           for cs in orch.board.challenges.values()
                           if cs.status != "solved"]}
    (ws / "eval-result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nresult saved: {ws / 'eval-result.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
