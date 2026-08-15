#!/usr/bin/env python3
"""
ctf_orchestrator.py —— CTF 编排器 v3（AK 导向定版，2026-08-16）

定版决策（docs/定版方案-最终.md）：
- 删除 triage / races 预算 / dead 状态 / 僵局击杀 / 提交纪律（submit.py parked，平台规则回来再加）
- 每道题 1 强 + 1 弱 worker 竞速；3 题并发；解不出自动续派（ralph-loop 语义）
- 监督与纠偏由 supervisor.py（Observer 移植）负责，编排器不杀 worker
模块：state.py（黑板）/ workers.py（进程+解析）/ planning.py（总体思路）
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
    STATUS_SOLVED, STATUS_NEEDS_HINT  # noqa: E402
from workers import (kali_exec, kill_tree, parse_worker_output,  # noqa: E402
                     start_worker, cleanup_orphans)
from platform import BasePlatform, MockHttpPlatform  # noqa: E402
from planning import Planner  # noqa: E402
from supervisor import Supervisor  # noqa: E402

WORKER_TIMEOUT = 1500  # 单个 worker 硬上限
# 直接调 node cli.js，不经 PowerShell：
# PowerShell 5.1 把含双引号的原生参数转发给 node 时会拆成多个 argv
# （prompt 里的 "FLAG: ..." / 题目 JSON / 计划 JSON 全带引号），
# 以 - 开头的片段直接触发 pi CLI "Unknown option" 退出。
# Python subprocess 的 list2cmdline 会正确转义内嵌引号。
# 密钥与环境由 workers.start_worker 注入（见 _worker_env）。
DEFAULT_PI_CMD = [
    "node", r"D:\ctf-agent\pi-mono\packages\coding-agent\dist\cli.js",
    "--provider", "deepseek",
    "-e", r"D:\ctf-agent\src\pi-ext\kali.ts",
]

# 定版：无题型路由，每道题固定 1 强 + 1 弱
DEFAULT_MODEL_CONFIG = {
    "strong": {"model": "deepseek-v4-pro", "thinking": "medium"},
    "weak": {"model": "deepseek-v4-flash", "thinking": "low"},
    "max_parallel_challenges": 3,
    "planning_enabled": True,
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

【重要契约】
- 如果题目有多个 flag，不要因为提交对一个就停止，直到平台明确显示题目完成。
- 不要过早宣布失败；一条路线失败时换思路继续，而不是放弃。

{plan_section}
{continuation_section}
{board_section}
{reminder_section}
{human_hints}
"""

CONTINUATION_MESSAGE = """\
【续跑提示】你之前的一次尝试没有解出这道题。继续当前任务：
- 不要重复已经完成的步骤，基于你的新上下文继续推进；
- 如果上次方向已经排除，换一个完全不同的方向（检查遗漏的附件、端口、参数）。
"""


