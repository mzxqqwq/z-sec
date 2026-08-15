"""session_archive.py —— 比赛/演练场次持久化（T-P2，2026-08-16）。

把主 workspace 的当前场次归档到 workspace/sessions/<id>/：
  - state.json（黑板，复制——当前场继续可看）
  - challenges/<cid>/worker_*.log（移动——新场次不覆盖旧日志）
  - meta.json（归档时间/原因/摘要）
新场次 = 归档后清空 state.json 与 worker 日志，附件保留（可复用）。
看板重启/编排器重启都不丢；历史场次只读回看。
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any


def _sessions_dir(ws: Path) -> Path:
    return ws / "sessions"


def _session_dir(ws: Path, session_id: str) -> Path:
    return _sessions_dir(ws) / session_id


def archive_current(ws: Path, reason: str = "") -> tuple[bool, str]:
    """归档当前场次（黑板 + worker 日志），返回 (ok, session_id 或错误)。"""
    state_file = ws / "state.json"
    if not state_file.exists():
        return False, "当前没有黑板数据（state.json 不存在），无需归档"
    session_id = time.strftime("%Y%m%d-%H%M%S")
    sdir = _session_dir(ws, session_id)
    sdir.mkdir(parents=True, exist_ok=True)
    # 黑板复制（当前场继续保留可看；归档版保证后续不被改）
    shutil.copy2(state_file, sdir / "state.json")
    # worker 日志移动
    logs_moved = 0
    ch_dir = ws / "challenges"
    if ch_dir.is_dir():
        for cdir in ch_dir.iterdir():
            if not cdir.is_dir():
                continue
            for log in cdir.glob("worker_*.log"):
                dst = sdir / "logs" / cdir.name
                dst.mkdir(parents=True, exist_ok=True)
                try:
                    log.replace(dst / log.name)
                    logs_moved += 1
                except OSError:
                    pass
    # 摘要
    summary = {"challenges": 0, "solved": 0}
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        chs = data.get("challenges", [])
        summary = {"challenges": len(chs),
                   "solved": sum(1 for c in chs if c.get("status") == "solved")}
    except Exception:
        pass
    meta = {"id": session_id, "archived_at": time.time(), "reason": reason,
            "summary": summary, "logs_moved": logs_moved}
    (sdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    # 清当前场（黑板删除；worker 日志已移走；附件保留）
    state_file.unlink(missing_ok=True)
    (ws / "eval-result.json").unlink(missing_ok=True)  # 主 workspace 一般无，防御
    return True, session_id


def list_sessions(ws: Path) -> list[dict[str, Any]]:
    """历史场次（新→旧）。"""
    out: list[dict[str, Any]] = []
    sdir = _sessions_dir(ws)
    if not sdir.is_dir():
        return out
    for d in sdir.iterdir():
        if not d.is_dir():
            continue
        meta_path = d / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        out.append({**meta, "id": d.name})
    return sorted(out, key=lambda m: m.get("archived_at") or 0, reverse=True)


def session_state_file(ws: Path, session_id: str) -> Path:
    return _session_dir(ws, session_id) / "state.json"


def session_log_dir(ws: Path, session_id: str, cid: str) -> Path:
    return _session_dir(ws, session_id) / "logs" / cid


def session_state_raw(ws: Path, session_id: str) -> dict[str, Any]:
    f = session_state_file(ws, session_id)
    if not f.exists():
        return {"challenges": []}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {"challenges": []}
