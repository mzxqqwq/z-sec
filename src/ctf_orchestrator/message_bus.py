"""Message Bus（verialabs message_bus.py 54 行移植，落盘版）。

单题内 append-only 共享 findings + 每 worker 游标（存文件）；
check() 只回传"游标之后且非本人"的条目——只读他人新发现、绝不回声。
worker 侧由 kali.ts 的 check_findings 工具消费（进程内存游标 + 文件 findings）。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

MAX_FINDINGS = 200
_lock = threading.Lock()  # 单编排器进程内串行写


class ChallengeMessageBus:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"findings": [], "cursors": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"findings": [], "cursors": {}}
        if not isinstance(data, dict):
            return {"findings": [], "cursors": {}}
        return data

    def _save(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def post(self, model: str, content: str) -> None:
        """编排器在 worker 完成后投递发现摘要。"""
        content = (content or "").strip()
        if not content:
            return
        with _lock:
            data = self._load()
            findings: list[dict[str, Any]] = data.get("findings", [])
            findings.append({"model": model, "content": content[:500], "ts": time.time()})
            if len(findings) > MAX_FINDINGS:
                trim = len(findings) - MAX_FINDINGS
                findings = findings[trim:]
                cursors = data.get("cursors", {})
                data["cursors"] = {k: max(0, int(v) - trim) for k, v in cursors.items()}
            data["findings"] = findings
            self._save(data)

    def check(self, model: str) -> list[dict[str, Any]]:
        """worker 侧读取：游标之后且非本人的未读。"""
        with _lock:
            data = self._load()
            findings: list[dict[str, Any]] = data.get("findings", [])
            cursors: dict[str, Any] = data.get("cursors", {})
            cursor = int(cursors.get(model, 0) or 0)
            unread = [f for f in findings[cursor:] if f.get("model") != model]
            cursors[model] = len(findings)
            data["cursors"] = cursors
            self._save(data)
        return unread

    @staticmethod
    def format_unread(findings: list[dict[str, Any]]) -> str:
        if not findings:
            return ""
        parts = [f"[{f.get('model','?')}] {f.get('content','')}" for f in findings]
        return "**Findings from other agents:**\n\n" + "\n\n".join(parts)
