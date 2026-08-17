"""Supervisor —— BreachWeave Observer 对齐版（pi 会话 + 工具落动作，2026-08-16 改造）。

为什么改（空输出事故根因，已复现定位）：
- 旧版裸调 /chat/completions + response_format=json_object，期望模型整段输出 JSON；
- deepseek-v4-pro 是推理模型，max_tokens=1200 被 reasoning 吃光（实测
  reasoning_tokens=1200=全额、content=''、finish_reason=length）→ json.loads("") 必炸；
- BreachWeave 原版（packages/core/src/solver/extension/challenge-observer/）不做 JSON
  解析：Observer 是独立 pi Agent 会话（createAgentSession，observer-agent.ts:360），
  结构化动作全部通过工具落地（memory_add/idea_update 等），模型最终回复只是文本摘要
  ——推理被截断也不影响动作落地。

本版架构：
- 每次审查起一个独立 pi 会话（-e observer.ts，无 kali.ts），cwd 是专用空目录；
- observer.ts 注册看板工具（board_list/idea_add/idea_update/memory_add/memory_update/
  memory_delete/send_efficiency_reminder），直接读写 <workdir>/observer/<cid>/board.json；
- 编排器只读回 board.json（不再解析模型输出），合并进 state.json 的 board 字段；
- 节奏/窗口/提醒冷却与去重逻辑保持不变（6 轮审查 / 10 轮窗口 / 6 轮冷却）。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Optional

REVIEW_EVERY_ROUNDS = 6
REVIEW_WINDOW_ROUNDS = 10
REMINDER_COOLDOWN_ROUNDS = 6
OBSERVER_TIMEOUT = 180          # 单次观察者会话硬上限（秒）
OBSERVER_TS = r"D:\ctf-agent\src\pi-ext\observer.ts"
DEFAULT_OBSERVER_CFG = {"model": "deepseek-v4-pro", "thinking": "medium"}


def _clip(s: Any, n: int) -> str:
    return str(s or "")[:n]


class Supervisor:
    """每 cid 一个进程内状态（增量日志 feed/审查节奏/提醒冷却）；审查 = 独立 pi 观察者会话。"""

    def __init__(self, pi_cmd: list[str], workdir: Path,
                 observer_cfg: Optional[dict[str, str]] = None,
                 enabled: bool = True,
                 solved_checker: Optional[Any] = None) -> None:
        # pi_cmd 形如 [node, cli.js, --provider deepseek, -e kali.ts, -e loop-detect.ts]；
        # 观察者会话只取第一个 -e 之前的公共前缀（node/cli/provider），换自己的扩展
        base: list[str] = []
        for part in pi_cmd:
            if part == "-e":
                break
            base.append(part)
        self.pi_base = base
        self.workdir = Path(workdir)
        cfg = observer_cfg or DEFAULT_OBSERVER_CFG
        self.model = str(cfg.get("model") or DEFAULT_OBSERVER_CFG["model"])
        self.thinking = str(cfg.get("thinking") or DEFAULT_OBSERVER_CFG["thinking"])
        self.enabled = enabled
        # 已解守卫：回调 cid -> bool（编排器传 lambda board.get(cid).status == solved）
        self.solved_checker = solved_checker
        self._state: dict[str, dict[str, Any]] = {}   # cid -> {since_review, last_reminder_round, last_reminder_msg, last_reminder_fp}
        # 修复（2026-08-17）：feed 必须按 (cid, log_path) 隔离——此前同一 cid 的两个
        # worker 日志共用一个 offset，seek 到错误位置，轮次/工具事件被漏读或重复计数
        # （BreachWeave 调研发现的"漏事件"根因）。
        self._feeds: dict[tuple[str, str], dict[str, Any]] = {}
        # 异步审查（2026-08-17）：观察者会话不再阻塞主控制循环
        self._lock = threading.Lock()
        self._reviewing: set[str] = set()
        self._pending_reminders: dict[str, str] = {}
        self._board_dirty: set[str] = set()

    # ---------- 增量日志 feed（轮次 = turn_end 事件，按文件隔离） ----------
    def _feed(self, cid: str, log_path: str) -> dict[str, Any]:
        key = (cid, log_path)
        feed = self._feeds.get(key)
        if feed is None:
            feed = {"offset": 0, "rounds": [], "cur_tools": [],
                    "cur_summary": "", "tool_started": {}}
            self._feeds[key] = feed
        return feed

    def feed_log(self, cid: str, log_path: Path) -> int:
        """增量读一个 worker 日志，更新该文件自己的轮次列表。返回该文件轮次数。"""
        feed = self._feed(cid, str(log_path))
        try:
            size = log_path.stat().st_size
        except OSError:
            return len(feed["rounds"])
        if size < feed["offset"]:  # 日志被重写（新一轮派工）
            feed.update({"offset": 0, "rounds": [], "cur_tools": [],
                         "cur_summary": "", "tool_started": {}})
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(feed["offset"])
            for line in fh:
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
                    args = ev.get("args") or {}
                    feed["tool_started"][ev.get("toolCallId", "")] = {
                        "tool_name": ev.get("toolName", ""),
                        "args_summary": _clip(json.dumps(args, ensure_ascii=False, sort_keys=True), 160),
                        "result_summary": "",
                        "is_error": False,
                    }
                elif etype == "tool_execution_end":
                    entry = feed["tool_started"].pop(ev.get("toolCallId", ""), None)
                    if entry is not None:
                        result = ev.get("result")
                        if isinstance(result, dict):
                            entry["result_summary"] = _clip(json.dumps(result, ensure_ascii=False), 160)
                        else:
                            entry["result_summary"] = _clip(result, 160)
                        entry["is_error"] = bool(ev.get("isError"))
                        feed["cur_tools"].append(entry)
                elif etype == "turn_end":
                    msg = ev.get("message") or {}
                    content = msg.get("content") or []
                    texts = [c.get("text", "") for c in content
                             if isinstance(c, dict) and c.get("type") == "text"]
                    feed["rounds"].append({
                        "assistant_summary": _clip("\n".join(texts), 300),
                        "tool_logs": feed["cur_tools"],
                    })
                    feed["cur_tools"] = []
            feed["offset"] = fh.tell()
        return len(feed["rounds"])

    # ---------- 触发节奏 ----------
    def _st(self, cid: str) -> dict[str, Any]:
        st = self._state.get(cid)
        if st is None:
            st = {"since_review": 0, "last_reminder_round": -999, "last_reminder_msg": ""}
            self._state[cid] = st
        return st

    def should_review(self, cid: str, round_count: int, force: bool = False) -> bool:
        if force:
            return True
        st = self._st(cid)
        return round_count - st["since_review"] >= REVIEW_EVERY_ROUNDS

    def _rounds_of(self, cid: str) -> list[dict[str, Any]]:
        """汇总该 cid 全部 worker 日志的轮次（双 worker 竞速 = 两个 feed 合并）。"""
        merged: list[dict[str, Any]] = []
        for (c, _), feed in self._feeds.items():
            if c == cid:
                merged.extend(feed["rounds"])
        return merged

    # ---------- 异步审查结果消费（主循环每轮调用） ----------
    def drain(self, cid: str) -> tuple[bool, Optional[str]]:
        """取走 (board_changed, reminder)。board 变更已由审查线程写入 board dict，
        调用方看到 dirty=True 时保存；reminder 由调用方 steer 注入存活 worker。"""
        with self._lock:
            dirty = cid in self._board_dirty
            if dirty:
                self._board_dirty.discard(cid)
            reminder = self._pending_reminders.pop(cid, None)
        return dirty, reminder

    # ---------- 观察者会话 ----------
    def _build_prompt(self, cid: str, challenge_raw: dict[str, Any], board: dict[str, Any],
                      rounds: list[dict[str, Any]], trigger: str) -> str:
        return (
            "## Challenge State\n"
            + json.dumps({"id": challenge_raw.get("id", cid),
                          "name": challenge_raw.get("name", ""),
                          "category": challenge_raw.get("category", ""),
                          "description": _clip(challenge_raw.get("description", ""), 300)},
                         ensure_ascii=False, indent=2)
            + f"\n\n## Trigger\n{trigger}\n\n## Current Board（本次审查前）\n"
            + json.dumps({"ideas": board.get("ideas", []), "memory": board.get("memory", [])},
                         ensure_ascii=False, indent=2)
            + "\n\n## Recent Solver Activity（最近轮次）\n"
            + json.dumps(rounds[-REVIEW_WINDOW_ROUNDS:], ensure_ascii=False, indent=2)
            + "\n\n## Response Contract\n"
              "先用 board_list 查看当前看板；无需修改就回复 NO_CHANGE；"
              "有修改通过工具落地，最终只回复 1-4 条短 bullet。"
        )

    def _run_observer(self, cid: str, board: dict[str, Any],
                      prompt: str) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        """起独立 pi 观察者会话，返回 (新看板 dict, reminder)。失败返回 (None, None)。"""
        from workers import start_worker_rpc, send_rpc, kill_tree
        obs_dir = self.workdir / "observer" / cid
        obs_dir.mkdir(parents=True, exist_ok=True)
        board_file = obs_dir / "board.json"
        board_file.write_text(
            json.dumps({"ideas": board.get("ideas", []),
                        "memory": board.get("memory", []),
                        "reminder": None},
                       ensure_ascii=False, indent=1),
            encoding="utf-8")
        log_path = obs_dir / "session.log"
        cmd = self.pi_base + ["-e", OBSERVER_TS,
                              "--model", self.model, "--thinking", self.thinking,
                              "--mode", "rpc", "--observer-board", str(board_file),
                              "--cid", cid]
        proc = start_worker_rpc(cmd, obs_dir, log_path)
        send_rpc(proc, {"type": "prompt", "message": prompt, "streamingBehavior": "steer"})

        # 增量扫描（2026-08-17）：记 offset，避免每秒全量重读整个 session.log
        scan = {"offset": 0}

        def _saw_agent_end() -> bool:
            try:
                size = log_path.stat().st_size
                if size < scan["offset"]:
                    scan["offset"] = 0
                with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(scan["offset"])
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(ev, dict) and ev.get("type") == "agent_end":
                            return True
                    scan["offset"] = fh.tell()
            except OSError:
                return False
            return False

        deadline = time.time() + OBSERVER_TIMEOUT
        while time.time() < deadline and proc.poll() is None:
            if _saw_agent_end():
                break
            time.sleep(1.0)
        if not _saw_agent_end():
            if proc.poll() is None:
                kill_tree(proc)
                print(f"[supervisor] {cid} observer timeout ({OBSERVER_TIMEOUT}s), killed")
            else:
                print(f"[supervisor] {cid} observer exited without agent_end")
            return None, None
        # rpc 会话 agent_end 后进程常驻等续发——观察者是一次性会话，等落盘后主动收掉
        time.sleep(2.0)
        if proc.poll() is None:
            kill_tree(proc)
        try:
            data = json.loads(board_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            print(f"[supervisor] {cid} board.json unreadable after session")
            return None, None
        if not isinstance(data, dict):
            return None, None
        reminder = data.pop("reminder", None)
        return data, (reminder if isinstance(reminder, str) and reminder.strip() else None)

    # ---------- 节奏化审查入口（2026-08-17 异步版） ----------
    def maybe_review(self, cid: str, challenge_raw: dict[str, Any], board: dict[str, Any],
                     trigger: str = "periodic") -> tuple[bool, Optional[str]]:
        """按 6 轮节奏排队一次后台审查，立即返回 (False, None)。
        结果经 drain(cid) 取走（board 变更直接写进传入的 board dict；reminder 由
        调用方 steer 注入存活 worker）。同一 cid 同时最多一个审查在飞。"""
        st = self._st(cid)
        rounds = self._rounds_of(cid)
        total = len(rounds)
        if not self.should_review(cid, total, force=(trigger != "periodic")):
            return False, None
        st["since_review"] = total
        if not self.enabled or not rounds:
            return False, None
        with self._lock:
            if cid in self._reviewing:
                return False, None
            self._reviewing.add(cid)
        snapshot = {"ideas": list(board.get("ideas", []) or []),
                    "memory": list(board.get("memory", []) or [])}
        prompt = self._build_prompt(cid, challenge_raw, board, rounds, trigger)
        threading.Thread(target=self._review_async,
                         args=(cid, board, snapshot, prompt, total, st, rounds),
                         daemon=True).start()
        return False, None

    def _review_async(self, cid: str, board: dict[str, Any], snapshot: dict[str, Any],
                      prompt: str, total: int, st: dict[str, Any],
                      rounds: list[dict[str, Any]]) -> None:
        """后台审查：起观察者会话 → 合并看板 → 记提醒。所有共享状态加锁。"""
        try:
            new_board, reminder = self._run_observer(cid, snapshot, prompt)
        except Exception as e:  # 线程内兜底，绝不炸主循环
            print(f"[supervisor] {cid} observer error: {e}")
            new_board, reminder = None, None
        with self._lock:
            self._reviewing.discard(cid)
            if new_board is not None:
                ideas = new_board.get("ideas") or []
                memory = new_board.get("memory") or []
                old_sig = json.dumps({"ideas": board.get("ideas", []),
                                      "memory": board.get("memory", [])},
                                     sort_keys=True, ensure_ascii=False)
                new_sig = json.dumps({"ideas": ideas, "memory": memory},
                                     sort_keys=True, ensure_ascii=False)
                if new_sig != old_sig:
                    board["ideas"] = ideas
                    board["memory"] = memory
                    self._board_dirty.add(cid)
                    print(f"[supervisor] {cid} board updated "
                          f"(ideas={len(ideas)}, memory={len(memory)})")
            # 提醒去重（对齐 BreachWeave observer-loop.ts:72-106）：
            # ① 冷却 6 轮；② 消息完全相同；③ activity 指纹相同（最近 3 轮工具序列没变 =
            # 模型在原地打转，语义相近的提醒不重发）；④ 已解不再发。
            if reminder:
                if self.solved_checker and self.solved_checker(cid):
                    reminder = None
                else:
                    within_cooldown = (total - st["last_reminder_round"]) < REMINDER_COOLDOWN_ROUNDS
                    same_msg = reminder == st["last_reminder_msg"]
                    fp = self._activity_fingerprint(rounds)
                    same_activity = fp and fp == st.get("last_reminder_fp")
                    if within_cooldown or same_msg or same_activity:
                        reminder = None
                    else:
                        st["last_reminder_round"] = total
                        st["last_reminder_msg"] = reminder
                        st["last_reminder_fp"] = fp
                        self._pending_reminders[cid] = reminder
            if new_board is None:
                print(f"[supervisor] {cid} observer session failed (fallback NO_CHANGE)")

    @staticmethod
    def _activity_fingerprint(rounds: list[dict[str, Any]]) -> str:
        """最近 3 轮的 (工具名, is_error) 序列指纹——活动没变说明在原地打转。"""
        sig: list[str] = []
        for rnd in rounds[-3:]:
            for tl in (rnd.get("tool_logs") or [])[-6:]:
                sig.append(f"{tl.get('tool_name', '?')}:{1 if tl.get('is_error') else 0}")
        return "|".join(sig)
