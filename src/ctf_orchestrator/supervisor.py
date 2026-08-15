"""Supervisor —— BreachWeave Observer 的旁路监督移植（observer-agent.ts:11-179）。

定版（2026-08-16）：监督者不杀 worker、不代解、不提交；每 6 轮（或 race 结束）审查一次，
把最近 10 轮压缩轨迹喂给强模型，产出策略看板（Idea/Memory）维护动作 + 可选效率提醒。
纠偏注入下一轮派工提示词（切 pi rpc 后升级为 steer 实时注入）。

与 BreachWeave 的差异（都因我们是"编排器读日志"而非"进程内扩展"）：
- 轮次 = worker 日志里 turn_end 事件计数；审查在编排器侧定时触发；
- 看板落 state.json 的 board 字段，worker 只读（提示词注入摘要）；
- 纠偏 v1 = 注入下一轮提示词。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

import requests

SYSTEM_PROMPT = """\
你是 CTF 解题 Agent 的 Observer 副驾驶（旁路监督者）。
你不是 solver：不推进解题、不执行工具、不获取 hint、不提交 flag。
你的唯一职责是维护这道题的策略看板（ideas 与 memory），使其紧凑、准确、低噪音。

默认立场（按序执行，这是立场不是建议）：
NO_CHANGE > update existing > delete superseded > add new

核心循环（每轮审查只按这个顺序，不要跳步）：
1. 先看当前 ideas 和 memory。
2. 先闭环已有主线：最近几轮结果是否证实、证伪或推进了某条已有 idea？能闭环就更新它的 status / result。
3. 如果只是某个 payload、编码、子分支、利用姿势失败，先记 failure 边界，不要判死整条主线。
4. 只有新结果无法承接到现有主线、且确实打开了不同攻击方向时，才新增 idea。
5. 既没有新方向、也没有更强的边界结论，就保持 NO_CHANGE（空动作）。

### Ideas（方向假设）
- idea 只表示"接下来值得测试什么"，不是事实、不是过程记录。
- 好的 idea 必须具体、可执行、可验证。不要拆出近义/同级/上下级重复 idea。
- 生命周期通过 status 推进：pending / testing / verified / failed / skipped。
- 对 failed 要最保守。判 failed 前连续自问：
  ① 这次失败否定的是整条路线，还是只否定了某个 payload/编码/子分支？
  ② 这条路线是否仍有合理变体、上下文条件或未验证前提？
  ③ 更适合把失败边界写进 memory，而不是关闭主线吗？
  任一存疑就不要判 failed，保持 testing 或退回更窄的 pending。
- verified/failed 时 result 必须包含决定性证据摘要。

### Memory（durable facts）
- 保存压缩后仍必须留下的事实/证据/失败边界/提示/约束。
- 合并重于累加：同主题先 memory_update，不要新增近义记录。
- failure 写成边界结论，不是动作流水（例："对 /login 的 union/time/error SQLi 均失败，疑似参数化"）。
- 环境限制或隐含约束是高优先级 memory（无外网、只读文件系统、缺依赖、沙箱限制）。
- 弱记录/重复/过时/被更强结论覆盖的应 update 或 delete，不允许近义长期并存。

### 体积硬上限（这些记录会进 solver 上下文）
- memory 保持 ≤12 条（每条 ≤220 字）、ideas ≤8 条（每条 ≤120 字）。
- 超限时压缩本身是优先动作：先 merge/update/delete，再考虑 add。

### 效率提醒（最后手段）
四个前提必须同时满足才输出 reminder：
1. 当前方法明显低效、重复、低信息增量（如手工逐个 fuzz、逐目录手工列举、重复已被证明失败的 payload）；
2. 这种状态持续出现，而非正常验证中的短暂停留；
3. solver 不处于合理主线的正常推进阶段；
4. 如果之前提醒过，之后几轮没有真正改线或又回到同一低效模式。
提醒内容短、具体、可执行：当前低效行为 + 更高效的替代方向。
如果 solver 已切到新方向，即使不完美也不要再打断。

### 硬约束
- 主 Agent 对 ideas 是只读的；ideas 只由你维护。
- 你不能自己提交 flag、获取 hint、或执行 solver 行为。
- 不要为了"看起来有动作"而新增/改写/删除。
- 不要做颠覆性重写，不要一次性大范围改动看板。
- 不要仅因最近几轮没提到某条记录就删除它。
- 没有明确证据不要随意回退已有 idea 状态。
- 看板文字像代码注释一样精炼，保留假设、边界和证据，不是过程流水。

### 输出契约
只输出一个 JSON 对象：
{"memory_actions": [{"action":"add|update|delete","kind":"fact|evidence|failure|hint","content":"...","old_content":"仅 update/delete 需要"}],
 "idea_actions": [{"action":"add|update","content":"...","status":"pending|testing|verified|failed|skipped","result":"证据摘要（verified/failed 必填）","old_content":"仅 update 需要"}],
 "reminder": null 或 "提醒文本"}
