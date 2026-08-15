"""状态机 v2.1 —— 每道题的生命周期与黑板持久化。

状态流转：
    new ──triage(轻量分类+难度)──▶ queued ──planning(LLM 拆解)──▶ solving
    solving ──竞速出 flag──▶ solved
    solving ──预算/僵局耗尽──▶ dead
    solving ──人工介入──▶ needs_hint ──人工写 hints 后──▶ queued

verify（人工复核）是 solving→solved 之间可选的关卡，由配置或人工开启。
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STATUS_NEW = "new"
STATUS_QUEUED = "queued"
STATUS_SOLVING = "solving"
STATUS_SOLVED = "solved"
STATUS_DEAD = "dead"
STATUS_NEEDS_HINT = "needs_hint"

ACTIVE_STATUSES = (STATUS_NEW, STATUS_QUEUED, STATUS_SOLVING, STATUS_NEEDS_HINT)


@dataclass
class ChallengeState:
    cid: str
    raw: dict[str, Any]
    status: str = STATUS_NEW
    # triage 结果（P2 完整实现；P0 用 raw 的 category/points 做轻量版）
    triage: dict[str, Any] = field(default_factory=dict)
    # planning 产物：LLM 拆解出的解题计划，注入 worker 提示词
    plan: str | None = None
    # 人工复核开关：True 时 flag 提交前必须人工确认（看板操作）
    verify_required: bool = False
    attempts: list[dict[str, Any]] = field(default_factory=list)
    wrong_submits: int = 0
    # 完整竞速轮次计数（预算依据；attempts 含僵局击杀等审计记录，不计入预算）
    races: int = 0
    # 提交纪律：去重 + 递增冷却
    submitted_flags: list[str] = field(default_factory=list)
    last_submit: float = 0.0
    # 时间预算（P1 启用；0 = 不限制）
    deadline: float = 0.0
    # hint 版本计数（P2 结构化 hint 用）
    hints_seen: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cid": self.cid, "raw": self.raw, "status": self.status,
            "triage": self.triage, "plan": self.plan,
            "verify_required": self.verify_required,
            "attempts": self.attempts, "wrong_submits": self.wrong_submits,
            "races": self.races,
            "submitted_flags": self.submitted_flags,
            "last_submit": self.last_submit, "deadline": self.deadline,
            "hints_seen": self.hints_seen,
        }

    @classmethod
    def from_dict(cls, item: dict[str, Any]) -> "ChallengeState":
        cs = cls(item["cid"], item.get("raw", {}), item.get("status", STATUS_NEW))
        cs.triage = item.get("triage", {})
        cs.plan = item.get("plan")
        cs.verify_required = bool(item.get("verify_required", False))
        cs.attempts = item.get("attempts", [])
        cs.wrong_submits = int(item.get("wrong_submits", 0))
        cs.races = int(item.get("races", len(cs.attempts)))
        cs.submitted_flags = item.get("submitted_flags", [])
        cs.last_submit = float(item.get("last_submit", 0.0))
        cs.deadline = float(item.get("deadline", 0.0))
        cs.hints_seen = int(item.get("hints_seen", 0))
        return cs

    # ---- 状态迁移（唯一入口，带合法性断言）----
    def transition(self, new_status: str) -> None:
        legal = {
            STATUS_NEW: (STATUS_QUEUED, STATUS_DEAD),
            STATUS_QUEUED: (STATUS_SOLVING, STATUS_DEAD),
            STATUS_SOLVING: (STATUS_SOLVED, STATUS_DEAD, STATUS_NEEDS_HINT, STATUS_QUEUED),
            STATUS_NEEDS_HINT: (STATUS_QUEUED, STATUS_DEAD),
            STATUS_SOLVED: (),
            STATUS_DEAD: (),
        }
        assert new_status in legal.get(self.status, ()), \
            f"illegal transition {self.status} -> {new_status} (cid={self.cid})"
        self.status = new_status


class Board:
    """黑板：state.json 的读写，进程内加锁（单编排器进程）。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.challenges: dict[str, ChallengeState] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for item in data.get("challenges", []):
            cs = ChallengeState.from_dict(item)
            self.challenges[cs.cid] = cs

    def save(self) -> None:
        with self._lock:
            self.path.write_text(
                json.dumps({"challenges": [c.to_dict() for c in self.challenges.values()]},
                           ensure_ascii=False, indent=2),
                encoding="utf-8")

    def get(self, cid: str) -> ChallengeState | None:
        return self.challenges.get(cid)

    def put(self, cs: ChallengeState) -> None:
        self.challenges[cs.cid] = cs
        self.save()

    def open_cids(self) -> list[str]:
        return [cid for cid, cs in self.challenges.items()
                if cs.status in (STATUS_NEW, STATUS_QUEUED, STATUS_NEEDS_HINT)]
