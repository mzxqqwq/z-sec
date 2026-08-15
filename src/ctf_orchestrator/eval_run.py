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
from dasctf_eval_platform import DasctfEvalPlatform  # noqa: E402
from cybench_platform import CybenchPlatform  # noqa: E402
from platform import MockHttpPlatform  # noqa: E402

L1_CONFIG = {
    "strong": {"model": "deepseek-v4-pro", "thinking": "medium"},
    "weak": {"model": "deepseek-v4-flash", "thinking": "low"},
    "max_parallel_challenges": 2,
    "planning_enabled": True,
}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="eval_run")
    p.add_argument("--platform", choices=["ctftiny", "cybench", "dasctf2025", "mock"],
                   default="ctftiny")
    p.add_argument("--workspace", default="D:/ctf-agent/eval-workspace")
    p.add_argument("--difficulty", default="", help="逗号分隔: very_easy,easy,moderate,hard")
    p.add_argument("--categories", default="", help="逗号分隔: crypto,misc,rev,pwn,web")
    p.add_argument("--exclude", default="", help="逗号分隔 cid，跳过指定题")
    p.add_argument("--only", default="", help="逗号分隔 cid，只跑指定题")
    p.add_argument("--max-rounds", type=int, default=3)
    p.add_argument("--max-attempts", type=int, default=2)
    p.add_argument("--config", default="", help="模型路由配置 JSON（默认 L1 配置）")
    p.add_argument("--kali-url", default="http://10.174.153.128:5000")
    p.add_argument("--mock-url", default="http://127.0.0.1:7788")
    p.add_argument("--bench-root", default="",
                   help="ctftiny 题库根目录（默认 D:/ctf-agent/benchmarks/ctftiny；"
                        "NYU 全量用 D:/ctf-agent/benchmarks/nyu-ctf-bench）")
    p.add_argument("--bench-meta", default="",
                   help="题库元数据文件名，逗号分隔（默认 ctftiny.json；NYU 用 test_dataset.json）")
    p.add_argument("--revive", action="store_true",
                   help="靶机已停的服务题用 Kali podman 本地复活（需先跑 pull-service-images.sh）")
    args = p.parse_args(argv)

    difficulties = [d.strip() for d in args.difficulty.split(",") if d.strip()] or None
    categories = [c.strip() for c in args.categories.split(",") if c.strip()] or None

    if args.platform == "ctftiny":
        kwargs = dict(kali_url=args.kali_url,
                      difficulties=difficulties,
                      categories=categories,
                      exclude=[c.strip() for c in args.exclude.split(",") if c.strip()] or None)
        if args.bench_root:
            kwargs["root"] = args.bench_root
        if args.bench_meta:
            kwargs["meta_files"] = tuple(m.strip() for m in args.bench_meta.split(",") if m.strip())
        if args.revive:
            kwargs["revive"] = True
        platform = CtftinyPlatform(**kwargs)
    elif args.platform == "cybench":
        platform = CybenchPlatform(
            categories=categories,
            exclude=[c.strip() for c in args.exclude.split(",") if c.strip()] or None)
    elif args.platform == "dasctf2025":
        platform = DasctfEvalPlatform()
    else:
        platform = MockHttpPlatform(args.mock_url)

    ws = Path(args.workspace)
    ws.mkdir(parents=True, exist_ok=True)

    # 健康闸门：Kali 不可达就拒绝开跑（防止 1 小时评测白跑）
    if args.platform in ("ctftiny",) and args.kali_url:
        import requests as _req
        try:
            h = _req.get(f"{args.kali_url}/health", timeout=8)
            if h.status_code != 200 or "healthy" not in h.text:
                print(f"Kali 健康检查失败: HTTP {h.status_code}")
                return 2
        except Exception as e:
            print(f"Kali 不可达，评测中止: {e}")
            return 2
        print("Kali 健康检查通过")

    model_config = L1_CONFIG
    if args.config and Path(args.config).exists():
        import json as _json
        model_config = _json.loads(Path(args.config).read_text(encoding="utf-8"))

    orch = Orchestrator(ws, platform, DEFAULT_PI_CMD, model_config,
                        max_attempts=args.max_attempts,
                        only={c.strip() for c in args.only.split(",") if c.strip()} or None)
    if model_config.get("kb_enabled"):
        orch.start_kb()
    orch.start_worker_api()

    t0 = time.time()
    try:
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
    finally:
        # 停掉本 run 复活的全部服务容器（Kali podman）
        try:
            platform.close()
        except Exception as e:
            print(f"[revive] cleanup error: {e}")

    # 成绩单（复活功能上线后：dead 服务题由 podman 本地复活，成绩单不再区分靶机状态）
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
