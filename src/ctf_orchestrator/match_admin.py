"""match_admin.py —— 比赛模式编排器进程管理（spawn ctf_orchestrator --platform dasctf）。

与 bench_admin 的区别：比赛是真实平台（dasctf），LLM 走大模型网关（本地代理 8787），
状态保留在 workspace-match（重启不丢，已 solved 不重跑）。提供 start/status/stop。
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

MATCH_WS = Path(r"D:\ctf-agent\workspace-match")
RUN_LOG = MATCH_WS / "match-run.log"
ORCH = Path(r"D:\ctf-agent\src\ctf_orchestrator\ctf_orchestrator.py")
MATCH_CFG = Path(r"D:\ctf-agent\config\match.json")

CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

_lock = threading.Lock()
_run: dict[str, Any] = {"proc": None, "started_at": 0.0, "cmd": []}


def _alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except Exception:
        return False


def _pid_file() -> int:
    p = MATCH_WS / "run.pid"
    if not p.exists():
        return 0
    try:
        return int(p.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def _effective_pid() -> int:
    proc = _run.get("proc")
    if proc is not None and proc.poll() is None:
        return proc.pid
    return _pid_file()  # ctf_orchestrator main 会写 workspace/run.pid


def _log_tail(lines: int = 40) -> str:
    if not RUN_LOG.exists():
        return ""
    text = RUN_LOG.read_text(encoding="utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])


def status() -> dict[str, Any]:
    with _lock:
        proc: Optional[subprocess.Popen] = _run.get("proc")
        if proc is not None:
            running = proc.poll() is None
            if running:
                return {"status": "running",
                        "elapsed": round(time.time() - float(_run.get("started_at") or time.time()), 1),
                        "pid": proc.pid, "exit_code": None, "log_tail": _log_tail()}
            _run["proc"] = None
            return {"status": "done" if proc.returncode == 0 else "failed",
                    "elapsed": round(time.time() - float(_run.get("started_at") or time.time()), 1),
                    "pid": proc.pid, "exit_code": proc.returncode, "log_tail": _log_tail()}
        pid = _pid_file()
        if pid and _alive(pid):
            return {"status": "running", "elapsed": 0, "pid": pid,
                    "exit_code": None, "log_tail": _log_tail()}
        return {"status": "idle", "elapsed": 0, "pid": None,
                "exit_code": None, "log_tail": _log_tail()}


def start(loop_sec: int = 10800) -> tuple[bool, str]:
    with _lock:
        if _effective_pid():
            return False, "比赛 agent 已在运行（先停止再启动）"
        MATCH_WS.mkdir(parents=True, exist_ok=True)
        (MATCH_WS / "run.pid").unlink(missing_ok=True)
        cmd = [sys.executable, "-u", "-X", "utf8", str(ORCH),
               "--platform", "dasctf",
               "--model-config", str(MATCH_CFG),
               "--workspace", str(MATCH_WS),
               "--loop", str(max(0, int(loop_sec)))]
        log_fh = open(RUN_LOG, "w", encoding="utf-8", errors="replace")
        env = dict(__import__("os").environ)
        # 比赛模式：LLM 走本地网关代理（agent.json 保持干净默认，benchmark 直连不受影响）
        env["DASCTF_LLM_BASE_URL"] = "http://127.0.0.1:8787"
        try:
            proc = subprocess.Popen(cmd, cwd=str(Path(r"D:\ctf-agent")),
                                    stdout=log_fh, stderr=subprocess.STDOUT,
                                    creationflags=CREATE_NEW_PROCESS_GROUP, env=env)
        except Exception as e:
            log_fh.close()
            return False, f"启动失败: {e}"
        _run.update({"proc": proc, "started_at": time.time(), "cmd": cmd})
        return True, f"比赛 agent 已启动（pid={proc.pid}，loop={int(loop_sec)}s；看板在「比赛看板」页）"


def stop() -> tuple[bool, str]:
    with _lock:
        pid = _effective_pid()
        if not pid:
            _run["proc"] = None
            return True, "无运行中的比赛 agent"
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=30)
        except Exception as e:
            return False, f"停止失败: {e}"
        try:
            (MATCH_WS / "run.pid").unlink(missing_ok=True)
        except OSError:
            pass
        _run["proc"] = None
        return True, "已停止"


if __name__ == "__main__":
    import json
    if len(sys.argv) > 1 and sys.argv[1] == "start":
        ok, msg = start(int(sys.argv[2]) if len(sys.argv) > 2 else 10800)
        print(json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False))
    elif len(sys.argv) > 1 and sys.argv[1] == "stop":
        ok, msg = stop()
        print(json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False))
    else:
        print(json.dumps(status(), ensure_ascii=False))
