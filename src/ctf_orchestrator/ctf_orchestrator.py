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
                     start_worker_rpc, send_rpc, cleanup_orphans,
                     CREATE_NEW_PROCESS_GROUP)
from platform import BasePlatform, MockHttpPlatform  # noqa: E402
from planning import Planner  # noqa: E402
from supervisor import Supervisor  # noqa: E402
from message_bus import ChallengeMessageBus  # noqa: E402

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
    "-e", r"D:\ctf-agent\src\pi-ext\loop-detect.ts",
]

# 定版：无题型路由，每道题固定 1 强 + 1 弱
DEFAULT_MODEL_CONFIG = {
    "strong": {"model": "deepseek-v4-pro", "thinking": "medium"},
    "weak": {"model": "deepseek-v4-flash", "thinking": "low"},
    "max_parallel_challenges": 3,
    "planning_enabled": True,
}

SOLVE_PROMPT_TEMPLATE = """\
你是一名 CTF 选手，正在参加一场 CTF 竞赛（Jeopardy 赛制）。请独立解出下面这道题并给出 flag。

题目信息：
{challenge_json}

附件已下载到工作目录 attachments/（如无附件则忽略）。若题目信息里给了连接信息
（connection/host/port），说明这是远程服务题：先用 nc 连上去摸清交互，再写 exp。

工作环境说明：你的 bash/文件工具实际运行在一台装好 pwntools/angr/z3/sympy/
nmap/sqlmap/binwalk/john/hashcat/radare2 的 Kali Linux 上（远程）。
你可以自由地写脚本、运行工具、连网络服务。解题成功后，用
"FLAG: <flag内容>" 一行输出最终答案。

【重要契约】
- 如果题目有多个 flag，不要因为提交对一个就停止，直到平台明确显示题目完成。
- 不要过早宣布失败；一条路线失败时换思路继续，而不是放弃。
- flag 格式以你找到的为准（flag{{...}}、csawctf{{...}}、DASCTF{{...}}、HTB{{...}} 等都可能
  出现）：找到完整 flag 就原样提交，不要因为格式与你的预期不符而丢弃或改写。
- 不要在机器上翻找 flag 文件、不要 grep 文件系统猜答案——flag 一定来自解题过程
  （解出算法/利用漏洞/连上服务拿到的才算数）。
- 服务题连接失败（DNS 解析失败/连接超时）时：最多重试 2-3 次就止损，不要反复探测网络；
  把算法/漏洞侧先解出来写成本地脚本（bash 支持多行 heredoc 脚本），再联网搜索公开题解
  交叉验证，最后用 submit_flag 提交确认。
- 若题目信息里 service_status=dead，说明该题远程服务探测为已停服：不要反复尝试连接，
  直接把算法/漏洞解出来写脚本，联网找公开题解验证后提交。
- 提交答案：优先调用 submit_flag 工具（参数 flag 为完整 flag 字符串），平台返回 correct 即完成；
  卡住时可用 get_hint 工具获取官方提示。仅当这两个工具不可用时，才用 "FLAG: <flag内容>" 一行输出。
- 同题有另一个 worker 在并行解题：每几步调用一次 check_findings 工具查看它的新发现，
  避免重复它已排除的方向；但它的发现是参考，与你的实测冲突时以实测为准。

{plan_section}
{continuation_section}
{board_section}
{reminder_section}
{human_hints}
{net_policy_section}
"""

BENCH_NET_NOTICE = """\
【benchmark 网络封锁】本场为能力评测：严禁联网搜索题目/题解（curl/wget/git/pip/外联
命令会被工具层拦截并报错）。题目附件 + 本地靶机（127.0.0.1）足以解题，请完全依赖
自己的分析。这是硬性要求，不要浪费时间尝试绕过。"""

CONTINUATION_MESSAGE = """\
【续跑提示】你之前的一次尝试没有解出这道题。继续当前任务：
- 不要重复已经完成的步骤，基于你的新上下文继续推进；
- 如果上次方向已经排除，换一个完全不同的方向（检查遗漏的附件、端口、参数）。
"""

CONCLUDE_MESSAGE = """\
时间到。停止探索，进入收尾：用一段话总结本次已确认的成果与未完成项；
如果发现了可能的完整 flag，直接输出一行 "FLAG: <flag内容>"。不要开始新的探索。
"""


def _count_new_agent_ends(log_path: Path, offset: int) -> tuple[int, int]:
    """增量统计日志中 agent_end 事件数。返回 (新增数, 新偏移)。"""
    try:
        size = log_path.stat().st_size
    except OSError:
        return 0, offset
    if size < offset:
        offset = 0
    count = 0
    with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
        fh.seek(offset)
        for line in fh:
            if '"type":"agent_end"' in line or '"type": "agent_end"' in line:
                count += 1
        offset = fh.tell()
    return count, offset