无需修改时 memory_actions/idea_actions 为空数组、reminder 为 null。
"""

REVIEW_MODEL = "deepseek-v4-pro"
REVIEW_EVERY_ROUNDS = 6
REVIEW_WINDOW_ROUNDS = 10
REMINDER_COOLDOWN_ROUNDS = 6
MAX_TOKENS = 1200
TIMEOUT = 120


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
    """每 cid 一个进程内状态（增量日志 feed/审查节奏/提醒冷却），看板动作落地到 state.json board。"""

    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com",
                 enabled: bool = True) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
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

    # ---------- 审查 ----------
    def review(self, cid: str, challenge_raw: dict[str, Any], board: dict[str, Any],
               rounds: list[dict[str, Any]], trigger: str) -> dict[str, Any]:
        """调强模型审查，返回 {memory_actions, idea_actions, reminder}。"""
        if not self.enabled or not rounds:
            return {"memory_actions": [], "idea_actions": [], "reminder": None}
        for window in (REVIEW_WINDOW_ROUNDS, max(3, REVIEW_WINDOW_ROUNDS // 2)):
            data = self._call_review(cid, challenge_raw, board, rounds[-window:], trigger)
            if data is not None:
                return data
        print(f"[supervisor] review gave no parseable output for {cid}")
        return {"memory_actions": [], "idea_actions": [], "reminder": None}

    def _call_review(self, cid: str, challenge_raw: dict[str, Any], board: dict[str, Any],
                     recent: list[dict[str, Any]], trigger: str) -> Optional[dict[str, Any]]:
        user = (
            "## Challenge State\n"
            + json.dumps({"id": challenge_raw.get("id", cid),
                          "name": challenge_raw.get("name", ""),
                          "category": challenge_raw.get("category", ""),
                          "description": _clip(challenge_raw.get("description", ""), 300)},
                         ensure_ascii=False, indent=2)
            + f"\n\n## Trigger\n{trigger}\n\n## Current Board\n"
            + json.dumps(board, ensure_ascii=False, indent=2)
            + "\n\n## Recent Solver Activity（最近轮次）\n"
            + json.dumps(recent, ensure_ascii=False, indent=2)
            + "\n\n## Response Contract\n只输出一个 JSON 对象。"
        )
        body = {
            "model": REVIEW_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            "max_tokens": MAX_TOKENS,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        try:
            r = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body, timeout=TIMEOUT,
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"].get("content") or ""
            data = json.loads(content)
        except Exception as e:
            print(f"[supervisor] review call failed: {e}")
            return None
        if not isinstance(data, dict):
            return None
        return {
            "memory_actions": data.get("memory_actions") or [],
            "idea_actions": data.get("idea_actions") or [],
            "reminder": data.get("reminder"),
        }

    # ---------- 看板动作落地 ----------
    @staticmethod
    def apply_board(board: dict[str, Any], actions: dict[str, Any]) -> bool:
        changed = False
        ideas: list[dict[str, Any]] = board.get("ideas", [])
        memory: list[dict[str, Any]] = board.get("memory", [])

        for a in actions.get("idea_actions", []) or []:
            if not isinstance(a, dict) or not a.get("content"):
                continue
            content = _clip(a["content"], 120)
            norm = content.strip().lower()
            if a.get("action") == "add":
                if any(i.get("content", "").strip().lower() == norm for i in ideas):
                    continue
                if len(ideas) >= 8:
                    continue
                ideas.append({
                    "content": content, "status": a.get("status", "pending"),
                    "result": _clip(a.get("result", ""), 200),
                    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                })
                changed = True
            elif a.get("action") == "update":
                old = _clip(a.get("old_content", ""), 120).strip().lower()
                for i in ideas:
                    if i.get("content", "").strip().lower() == old or not old:
                        if a.get("content"):
                            i["content"] = _clip(a["content"], 120)
                        if a.get("status"):
                            i["status"] = a["status"]
                        if a.get("result"):
                            i["result"] = _clip(a["result"], 200)
                        i["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                        changed = True
                        break

        for a in actions.get("memory_actions", []) or []:
            if not isinstance(a, dict) or not a.get("content"):
                continue
            kind = a.get("kind", "fact")
            if kind not in ("fact", "evidence", "failure", "hint"):
                kind = "fact"
            content = _clip(a["content"], 220)
            if a.get("action") == "add":
                if any(m.get("content", "") == content for m in memory):
                    continue
                if len(memory) >= 12:
                    continue
                memory.append({"kind": kind, "content": content,
                               "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})
                changed = True
            elif a.get("action") in ("update", "delete"):
                old = _clip(a.get("old_content", ""), 220)
                for m in list(memory):
                    if old and m.get("content", "") != old:
                        continue
                    if not old and m.get("kind") != kind:
                        continue
                    if a.get("action") == "delete":
                        memory.remove(m)
                    else:
                        m["content"] = content
                        m["kind"] = kind
                        m["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    changed = True
                    break
        board["ideas"] = ideas
        board["memory"] = memory
        return changed

    # ---------- 节奏化审查入口 ----------
    def maybe_review(self, cid: str, challenge_raw: dict[str, Any], board: dict[str, Any],
                     trigger: str = "periodic") -> tuple[bool, Optional[str]]:
        """返回 (board_changed, reminder)。按 6 轮节奏 + 提醒冷却/指纹去重。"""
        st = self._st(cid)
        rounds = self._feed(cid)["rounds"]
        total = len(rounds)
        if not self.should_review(cid, total, force=(trigger != "periodic")):
            return False, None
        st["since_review"] = total
        actions = self.review(cid, challenge_raw, board, rounds, trigger)
        changed = self.apply_board(board, actions)
        reminder = actions.get("reminder")
        if reminder and isinstance(reminder, str) and reminder.strip():
            msg = reminder.strip()
            within_cooldown = (total - st["last_reminder_round"]) < REMINDER_COOLDOWN_ROUNDS
            same_msg = msg == st["last_reminder_msg"]
            if within_cooldown or same_msg:
                reminder = None
            else:
                st["last_reminder_round"] = total
                st["last_reminder_msg"] = msg
        return changed, reminder
