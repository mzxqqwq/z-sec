#!/usr/bin/env python3
"""
ctf_orchestrator.py —— CTF 编排器 v2.1（P0：状态机 + json 模式 + 进程组杀 + 提交纪律）

模块：state.py（黑板+状态机）/ submit.py（提交纪律）/ workers.py（进程+解析）
用法不变：--once / --loop N / --workspace / --model-config / --pi-cmd
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from state import Board, ChallengeState, STATUS_NEW, STATUS_QUEUED, STATUS_SOLVING, \
    STATUS_SOLVED, STATUS_DEAD, STATUS_NEEDS_HINT  # noqa: E402
from submit import SubmissionPolicy  # noqa: E402
from workers import (kali_exec, kill_tree, parse_worker_output,  # noqa: E402
                     start_worker, cleanup_orphans)
from platform import BasePlatform, MockHttpPlatform  # noqa: E402
from planning import Planner  # noqa: E402
from stuck import StuckMonitor  # noqa: E402

WORKER_TIMEOUT = 1500  # 单个 worker 硬上限（P1 引入逐题预算后收紧）
DEFAULT_PI_CMD = [
    "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
    r"D:\ctf-agent\src\run-pi.ps1",
]

DEFAULT_MODEL_CONFIG = {
    "category_routing": {
        "web":    [{"model": "deepseek-v4-pro",   "thinking": "high"},
                   {"model": "deepseek-v4-flash", "thinking": "low"}],
        "pwn":    [{"model": "deepseek-v4-pro",   "thinking": "high"},
                   {"model": "deepseek-v4-flash", "thinking": "low"}],
        "crypto": [{"model": "deepseek-v4-flash", "thinking": "low"}],
        "rev":    [{"model": "deepseek-v4-flash", "thinking": "low"}],
        "misc":   [{"model": "deepseek-v4-flash", "thinking": "low"}],
        "default":[{"model": "deepseek-v4-flash", "thinking": "low"}],
    },
    "max_parallel_challenges": 3,
    "race_workers_per_challenge": 2,
}

SOLVE_PROMPT_TEMPLATE = """\
你是一名 CTF 选手，正在参加 DASCTF 竞赛。请独立解出下面这道题并给出 flag。

题目信息：
{challenge_json}

附件已下载到工作目录 attachments/（如无附件则忽略）。

工作环境说明：你的 bash/文件工具实际运行在一台装好 pwntools/angr/z3/sympy/
nmap/sqlmap/binwalk/john/hashcat/radare2 的 Kali Linux 上（远程）。
你可以自由地写脚本、运行工具、连网络服务。解题成功后，用
"FLAG: <flag内容>" 一行输出最终答案。

