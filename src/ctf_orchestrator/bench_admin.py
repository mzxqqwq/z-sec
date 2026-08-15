"""bench_admin.py —— Benchmark 模块后端支持（T-B1，2026-08-16）。

职责：
- 题库清单：扫描本地 benchmarks 目录产出元数据（题数/分类/真值数）；
- 跑分进程管理：spawn eval_run.py 子进程（独立进程组）、日志落盘、状态查询、停止。

跑分 workspace：D:/ctf-agent/eval-workspace-bench（每次开跑前清 state.json，成绩单干净）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

BENCH_ROOT = Path(r"D:\ctf-agent\benchmarks")
BENCH_WS = Path(r"D:\ctf-agent\eval-workspace-bench")
RUN_LOG = BENCH_WS / "bench-run.log"
EVAL_RUN = Path(r"D:\ctf-agent\src\ctf_orchestrator\eval_run.py")
L2_CONFIG = Path(r"D:\ctf-agent\src\ctf_orchestrator\l2-config.json")

CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

# 题库定义：id → 名称/描述/eval_run 参数
BENCH_DEFS: dict[str, dict[str, Any]] = {
    "ctftiny": {
        "name": "CTFTiny（CSAW）",
        "desc": "CSAW 2021-22 真题切片，静态为主，含官方难度分级",
        "meta": "ctftiny.json",
        "args": ["--platform", "ctftiny"],
    },
    "nyu": {
        "name": "NYU CTF-Bench（全量）",
        "desc": "CTFTiny 的全量上游：test 200 + dev 57，2013-2023",
        "meta": "test_dataset.json",
        "args": ["--platform", "ctftiny",
                 "--bench-root", "D:/ctf-agent/benchmarks/nyu-ctf-bench",
                 "--bench-meta", "test_dataset.json,development_dataset.json"],
    },
    "cybench": {
        "name": "Cybench",
        "desc": "40 题专业级（4 赛事）；静态 19 题可跑，服务题待容器",
        "meta": None,
        "args": ["--platform", "cybench"],
    },
    "dasctf2025": {
        "name": "DASCTF 2025 真题",
        "desc": "13 题真题（7 题有真值），赛味最正",
        "meta": None,
        "args": ["--platform", "dasctf2025"],
    },
}


def _count_challenges(meta_path: Path) -> dict[str, Any]:
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"challenges": 0, "categories": {}}
    if isinstance(data, dict):
        cats: dict[str, int] = {}
        for entry in data.values():
            if not isinstance(entry, dict):
                continue
            raw_cat = str(entry.get("category") or "?").lower()
            cat = {"cry": "crypto", "for": "misc", "msc": "misc", "forensics": "misc",
                   "re": "rev"}.get(raw_cat, raw_cat)
            cats[cat] = cats.get(cat, 0) + 1
        return {"challenges": len(data), "categories": cats}
    return {"challenges": 0, "categories": {}}


def list_benchmarks() -> list[dict[str, Any]]:
    out = []
    for bid, d in BENCH_DEFS.items():
        entry: dict[str, Any] = {"id": bid, "name": d["name"], "desc": d["desc"],
                                 "challenges": 0, "categories": {}, "truth": None}
        root = None
        if bid == "ctftiny":
            root = BENCH_ROOT / "ctftiny"
        elif bid == "nyu":
            root = BENCH_ROOT / "nyu-ctf-bench"
        elif bid == "cybench":
            root = BENCH_ROOT / "cybench"
        elif bid == "dasctf2025":
            manifest = BENCH_ROOT / "dasctf-2025-manifest.json"
            try:
                rows = json.loads(manifest.read_text(encoding="utf-8"))
                entry["challenges"] = len(rows)
                entry["truth"] = sum(1 for r in rows if r.get("flag") not in (None, "unknown"))
                cats: dict[str, int] = {}
                for r in rows:
                    c = str(r.get("category") or "?").lower()
                    cats[c] = cats.get(c, 0) + 1
                entry["categories"] = cats
            except (OSError, json.JSONDecodeError):
                entry["challenges"] = 0
            out.append(entry)
            continue
        if root is not None and root.is_dir():
            meta_name = d.get("meta")
            if meta_name:
                entry.update(_count_challenges(root / meta_name))
            else:
                metas = list(root.rglob("metadata.json"))
                entry["challenges"] = len(metas)
                cats = {}
                for m in metas[:500]:
                    try:
                        md = json.loads(m.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    cats_list = md.get("categories") or []
                    c = str(cats_list[0] if cats_list else md.get("category") or "?") if isinstance(md, dict) else "?"
                    cats[c] = cats.get(c, 0) + 1
                entry["categories"] = cats
            entry["truth"] = entry["challenges"]
        out.append(entry)
    return out


# ---------- 跑分进程管理 ----------
_run_lock = threading.Lock()
_run: dict[str, Any] = {
    "proc": None, "platform": None, "started_at": 0.0, "cmd": [], "status": "idle",
}


def _status_locked() -> dict[str, Any]:
    proc: Optional[subprocess.Popen] = _run.get("proc")
    if proc is None:
        return {"status": "idle", "platform": _run.get("platform"),
                "elapsed": 0, "log_tail": ""}
    running = proc.poll() is None
    status = "running" if running else "done"
    elapsed = time.time() - float(_run.get("started_at") or time.time())
    if not running and proc.returncode != 0:
        status = "failed"
    return {"status": status, "platform": _run.get("platform"), "elapsed": round(elapsed, 1),
            "pid": proc.pid, "exit_code": proc.returncode,
            "log_tail": _log_tail()}


def _log_tail(lines: int = 40) -> str:
    if not RUN_LOG.exists():
        return ""
    text = RUN_LOG.read_text(encoding="utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])


def status() -> dict[str, Any]:
    with _run_lock:
        return _status_locked()


def start(bench_id: str, filters: dict[str, Any] | None = None) -> tuple[bool, str]:
    """启动一次跑分。filters: difficulty/categories/only/exclude（逗号分隔字符串）。"""
    with _run_lock:
        if _run.get("proc") is not None and _run["proc"].poll() is None:
            return False, "已有跑分在运行，先停止"
        d = BENCH_DEFS.get(bench_id)
        if d is None:
            return False, f"未知题库 {bench_id}"
        filters = filters or {}
        cmd = [sys.executable, "-X", "utf8", str(EVAL_RUN),
               *d["args"], "--workspace", str(BENCH_WS),
               "--config", str(L2_CONFIG)]
        if filters.get("difficulty"):
            cmd += ["--difficulty", str(filters["difficulty"])]
        if filters.get("categories"):
            cmd += ["--categories", str(filters["categories"])]
        if filters.get("only"):
            cmd += ["--only", str(filters["only"])]
        if filters.get("exclude"):
            cmd += ["--exclude", str(filters["exclude"])]
        # 干净跑分：清黑板（保留 challenges 日志目录）
        BENCH_WS.mkdir(parents=True, exist_ok=True)
        for junk in ("state.json", "eval-result.json"):
            p = BENCH_WS / junk
            if p.exists():
                p.unlink()
        log_fh = open(RUN_LOG, "w", encoding="utf-8", errors="replace")
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(Path(r"D:\ctf-agent")),
                stdout=log_fh, stderr=subprocess.STDOUT,
                creationflags=CREATE_NEW_PROCESS_GROUP,
            )
        except Exception as e:
            log_fh.close()
            return False, f"启动失败: {e}"
        _run.update({"proc": proc, "platform": bench_id,
                     "started_at": time.time(), "cmd": cmd})
        return True, f"已启动 {d['name']} 跑分 (pid={proc.pid})"


def stop() -> tuple[bool, str]:
    with _run_lock:
        proc: Optional[subprocess.Popen] = _run.get("proc")
        if proc is None or proc.poll() is not None:
            _run["proc"] = None
            return True, "无运行中的跑分"
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=30)
        except Exception as e:
            return False, f"停止失败: {e}"
        _run["proc"] = None
        return True, "已停止"
