"""提交纪律（verialabs swarm.py 模式压缩版 + 去重）。

规则：
- 精确去重：同一 flag 只提交一次（跨 worker、跨轮次）
- 递增冷却：错交后按错误次数套 [0, 15, 60, 180] 秒（3 小时赛压缩版）
- 全局锁：并发竞速场景下提交串行化
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

ESCALATING_COOLDOWNS = [0, 15, 60, 180]  # 秒


class SubmissionPolicy:
    def __init__(self, submit_fn: Callable[[str, str], Any], max_wrong_submits: int = 3) -> None:
        self._submit_fn = submit_fn
        self._max_wrong = max_wrong_submits
        self._lock = threading.Lock()

    def try_submit(self, board, cid: str, flag: str) -> tuple[str, bool]:
        """提交一个 flag。返回 (说明文本, 是否确认为正确)。

        说明文本供日志/看板展示；确认为 True 时调用方应把题目置为 solved。
        """
        with self._lock:
            cs = board.get(cid)
            if cs is None:
                return "unknown challenge", False
            if cs.status == "solved":
                return "already solved", True

            flag = flag.strip()
            if flag in cs.submitted_flags:
                return "duplicate flag", False

            if cs.wrong_submits >= self._max_wrong:
                return f"wrong-submit budget exhausted ({self._max_wrong})", False

            cooldown_idx = min(cs.wrong_submits, len(ESCALATING_COOLDOWNS) - 1)
            cooldown = ESCALATING_COOLDOWNS[cooldown_idx]
            if cooldown > 0:
                elapsed = time.monotonic() - cs.last_submit
                if elapsed < cooldown:
                    return f"cooldown {int(cooldown - elapsed)}s left", False

            cs.submitted_flags.append(flag)
            cs.last_submit = time.monotonic()
            board.save()

            try:
                res = self._submit_fn(cid, flag)
            except Exception as e:  # 网络/平台错误：不判错，保留下次机会
                cs.submitted_flags.pop()
                board.save()
                return f"submit error: {e}", False

            correct = self._is_correct(res)
            if correct:
                cs.transition("solved")
                board.save()
                return "correct", True
            cs.wrong_submits += 1
            board.save()
            return f"incorrect ({cs.wrong_submits}/{self._max_wrong})", False

    @staticmethod
    def _is_correct(res: Any) -> bool:
        if hasattr(res, "accepted"):  # SubmitResult（platform.py）
            return bool(res.accepted)
        if isinstance(res, dict):
            return res.get("correct") in (True, "true", 1) or res.get("success") in (True, "true", 1)
        return res is True
