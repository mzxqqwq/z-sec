"""僵局检测（编排器级）——基于 pi json 事件流的工具调用序列分析。

移植思路：LLM-CTF-Solver 的 6 维僵局检测（solve_agent.py:1393-1479，已核验）+
verialabs LoopDetector 的签名思想（loop_detect.py，已核验），针对我们"worker 是
黑盒进程"的约束做了适配：不侵入 worker 循环，改为**实时读 worker 的 json 日志**，
从 turn_end 事件重建工具调用序列，在编排器侧判定并 kill/重派。

判定规则（3 小时限时收紧版）：
- D2 重复调用：最近 4 次调用 ≥3 次【工具名+参数签名】完全相同（精确签名，
  不是同名——web 题反复 curl 不同 URL 是正常行为，不算僵局）
- D3 重复输出：最近 3 个工具结果头 200 字符完全相同
- D6 错误率：最近 ≥5 个工具结果错误率 ≥60%
- idle：进程存活但 300 秒无新事件
"""
from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from typing import Any


def _signature(name: str, args: Any) -> str:
    """工具名 + 规范化参数（verialabs loop_detect.py 思想的自研实现）。"""
    if args is None:
        return name
    try:
        raw = json.dumps(args, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        raw = str(args)
    return f"{name}:{raw[:300]}"


class StuckMonitor:
    def __init__(self, log_path: Path, idle_seconds: float = 300.0) -> None:
        self.log_path = log_path
        self._pos = 0
        self._idle_seconds = idle_seconds
        self._last_event_at: float = time.monotonic()
        self._calls: deque[dict[str, Any]] = deque(maxlen=16)  # {name, sig, err, head}
        self._start_args: dict[str, Any] = {}  # toolCallId -> args（tool_execution_start）
        self._last_warned_at = 0.0

    def _read_new_lines(self) -> list[str]:
        if not self.log_path.exists():
            return []
        with open(self.log_path, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(self._pos)
            text = fh.read()
            self._pos = fh.tell()
        return text.splitlines()

    def poll(self) -> int:
        """读入新事件，返回本轮新解析出的工具结果条数。"""
        n = 0
        for line in self._read_new_lines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            etype = payload.get("type")
            if etype == "tool_execution_start":
                self._start_args[payload.get("toolCallId", "")] = payload.get("args")
                self._last_event_at = time.monotonic()
            elif etype == "turn_end":
                results = payload.get("toolResults")
                if not isinstance(results, list):
                    continue
                for r in results:
                    if not isinstance(r, dict):
                        continue
                    name = r.get("toolName", "")
                    args = self._start_args.pop(r.get("toolCallId", ""), None)
                    content = r.get("content")
                    head = ""
                    if isinstance(content, list):
                        texts = [c.get("text", "") for c in content if isinstance(c, dict)]
                        head = "\n".join(texts)[:200]
                    elif isinstance(content, str):
                        head = content[:200]
                    self._calls.append({
                        "name": name,
                        "sig": _signature(name, args),
                        "err": bool(r.get("isError")),
                        "head": head,
                    })
                    n += 1
                    self._last_event_at = time.monotonic()
        return n

    def is_stuck(self, alive: bool) -> tuple[bool, str]:
        """判定是否僵局。alive=False 时不判 idle（进程已死）。

        多阶段任务保护：当工具调用多样性高（≥8 种不同签名）时，说明 worker
        在真实推进阶段（如多层隐写：解压→解密→再解压），收紧规则阈值防止误杀。
        """
        calls = list(self._calls)
        unique_sigs = len({c["sig"] for c in calls})
        diverse = unique_sigs >= 8
        # D2 重复调用：最近 4 次 ≥3 次完全相同签名（高多样性时要求 4/4）
        if len(calls) >= 4:
            sigs = [c["sig"] for c in calls[-4:]]
            need = 4 if diverse else 3
            if any(sigs.count(x) >= need for x in set(sigs)):
                return True, "repeated identical call"
        # D3 重复输出：最近 3 个非空结果头完全相同
        heads = [c["head"] for c in calls[-3:] if c["head"]]
        if len(heads) == 3 and heads[0] == heads[1] == heads[2]:
            return True, "identical output"
        # D6 错误率：最近 ≥5 个结果错误率 ≥60%（高多样性时要求 ≥80%）
        recent = calls[-5:]
        if len(recent) >= 5:
            errs = sum(1 for c in recent if c["err"])
            threshold = 0.8 if diverse else 0.6
            if errs / len(recent) >= threshold:
                return True, f"error rate {errs}/{len(recent)}"
        # idle：进程活着但长时间无事件（多阶段任务放宽到 600s）
        idle_limit = 600.0 if diverse else self._idle_seconds
        if alive and time.monotonic() - self._last_event_at > idle_limit:
            return True, "idle"
        return False, ""

    def summarize(self) -> dict[str, Any]:
        calls = list(self._calls)
        return {
            "total_tool_calls": len(calls),
            "last_tools": [c["name"] for c in calls[-6:]],
            "error_rate": (sum(1 for c in calls if c["err"]) / len(calls)) if calls else 0.0,
        }
