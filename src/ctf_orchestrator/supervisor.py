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


def parse_rounds(log_path: Path) -> list[dict[str, Any]]:
    """worker jsonl → 轮次列表（每轮 = assistant 摘要 + 工具日志摘要）。

    轮边界 = turn_end 事件；一轮内收集该轮全部 tool_execution_start/end。
    """
    rounds: list[dict[str, Any]] = []
    cur_tools: list[dict[str, Any]] = []
    cur_summary = ""
    tool_started: dict[str, dict[str, Any]] = {}
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return rounds
    for line in lines:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type")
        if etype == "tool_execution_start":
            args = ev.get("args") or {}
            tool_started[ev.get("toolCallId", "")] = {
                "tool_name": ev.get("toolName", ""),
                "args_summary": _clip(json.dumps(args, ensure_ascii=False, sort_keys=True), 160),
                "result_summary": "",
                "is_error": False,
            }
        elif etype == "tool_execution_end":
            entry = tool_started.pop(ev.get("toolCallId", ""), None)
            if entry is not None:
                result = ev.get("result")
                if isinstance(result, dict):
                    entry["result_summary"] = _clip(
                        json.dumps(result, ensure_ascii=False), 160)
                else:
                    entry["result_summary"] = _clip(result, 160)
                entry["is_error"] = bool(ev.get("isError"))
                cur_tools.append(entry)
        elif etype == "turn_end":
            msg = ev.get("message") or {}
            content = msg.get("content") or []
            texts = [c.get("text", "") for c in content
                     if isinstance(c, dict) and c.get("type") == "text"]
            cur_summary = _clip("\n".join(texts), 300)
            rounds.append({"assistant_summary": cur_summary, "tool_logs": cur_tools})
            cur_tools = []
            cur_summary = ""
    if cur_tools:
        rounds.append({"assistant_summary": cur_summary or "(进行中)", "tool_logs": cur_tools})
    return rounds


class Supervisor:
    """每 cid 一个进程内状态（增量日志 feed/审查节奏/提醒冷却）；审查 = 独立 pi 观察者会话。"""

    def __init__(self, pi_cmd: list[str], workdir: Path,
                 observer_cfg: Optional[dict[str, str]] = None,
                 enabled: bool = True) -> None:
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
        self._state: dict[str, dict[str, Any]] = {}   # cid -> {since_review, last_reminder_round, last_reminder_msg}
        self._feeds: dict[str, dict[str, Any]] = {}   # cid -> 增量解析状态

    # ---------- 增量日志 feed（轮次 = turn_end 事件） ----------
    def _feed(self, cid: str) -> dict[str, Any]:
        feed = self._feeds.get(cid)
        if feed is None:
            feed = {"offset": 0, "rounds": [], "cur_tools": [],
                    "cur_summary": "", "tool_started": {}}
            self._feeds[cid] = feed
        return feed

    def feed_log(self, cid: str, log_path: Path) -> int:
        """增量读一个 worker 日志，更新轮次列表。返回当前轮次数。"""
        feed = self._feed(cid)
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

    def rounds_of(self, cid: str) -> list[dict[str, Any]]:
        return self._feed(cid)["rounds"]

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

        def _saw_agent_end() -> bool:
            try:
                text = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return False
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(ev, dict) and ev.get("type") == "agent_end":
                    return True
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

    # ---------- 节奏化审查入口 ----------
    def maybe_review(self, cid: str, challenge_raw: dict[str, Any], board: dict[str, Any],
                     trigger: str = "periodic") -> tuple[bool, Optional[str]]:
        """返回 (board_changed, reminder)。按 6 轮节奏 + 提醒冷却/指纹去重；
        看板变更直接写进传入的 board dict（调用方保存）。"""
        st = self._st(cid)
        rounds = self._feed(cid)["rounds"]
        total = len(rounds)
        if not self.should_review(cid, total, force=(trigger != "periodic")):
            return False, None
        st["since_review"] = total
        if not self.enabled or not rounds:
            return False, None

        prompt = self._build_prompt(cid, challenge_raw, board, rounds, trigger)
        new_board, reminder = self._run_observer(cid, board, prompt)
        if new_board is None:
            print(f"[supervisor] {cid} observer session failed (fallback NO_CHANGE)")
            return False, None

        ideas = new_board.get("ideas") or []
        memory = new_board.get("memory") or []
        old_sig = json.dumps({"ideas": board.get("ideas", []),
                              "memory": board.get("memory", [])},
                             sort_keys=True, ensure_ascii=False)
        new_sig = json.dumps({"ideas": ideas, "memory": memory},
                             sort_keys=True, ensure_ascii=False)
        changed = new_sig != old_sig
        if changed:
            board["ideas"] = ideas
            board["memory"] = memory
            print(f"[supervisor] {cid} board updated (ideas={len(ideas)}, memory={len(memory)})")

        # 提醒冷却 + 指纹去重（BreachWeave observer-loop.ts:72-106 语义）
        if reminder:
            within_cooldown = (total - st["last_reminder_round"]) < REMINDER_COOLDOWN_ROUNDS
            same_msg = reminder == st["last_reminder_msg"]
            if within_cooldown or same_msg:
                reminder = None
            else:
                st["last_reminder_round"] = total
                st["last_reminder_msg"] = reminder
        return changed, reminder