class Orchestrator:
    def __init__(self, workspace: Path, platform: BasePlatform, pi_cmd: list[str],
                 model_config: dict[str, Any],
                 max_attempts: int = 3,
                 only: set[str] | None = None) -> None:
        self.ws = workspace
        self.ws.mkdir(parents=True, exist_ok=True)
        (self.ws / "hints").mkdir(parents=True, exist_ok=True)
        (self.ws / "challenges").mkdir(parents=True, exist_ok=True)
        self.platform = platform
        self.pi_cmd = pi_cmd
        self.model_config = model_config
        # max_attempts 语义（定版后）：单题连续未解的自动续派上限（ralph-loop 层预算）
        self.max_attempts = max_attempts
        self.board = Board(workspace / "state.json")
        self.only = only
        self._challenges: dict[str, Any] = {}  # cid -> NormalizedChallenge（内存注册表）
        try:
            api_key = Planner.load_key_from_secrets()
            self.planner = Planner(api_key, enabled=bool(model_config.get("planning_enabled", True)))
        except Exception as e:
            print(f"[planner] disabled: {e}")
            self.planner = Planner("", enabled=False)
        try:
            self.supervisor = Supervisor(Planner.load_key_from_secrets(),
                                         enabled=bool(model_config.get("supervisor_enabled", True)))
        except Exception as e:
            print(f"[supervisor] disabled: {e}")
            self.supervisor = Supervisor("", enabled=False)

    # ---------- 平台 ----------
    def _platform_submit(self, cid: str, flag: str) -> Any:
        ch = self._challenges.get(cid)
        if ch is None:
            raise ValueError(f"unknown challenge {cid}")
        return self.platform.submit_flag(ch, flag)

    # ---------- 提交（定版：直接提交，无纪律；平台规则回来后再接 submit.py） ----------
    @staticmethod
    def _submission_accepted(res: Any) -> bool:
        if hasattr(res, "accepted"):
            return bool(res.accepted)
        if isinstance(res, dict):
            return bool(res.get("correct") or res.get("success") or res.get("accepted"))
        return res is True

    def _submit_direct(self, cid: str, flag: str) -> tuple[str, bool]:
        cs = self.board.get(cid)
        if cs is None:
            return "unknown challenge", False
        if cs.status == STATUS_SOLVED:
            return "already solved", True
        try:
            res = self._platform_submit(cid, flag)
        except Exception as e:
            return f"submit error: {e}", False
        ok = self._submission_accepted(res)
        if ok:
            cs.transition(STATUS_SOLVED)
            self.board.save()
            return "correct", True
        msg = getattr(res, "message", "") if hasattr(res, "message") else ""
        return f"incorrect ({msg})", False

    def _sync(self) -> int:
        new_count = 0
        for ch in self.platform.list_challenges():
            cid = ch.challenge_id
            self._challenges[cid] = ch
            if self.board.get(cid) is None:
                self.board.put(ChallengeState(cid, ch.to_prompt_json()))
                new_count += 1
        return new_count

    # ---------- hints ----------
    def _hints(self, cid: str) -> str:
        hint_file = self.ws / "hints" / f"{cid}.md"
        if hint_file.exists():
            return "\n\n【人工提示（最新优先）】\n" + hint_file.read_text(encoding="utf-8")
        return ""

    # ---------- worker 配置（定版：1 强 + 1 弱，无题型路由） ----------
    def _worker_configs(self, cs: ChallengeState) -> list[dict[str, str]]:
        strong = self.model_config.get("strong") or {"model": "deepseek-v4-pro", "thinking": "medium"}
        weak = self.model_config.get("weak") or {"model": "deepseek-v4-flash", "thinking": "low"}
        return [strong, weak]

    # ---------- 策略看板摘要（T7：Observer 写、worker 只读） ----------
    @staticmethod
    def _board_section(cs: ChallengeState) -> str:
        ideas = cs.board.get("ideas", []) or []
        memory = cs.board.get("memory", []) or []
        if not ideas and not memory:
            return ""
        lines = ["\n\n【策略看板（Observer 维护，供参考；与你的实测冲突时以实测为准）】"]
        if ideas:
            parts = [f"[{i.get('status','pending')}] {i.get('content','')}" for i in ideas[:8]]
            lines.append("待验证方向：" + "；".join(parts))
        if memory:
            parts = [f"[{m.get('kind','fact')}] {m.get('content','')}" for m in memory[:12]]
            lines.append("已知事实/边界：" + "；".join(parts))
        return "\n".join(lines)

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

    # ---------- 单题竞速（定版：无僵局击杀、无预算判死；解不出自动续派） ----------
    def _run_one(self, cid: str) -> None:
        cs = self.board.get(cid)
        if cs is None or cs.status not in (STATUS_NEW, STATUS_QUEUED, STATUS_NEEDS_HINT):
            return
        # 连续未解续派预算：超过后停在 needs_hint 等人工提示（ralph-loop 层预算）
        if cs.status == STATUS_NEEDS_HINT and len(cs.attempts) >= self.max_attempts * 2:
            self.board.save()
            print(f"[{cid}] waiting human hint (auto retries exhausted)")
            return

        cs.transition(STATUS_SOLVING)
        self.board.save()

        workdir = self.ws / "challenges" / cid
        workdir.mkdir(parents=True, exist_ok=True)
        remote_base = f"/root/ctf/{cid}"
        # 定版：每 worker 独立远程目录 w<idx>（防同题双 worker 文件互踩）
        remote_roots = {i: f"{remote_base}/w{i}" for i in range(len(self._worker_configs(cs)))}
        for root in remote_roots.values():
            self._sync_attachments(cid, workdir, root)

        # ---- planning（强模型出总体思路）----
        if cs.plan is None:
            cs.plan = self.planner.plan(
                cid, json.dumps(cs.raw, ensure_ascii=False, indent=2), len(cs.attempts))
            if cs.plan:
                print(f"[{cid}] plan generated ({len(cs.plan)} chars)")

        plan_section = ""
        if cs.plan:
            plan_section = f"\n\n【解题思路（规划器产出）】\n{cs.plan}"
        continuation_section = CONTINUATION_MESSAGE if cs.attempts else ""
        board_section = self._board_section(cs)
        reminder_section = ""
        if cs.triage.get("supervisor_reminder"):
            reminder_section = f"\n\n【Observer 纠偏提醒】\n{cs.triage['supervisor_reminder']}"
            cs.triage["supervisor_reminder"] = ""
        base_prompt = SOLVE_PROMPT_TEMPLATE.format(
            challenge_json=json.dumps(cs.raw, ensure_ascii=False, indent=2),
            plan_section=plan_section,
            continuation_section=continuation_section,
            board_section=board_section,
            reminder_section=reminder_section,
            human_hints=self._hints(cid))

        configs = self._worker_configs(cs)
        print(f"[{cid}] race: {len(configs)} workers "
              + ", ".join(f"{c['model']}:{c['thinking']}" for c in configs))

        procs: dict[Any, dict[str, Any]] = {}
        starts: dict[Any, float] = {}

        def dispatch(idx: int, cfg: dict[str, str], prompt: str) -> None:
            tag = f"{cfg['model']}:{cfg['thinking']}".replace("/", "_").replace(":", "-")
            log_path = workdir / f"worker_{idx}_{tag}.log"
            cmd = (self.pi_cmd + ["--model", cfg["model"], "--thinking", cfg["thinking"],
                                  "--mode", "json", "--kali", remote_roots[idx], "-p", prompt])
            proc = start_worker(cmd, workdir, log_path)
            procs[proc] = {**cfg, "log": log_path, "idx": idx}
            starts[proc] = time.time()
            print(f"[{cid}] worker {idx} started: {tag} (pid {proc.pid})")

        for i, cfg in enumerate(configs):
            dispatch(i, cfg, base_prompt)

        solved = False
        deadline = time.time() + WORKER_TIMEOUT
        while procs and not solved and time.time() < deadline:
            # ---- Supervisor 旁路审查（6 轮节奏，只纠偏不杀 worker）----
            for meta in procs.values():
                self.supervisor.feed_log(cid, meta["log"])
            changed, reminder = self.supervisor.maybe_review(cid, cs.raw, cs.board)
            if changed:
                self.board.save()
                print(f"[{cid}] supervisor updated board "
                      f"(ideas={len(cs.board['ideas'])}, memory={len(cs.board['memory'])})")
            if reminder:
                cs.triage["supervisor_reminder"] = reminder
                self.board.save()
                print(f"[{cid}] supervisor reminder: {reminder[:80]}...")

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
                        msg, ok = self._submit_direct(cid, flag)
                        print(f"[{cid}] submit {flag[:24]}... -> {msg}")
                        if ok:
                            solved = True
                            break
                if solved:
                    break
            time.sleep(2)

        # ---- 循环结束清理：未解且 deadline 到点 → 强杀剩余 worker（防孤儿/日志撞名）----
        live_logs = [m["log"] for m in procs.values()]
        if not solved:
            for p in list(procs):
                meta = procs.pop(p)
                elapsed = time.time() - starts.get(p, time.time())
                kill_tree(p)
                cs.attempts.append({"at": time.time(), "elapsed": elapsed,
                                    "worker": f"{meta['model']}:{meta['thinking']}",
                                    "timeout": True, "flags": []})
                self.board.save()
                print(f"[{cid}] worker {meta['model']} killed at deadline ({elapsed:.0f}s)")

        if solved:
            for p in procs:
                kill_tree(p)
            print(f"[{cid}] race won; other workers terminated")
        else:
            # ---- race 结束审查（agent_end 等价触发）----
            for lp in live_logs:
                self.supervisor.feed_log(cid, lp)
            changed, reminder = self.supervisor.maybe_review(cid, cs.raw, cs.board,
                                                             trigger="race_end")
            if changed:
                self.board.save()
                print(f"[{cid}] supervisor race-end review updated board")
            if reminder and not cs.triage.get("supervisor_reminder"):
                cs.triage["supervisor_reminder"] = reminder
                self.board.save()
                print(f"[{cid}] supervisor reminder (race-end): {reminder[:80]}...")
            # 未解 → needs_hint；下一轮自动续派（ralph-loop 语义：不放弃）
            if cs.status == STATUS_SOLVING:
                cs.transition(STATUS_NEEDS_HINT)
                self.board.save()
            print(f"[{cid}] unsolved; will auto-continue next round")

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
            msg, ok = self._submit_direct(cid, flag)
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