class Orchestrator:
    def __init__(self, workspace: Path, platform: BasePlatform, pi_cmd: list[str],
                 model_config: dict[str, Any],
                 max_attempts: int = 3,
                 only: set[str] | None = None,
                 bench_mode: bool = False) -> None:
        self.ws = workspace
        self.ws.mkdir(parents=True, exist_ok=True)
        (self.ws / "hints").mkdir(parents=True, exist_ok=True)
        (self.ws / "challenges").mkdir(parents=True, exist_ok=True)
        self.platform = platform
        self.pi_cmd = pi_cmd
        self.model_config = model_config
        # benchmark 模式：给 worker 注入 NET_POLICY=local-only（工具层封锁外联，
        # benchmark 题公开可搜而比赛题搜不到——防开卷抄解，2026-08-17）
        self.bench_mode = bench_mode
        # max_attempts 语义（定版后）：单题连续未解的自动续派上限（ralph-loop 层预算）
        self.max_attempts = max_attempts
        self.board = Board(workspace / "state.json")
        self.only = only
        self._challenges: dict[str, Any] = {}  # cid -> NormalizedChallenge（内存注册表）
        try:
            import agent_config  # 统一配置中心（config/agent.json + Web UI）
            pcfg = agent_config.llm("planner")
            p_model = pcfg.get("model") or "deepseek-v4-pro"
            raw = agent_config.raw_llm(p_model)
            self.planner = Planner(raw["api_key"], raw["base_url"], model=p_model,
                                   enabled=bool(model_config.get("planning_enabled", True)))
        except Exception as e:
            print(f"[planner] disabled: {e}")
            self.planner = Planner("", enabled=False)
        try:
            # Observer 对齐 BreachWeave：独立 pi 会话 + 工具落动作（无 JSON 解析，2026-08-16）
            obs_cfg = model_config.get("observer") or model_config.get("strong")
            self.supervisor = Supervisor(self.pi_cmd, self.ws, obs_cfg,
                                         enabled=bool(model_config.get("supervisor_enabled", True)))
        except Exception as e:
            print(f"[supervisor] disabled: {e}")
            self.supervisor = Supervisor(self.pi_cmd, self.ws, None, enabled=False)

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
            human_hints=self._hints(cid),
            net_policy_section=("\n" + BENCH_NET_NOTICE) if self.bench_mode else "")

        configs = self._worker_configs(cs)
        print(f"[{cid}] race: {len(configs)} workers "
              + ", ".join(f"{c['model']}:{c['thinking']}" for c in configs))

        procs: dict[Any, dict[str, Any]] = {}
        starts: dict[Any, float] = {}
        bus_path = workdir / "message_bus.json"
        if not bus_path.exists():
            bus_path.write_text('{"findings": [], "cursors": {}}', encoding="utf-8")
        bus = ChallengeMessageBus(bus_path)

        def dispatch(idx: int, cfg: dict[str, str], prompt: str) -> None:
            tag = f"{cfg['model']}:{cfg['thinking']}".replace("/", "_").replace(":", "-")
            log_path = workdir / f"worker_{idx}_{tag}.log"
            cmd = (self.pi_cmd + ["--model", cfg["model"], "--thinking", cfg["thinking"],
                                  "--mode", "rpc", "--kali", remote_roots[idx],
                                  "--cid", cid])
            proc = start_worker_rpc(cmd, workdir, log_path,
                                    extra_env={"MESSAGE_BUS_FILE": str(bus_path),
                                               "WORKER_TAG": tag,
                                               "NET_POLICY": "local-only" if self.bench_mode else "",
                                               "KB_ENABLED":
                                                   "1" if self.model_config.get("kb_enabled") else "0"})
            procs[proc] = {**cfg, "log": log_path, "idx": idx,
                           "proc": proc, "agent_ends": 0, "log_offset": 0}
            starts[proc] = time.time()
            # rpc 模式不消费 -p：初始任务经 stdin prompt 下发
            send_rpc(proc, {"type": "prompt", "message": prompt, "streamingBehavior": "steer"})
            print(f"[{cid}] worker {idx} started: {tag} (pid {proc.pid})")

        for i, cfg in enumerate(configs):
            dispatch(i, cfg, base_prompt)

        solved = False
        conclude_sent = False
        deadline = time.time() + WORKER_TIMEOUT
        while procs and not solved and time.time() < deadline:
            # ---- 经 worker-api 工具提交成功 → 题目已解，收尾（不依赖 worker 退出带 flag）----
            if cs.status == STATUS_SOLVED:
                solved = True
                print(f"[{cid}] solved via worker submit tool; stopping race")
                break
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

            # ---- ralph-loop 进程内续跑：agent_end 且未解 → 注入继续（T11，实测 prompt+steer 可触发新回合）----
            for p, meta in procs.items():
                if p.poll() is not None:
                    continue
                n, off = _count_new_agent_ends(meta["log"], meta["log_offset"])
                meta["log_offset"] = off
                if n > 0 and not conclude_sent and (deadline - time.time()) > 120:
                    if send_rpc(p, {"type": "prompt", "message": CONTINUATION_MESSAGE.strip(),
                                    "streamingBehavior": "steer"}):
                        print(f"[{cid}] {meta['model']} agent_end -> continuation prompt")

            # ---- conclude（T11）：deadline 前 90s 收尾总结，不丢成果 ----
            if not conclude_sent and time.time() > deadline - 90:
                conclude_sent = True
                for p in list(procs):
                    if p.poll() is not None:
                        continue
                    send_rpc(p, {"type": "abort"})
                    send_rpc(p, {"type": "prompt", "message": CONCLUDE_MESSAGE,
                                 "streamingBehavior": "steer"})
                print(f"[{cid}] conclude phase: abort + summary request sent")

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
                # T10：worker 完成一轮 → 投递发现摘要到 message bus（他人未读可见）
                if parsed["final_text"]:
                    bus.post(f"{meta['model']}:{meta['thinking']}",
                             parsed["final_text"][:500])
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

    # ---------- worker-api（:8089，kali.ts 的 submit_flag/get_hint 工具回调入口） ----------
    def start_kb(self, port: int = 8099) -> None:
        """KB 服务懒启动（kb_enabled 时）：ping 不通则拉起 kb_server.py。"""
        import urllib.request
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/ping", timeout=2) as r:
                r.read()
            print(f"[kb] already running on :{port}")
            return
        except Exception:
            pass
        try:
            import subprocess
            subprocess.Popen(
                [sys.executable, "-X", "utf8",
                 str(Path(__file__).resolve().parent / "kb_server.py"),
                 "--port", str(port)],
                cwd=str(Path(__file__).resolve().parent),
                creationflags=CREATE_NEW_PROCESS_GROUP,
            )
            print(f"[kb] launched on :{port}")
        except Exception as e:
            print(f"[kb] launch failed: {e}")

    def start_worker_api(self, port: int = 8089) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        orch = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # 静默访问日志
                pass

            def _json(self, code, payload):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/ping":
                    self._json(200, {"ok": True})
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self):
                try:
                    length = int(self.headers.get("Content-Length", 0) or 0)
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except Exception:
                    payload = {}
                if self.path == "/worker-submit":
                    cid = str(payload.get("cid", ""))
                    flag = str(payload.get("flag", "")).strip()
                    try:
                        msg, ok = orch._submit_direct(cid, flag)
                    except Exception as e:
                        msg, ok = f"submit error: {e}", False
                    print(f"[worker-api] submit {cid} {flag[:24]}... -> {msg}")
                    self._json(200, {"correct": ok, "message": msg})
                elif self.path == "/worker-hint":
                    cid = str(payload.get("cid", ""))
                    ch = orch._challenges.get(cid)
                    hint = orch.platform.get_hint(ch) if ch is not None else "该题无官方提示"
                    self._json(200, {"hint": hint})
                else:
                    self._json(404, {"error": "not found"})

        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        except OSError as e:
            print(f"[worker-api] port {port} busy; workers fall back to text-flag extraction ({e})")
            return
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"[worker-api] listening on http://127.0.0.1:{port}")

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
    # 统一配置中心优先（config/agent.json，Web UI 可改）；--model-config 显式覆盖
    try:
        import agent_config
        model_config = agent_config.build_model_config()
    except Exception:
        model_config = DEFAULT_MODEL_CONFIG
    if args.model_config and Path(args.model_config).exists():
        model_config = json.loads(Path(args.model_config).read_text(encoding="utf-8"))
    if args.no_planning:
        model_config = {**model_config, "planning_enabled": False}

    orch = Orchestrator(Path(args.workspace), platform, pi_cmd, model_config,
                        only={c.strip() for c in args.only.split(",") if c.strip()} or None)
    if model_config.get("kb_enabled"):
        orch.start_kb()
    orch.start_worker_api()
    if args.loop > 0:
        orch.loop(args.loop)
    else:
        orch.run_round()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