{plan_section}
{human_hints}
"""


class Orchestrator:
    def __init__(self, workspace: Path, platform: BasePlatform, pi_cmd: list[str],
                 model_config: dict[str, Any],
                 max_attempts: int = 2, max_wrong_submits: int = 3,
                 only: set[str] | None = None) -> None:
        self.ws = workspace
        self.ws.mkdir(parents=True, exist_ok=True)
        (self.ws / "hints").mkdir(parents=True, exist_ok=True)
        (self.ws / "challenges").mkdir(parents=True, exist_ok=True)
        self.platform = platform
        self.pi_cmd = pi_cmd
        self.model_config = model_config
        self.max_attempts = max_attempts
        self.board = Board(workspace / "state.json")
        self.submit_policy = SubmissionPolicy(self._platform_submit, max_wrong_submits)
        self.only = only
        self._challenges: dict[str, Any] = {}  # cid -> NormalizedChallenge（内存注册表）
        try:
            api_key = Planner.load_key_from_secrets()
            self.planner = Planner(api_key, enabled=bool(model_config.get("planning_enabled", True)))
        except Exception as e:
            print(f"[planner] disabled: {e}")
            self.planner = Planner("", enabled=False)

    # ---------- 平台 ----------
    def _platform_submit(self, cid: str, flag: str) -> Any:
        ch = self._challenges.get(cid)
        if ch is None:
            raise ValueError(f"unknown challenge {cid}")
        return self.platform.submit_flag(ch, flag)

    def _sync(self) -> int:
        new_count = 0
        for ch in self.platform.list_challenges():
            cid = ch.challenge_id
            self._challenges[cid] = ch
            if self.board.get(cid) is None:
                self.board.put(ChallengeState(cid, ch.to_prompt_json()))
                new_count += 1
        return new_count

    # ---------- triage 轻量版（P2 换完整版） ----------
    def _triage(self, cs: ChallengeState) -> None:
        cat = (cs.raw.get("category") or "unknown").lower()
        points = cs.raw.get("points")
        try:
            pts = float(points) if points is not None else 0
        except (TypeError, ValueError):
            pts = 0
        difficulty = "easy" if pts <= 150 else ("medium" if pts <= 400 else "hard")
        cs.triage = {"category_guess": cat, "points": pts, "difficulty_guess": difficulty}

    # ---------- hints ----------
    def _hints(self, cid: str) -> str:
        hint_file = self.ws / "hints" / f"{cid}.md"
        if hint_file.exists():
            return "\n\n【人工提示（最新优先）】\n" + hint_file.read_text(encoding="utf-8")
        return ""

    # ---------- worker 配置 ----------
    def _worker_configs(self, cs: ChallengeState) -> list[dict[str, str]]:
        cat = cs.triage.get("category_guess", "default")
        routing = self.model_config.get("category_routing", {})
        configs = routing.get(cat) or routing.get("default") or \
            [{"model": "deepseek-v4-flash", "thinking": "low"}]
        race = int(self.model_config.get("race_workers_per_challenge", 1))
        return configs[:max(1, race)]

    # ---------- 附件同步（Windows → Kali） ----------
    def _sync_attachments(self, cid: str, workdir: Path, remote_root: str) -> None:
        att_dir = workdir / "attachments"
        att_dir.mkdir(exist_ok=True)
        ch = self._challenges.get(cid)
        if ch is not None:
            try:
                self.platform.download_attachments(ch, att_dir)
            except Exception as e:
                print(f"[{cid}] attachment download failed: {e}")
        try:
            kali_exec(f"mkdir -p {remote_root}/attachments")
            for f in att_dir.iterdir():
                if not f.is_file():
                    continue
                b64 = base64.b64encode(f.read_bytes()).decode()
                name = f.name.replace("'", r"'\''")
                kali_exec(f"echo '{b64}' | base64 -d > {remote_root}/attachments/{name}")
        except Exception as e:
            print(f"[{cid}] kali sync failed: {e}")

    # ---------- 单题竞速 ----------
    def _run_one(self, cid: str) -> None:
        cs = self.board.get(cid)
        if cs is None or cs.status not in (STATUS_NEW, STATUS_QUEUED, STATUS_NEEDS_HINT):
            return
        if cs.status == STATUS_NEW:
            self._triage(cs)
            cs.transition(STATUS_QUEUED)
        if len(cs.attempts) >= self.max_attempts:
            cs.transition(STATUS_DEAD)
            self.board.save()
            print(f"[{cid}] attempts exhausted -> dead")
            return

        cs.transition(STATUS_SOLVING)
        self.board.save()

        workdir = self.ws / "challenges" / cid
        workdir.mkdir(parents=True, exist_ok=True)
        remote_root = f"/root/ctf/{cid}"
        self._sync_attachments(cid, workdir, remote_root)

        # ---- planning（P1：派 worker 前先让便宜模型出解题计划）----
        if cs.plan is None:
            cs.plan = self.planner.plan(
                cid, json.dumps(cs.raw, ensure_ascii=False, indent=2), len(cs.attempts))
            if cs.plan:
                print(f"[{cid}] plan generated ({len(cs.plan)} chars)")

        plan_section = ""
        if cs.plan:
            plan_section = f"\n\n【解题计划（规划器产出）】\n{cs.plan}"
        base_prompt = SOLVE_PROMPT_TEMPLATE.format(
            challenge_json=json.dumps(cs.raw, ensure_ascii=False, indent=2),
            plan_section=plan_section,
            human_hints=self._hints(cid))

        LOOP_WARNING = ("\n\n【重要】你上一次运行陷入重复循环被强制终止。"
                        "不要重复相同的命令。换一个完全不同的思路："
                        "检查遗漏的线索（附件、端口、参数），或换一种工具/方法。")

        configs = self._worker_configs(cs)
        print(f"[{cid}] race: {len(configs)} workers "
              + ", ".join(f"{c['model']}:{c['thinking']}" for c in configs))

        procs: dict[Any, dict[str, Any]] = {}
        starts: dict[Any, float] = {}
        monitors: dict[Any, StuckMonitor] = {}
        stuck_killed: set[Any] = set()
        replacement_used = False

        def dispatch(idx: int, cfg: dict[str, str], prompt: str) -> None:
            tag = f"{cfg['model']}:{cfg['thinking']}".replace("/", "_").replace(":", "-")
            log_path = workdir / f"worker_{idx}_{tag}.log"
            cmd = (self.pi_cmd + ["--model", cfg["model"], "--thinking", cfg["thinking"],
                                  "--mode", "json", "--kali", remote_root, "-p", prompt])
            proc = start_worker(cmd, workdir, log_path)
            procs[proc] = {**cfg, "log": log_path, "idx": idx}
            starts[proc] = time.time()
            monitors[proc] = StuckMonitor(log_path)
            print(f"[{cid}] worker {idx} started: {tag} (pid {proc.pid})")

        for i, cfg in enumerate(configs):
            dispatch(i, cfg, base_prompt)

        solved = False
        deadline = time.time() + WORKER_TIMEOUT
        while procs and not solved and time.time() < deadline:
            # ---- 僵局监测：实时读 worker 事件流，卡住即杀 ----
            for p in list(procs):
                if p.poll() is not None or p in stuck_killed:
                    continue
                monitors[p].poll()
                stuck, reason = monitors[p].is_stuck(alive=True)
                elapsed = time.time() - starts[p]
                if stuck and elapsed > 120 and p not in stuck_killed:
                    stuck_killed.add(p)
                    meta = procs.pop(p)
                    summary = monitors[p].summarize()
                    print(f"[{cid}] STUCK[{reason}] killing worker "
                          f"{meta['model']} after {elapsed:.0f}s "
                          f"(calls={summary['total_tool_calls']}, err={summary['error_rate']:.0%})")
                    kill_tree(p)
                    cs.attempts.append({"at": time.time(), "elapsed": elapsed,
                                        "worker": f"{meta['model']}:{meta['thinking']}",
                                        "stuck_reason": reason, "flags": []})
                    self.board.save()
                    if not replacement_used and cs.status == STATUS_SOLVING:
                        replacement_used = True
                        if configs:
                            dispatch(len(configs), configs[0], base_prompt + LOOP_WARNING)
                            print(f"[{cid}] replacement worker dispatched with loop warning")

            finished = [p for p in procs if p.poll() is not None]
            for p in finished:
                meta = procs.pop(p)
                elapsed = time.time() - starts.get(p, time.time())
                log_text = meta["log"].read_text(encoding="utf-8", errors="replace") \
                    if meta["log"].exists() else ""
                parsed = parse_worker_output(log_text)
                flags = parsed["flags"]
                cs.attempts.append({"at": time.time(), "elapsed": elapsed,
                                    "worker": f"{meta['model']}:{meta['thinking']}",
                                    "output_tail": parsed["final_text"][-4000:],
                                    "flags": flags})
                self.board.save()
                print(f"[{cid}] worker {meta['model']} done in {elapsed:.0f}s, flags={flags[:3]}")
                if flags:
                    for flag in flags[:3]:
                        if cs.status == STATUS_SOLVED:
                            solved = True
                            break
                        if cs.verify_required:
                            cs.triage["pending_flags"] = flags
                            cs.transition(STATUS_NEEDS_HINT)
                            self.board.save()
                            print(f"[{cid}] verify required: candidates {flags[:3]}")
                            break
                        msg, ok = self.submit_policy.try_submit(self.board, cid, flag)
                        print(f"[{cid}] submit {flag[:24]}... -> {msg}")
                        if ok:
                            solved = True
                            break
                if solved:
                    break
            time.sleep(2)

        if solved:
            for p in procs:
                kill_tree(p)
            print(f"[{cid}] race won; other workers terminated")
        else:
            if cs.status == STATUS_SOLVING:
                cs.transition(STATUS_QUEUED)  # 下一轮再试（预算内）
                self.board.save()
            print(f"[{cid}] race finished unsolved")

    # ---------- 人工请求协议（看板/人工通过文件与本进程通信，无锁竞态） ----------
    def _process_requests(self) -> None:
        req_dir = self.ws / "requests"
        confirm_dir = req_dir / "confirm"
        confirm_dir.mkdir(parents=True, exist_ok=True)
        for f in confirm_dir.glob("*.json"):
            cid = f.stem
            cs = self.board.get(cid)
            try:
                payload = json.loads(f.read_text(encoding="utf-8"))
                flag = str(payload.get("flag", "")).strip()
            except Exception:
                flag = ""
            f.unlink(missing_ok=True)
            if not flag or cs is None or cs.status == STATUS_SOLVED:
                continue
            msg, ok = self.submit_policy.try_submit(self.board, cid, flag)
            print(f"[confirm] {cid} -> {msg}")

        verify_dir = req_dir / "verify"
        verify_dir.mkdir(parents=True, exist_ok=True)
        for f in verify_dir.glob("*.toggle"):
            cid = f.stem
            cs = self.board.get(cid)
            f.unlink(missing_ok=True)
            if cs is None:
                continue
            cs.verify_required = not cs.verify_required
            self.board.save()
            print(f"[verify] {cid} verify_required={cs.verify_required}")

    # ---------- 轮次 ----------
    def run_round(self) -> None:
        self._process_requests()
        n = self._sync()
        print(f"round: {len(self.board.challenges)} challenges known ({n} new)")
        open_cids = self.board.open_cids()
        if self.only:
            open_cids = [c for c in open_cids if c in self.only]
        # triage 排序：先易后难（3 小时抢分策略）
        if self.model_config.get("triage_order", "easy-first") == "easy-first":
            order = {"easy": 0, "medium": 1, "hard": 2, "unknown": 1}
            open_cids.sort(key=lambda c: order.get(
                (self.board.get(c) or ChallengeState(c, {})).triage.get("difficulty_guess", "unknown"), 1))
        max_par = int(self.model_config.get("max_parallel_challenges", 3))
        with ThreadPoolExecutor(max_workers=max_par) as pool:
            list(pool.map(self._run_one, open_cids))

    def loop(self, interval: int) -> None:
        while True:
            try:
                self.run_round()
            except Exception as e:
                print(f"round error: {e}")
            time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ctf_orchestrator")
    p.add_argument("--workspace", default="D:/ctf-agent/workspace")
    p.add_argument("--once", action="store_true")
    p.add_argument("--loop", type=int, default=0, metavar="SECONDS")
    p.add_argument("--pi-cmd", default=None)
    p.add_argument("--model-config", default=None)
    p.add_argument("--no-cleanup", action="store_true", help="跳过孤儿清理")
    p.add_argument("--platform", choices=["auto", "mock", "dasctf"], default="auto")
    p.add_argument("--no-planning", action="store_true", help="禁用规划器")
    p.add_argument("--only", default="", help="只处理指定 cid（逗号分隔，调试用）")
    args = p.parse_args(argv)

    if not args.no_cleanup:
        try:
            killed = cleanup_orphans()
            if killed:
                print(f"cleaned {killed} orphan workers")
        except Exception as e:
            print(f"orphan cleanup failed: {e}")

    base_url = os.environ.get("DASCTF_BASE_URL", "https://game.gcsis.cn")
    plat_kind = args.platform
    if plat_kind == "auto":
        plat_kind = "mock" if ("127.0.0.1" in base_url or "localhost" in base_url) else "dasctf"
    if plat_kind == "mock":
        platform = MockHttpPlatform(base_url)
    else:
        from platform import DasctfPlatform
        platform = DasctfPlatform(base_url)

    pi_cmd = json.loads(args.pi_cmd) if args.pi_cmd else DEFAULT_PI_CMD
    model_config = DEFAULT_MODEL_CONFIG
    if args.model_config and Path(args.model_config).exists():
        model_config = json.loads(Path(args.model_config).read_text(encoding="utf-8"))
    if args.no_planning:
        model_config = {**model_config, "planning_enabled": False}

    orch = Orchestrator(Path(args.workspace), platform, pi_cmd, model_config,
                        only={c.strip() for c in args.only.split(",") if c.strip()} or None)
    if args.loop > 0:
        orch.loop(args.loop)
    else:
        orch.run_round()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
