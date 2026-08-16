#!/usr/bin/env python3
"""
dashboard.py —— 人机协同看板（Flask，只读 + 文件协议写回）

职责（对应官方考察"人类四价值"）：
- 过程监督：题目状态/耗时/尝试/提交记录一览 + 实时日志尾部 + 中文摘要
- 目标设定/策略判断：网页写 hints（写 hints/<cid>.md，编排器下一轮注入）
- 结果复核：人工确认候选 flag 提交（写 requests/confirm/<cid>.json）或开关复核模式
  （写 requests/verify/<cid>.toggle）

与编排器的通信全部走文件协议（看板不直接写 state.json，避免跨进程竞态）。

用法：python dashboard.py --workspace D:/ctf-agent/workspace --port 8088
React UI（D:/ctf-agent/ui 构建后）：自动 serve ui/dist 静态文件（/ui/ 路径）。
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from string import Template

from flask import Flask, jsonify, redirect, request, send_from_directory

app = Flask(__name__)
WORKSPACE: Path = Path("D:/ctf-agent/workspace")
UI_DIST: Path = Path(r"D:\ctf-agent\ui\dist")


def state(ws: Path | None = None) -> dict:
    f = (ws or WORKSPACE) / "state.json"
    if not f.exists():
        return {"challenges": []}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {"challenges": []}


def hints_text(cid: str, ws: Path | None = None) -> str:
    f = (ws or WORKSPACE) / "hints" / f"{cid}.md"
    return f.read_text(encoding="utf-8") if f.exists() else ""


def worker_log_tail(cid: str, lines: int = 40, line_cap: int = 4000,
                    ws: Path | None = None) -> str:
    wd = (ws or WORKSPACE) / "challenges" / cid
    if not wd.exists():
        return ""
    logs = sorted(wd.glob("worker_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        return ""
    text = logs[0].read_text(encoding="utf-8", errors="replace")
    out = []
    for line in text.splitlines()[-lines:]:
        out.append(line[:line_cap])  # 单行截断：完整事件在磁盘原文件里
    return "\n".join(out)


PAGE = Template("""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>CTF Agent 驾驶舱</title>
<meta http-equiv="refresh" content="15">
<style>
body{font-family:monospace;margin:16px;background:#111;color:#eee}
table{border-collapse:collapse;width:100%;margin:8px 0}
th,td{border:1px solid #444;padding:4px 8px;font-size:13px;text-align:left}
th{background:#222}
.solved{color:#4c4}
.solving{color:#cc4}
.dead{color:#c44}
.needs{color:#4cc}
form{display:inline}
textarea{width:100%;background:#1a1a1a;color:#eee;border:1px solid #444}
button{background:#333;color:#eee;border:1px solid #555;padding:2px 10px;cursor:pointer}
.pending{background:#332;padding:4px;margin:4px 0}
</style></head><body>
<h2>CTF Agent 驾驶舱 <small>(15s 自动刷新)</small></h2>
<table><tr><th>ID</th><th>题目</th><th>题型</th><th>状态</th><th>尝试</th><th>错交</th><th>待复核候选</th><th>操作</th></tr>
$rows
</table>
<hr><h3>写提示（hints，编排器下一轮自动注入）</h3>
<form method="post" action="/hint/$default_cid">
  <textarea name="text" rows="3" placeholder="给题目 $default_cid 的提示..."></textarea>
  <button>写入提示</button>
</form>
<hr><h3>日志尾部（<a href="/log/$default_cid">查看 worker 日志</a>）</h3>
<pre style="background:#161616;padding:8px;max-height:300px;overflow:auto">$log_tail</pre>
</body></html>""")


@app.get("/")
def index() -> str:
    # 新版 React UI 为默认首页（旧模板保留在 /legacy）
    return redirect("/ui/")


@app.get("/legacy")
def legacy_index() -> str:
    data = state()
    rows = []
    default_cid = ""
    for c in data.get("challenges", []):
        raw = c.get("raw") or {}
        if not default_cid:
            default_cid = c["cid"]
        status = c.get("status", "?")
        cls = {"solved": "solved", "dead": "dead", "needs_hint": "needs"}.get(status, "solving")
        pending = (c.get("triage") or {}).get("pending_flags") or []
        verify = "复核中" if c.get("verify_required") else "自动"
        pend_html = ""
        for fl in pending:
            pend_html += (f'<div class="pending"><b>{fl[:48]}</b> '
                          f'<form method="post" action="/confirm/{c["cid"]}">'
                          f'<input type="hidden" name="flag" value="{fl}">'
                          f'<button>确认提交</button></form></div>')
        actions = (f'<form method="post" action="/verify/{c["cid"]}">'
                   f'<button>{verify}</button></form>')
        rows.append(
            f'<tr><td>{c["cid"]}</td><td>{raw.get("name", "")}</td>'
            f'<td>{raw.get("category", "?")}</td>'
            f'<td class="{cls}">{status}</td>'
            f'<td>{len(c.get("attempts", []))}</td><td>{c.get("wrong_submits", 0)}</td>'
            f'<td>{pend_html}</td><td>{actions}</td></tr>')
    return PAGE.substitute(rows="".join(rows), default_cid=default_cid,
                           log_tail=worker_log_tail(default_cid) if default_cid else "")


@app.post("/hint/<cid>")
def hint(cid: str):
    text = (request.form.get("text") or "").strip()
    if text:
        f = WORKSPACE / "hints" / f"{cid}.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(f"\n## {stamp}\n{text}\n")
    return redirect("/")


@app.post("/confirm/<cid>")
def confirm(cid: str):
    flag = (request.form.get("flag") or "").strip()
    if flag:
        d = WORKSPACE / "requests" / "confirm"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{cid}.json").write_text(json.dumps({"flag": flag}), encoding="utf-8")
    return redirect("/")


@app.post("/verify/<cid>")
def verify(cid: str):
    d = WORKSPACE / "requests" / "verify"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{cid}.toggle").write_text("", encoding="utf-8")
    return redirect("/")


@app.get("/log/<cid>")
def log_view(cid: str):
    return f"<pre>{worker_log_tail(cid)}</pre>"


def _challenge_view(c: dict, log_dir: Path | None = None) -> dict:
    """state.json 条目 → 前端列表视图。log_dir 指向归档日志目录时用量从归档算。"""
    raw = c.get("raw") or {}
    attempts = c.get("attempts") or []
    elapsed = 0.0
    for a in attempts:
        elapsed += float(a.get("elapsed") or 0.0)
    cid = c.get("cid", "?")
    if log_dir is not None:
        import tracing
        usage = tracing.summarize_challenge(log_dir)
    else:
        usage = _usage_cached(cid)
    return {
        "cid": cid,
        "name": raw.get("name", ""),
        "category": raw.get("category", "?"),
        "status": c.get("status", "?"),
        "races": int(c.get("races") or len(attempts)),
        "attempts": len(attempts),
        "elapsed": round(elapsed, 1),
        "wrong_submits": int(c.get("wrong_submits") or 0),
        "verify_required": bool(c.get("verify_required")),
        "pending_flags": (c.get("triage") or {}).get("pending_flags") or [],
        "tokens": usage["totalTokens"],
        "cost": round(usage["cost"], 4),
        "digest_first": str(raw.get("description", "") or "")[:80],
        "connection": str(raw.get("connection") or ""),
        "liveness": str(raw.get("liveness") or "unknown"),
        "revived": bool(raw.get("revived")),
    }


# ---- 用量缓存（T14：worker 日志 mtime 变化才重算；按 workspace 分桶） ----
_USAGE_CACHE: dict[str, tuple[float, dict]] = {}


def _usage_cached(cid: str, ws: Path | None = None) -> dict:
    import tracing  # 同目录模块
    wd = (ws or WORKSPACE) / "challenges" / cid
    if not wd.exists():
        return {"totalTokens": 0, "cost": 0.0}
    mtimes = [p.stat().st_mtime for p in wd.glob("worker_*.log")]
    key = max(mtimes) if mtimes else 0.0
    cache_key = f"{ws or WORKSPACE}::{cid}"
    hit = _USAGE_CACHE.get(cache_key)
    if hit and hit[0] == key:
        return hit[1]
    u = tracing.summarize_challenge(wd)
    _USAGE_CACHE[cache_key] = (key, u)
    return u


@app.get("/api/usage/<cid>")
def api_usage(cid: str):
    session_id = (request.args.get("session") or "").strip() or None
    if session_id:
        import tracing
        u = tracing.summarize_challenge(
            session_archive.session_log_dir(WORKSPACE, session_id, cid))
    else:
        u = _usage_cached(cid)
    return jsonify({"cid": cid,
                    "tokens": u["totalTokens"], "cost": round(u["cost"], 4),
                    "input": u["input"], "output": u["output"], "workers": u["workers"]})


@app.get("/api/summary")
def api_summary():
    """全局态势（星图总览 hero 统计行用）。"""
    data = state()
    challenges = data.get("challenges", [])
    solved = solving = needs_hint = 0
    cost = 0.0
    tokens = 0
    for c in challenges:
        st = c.get("status", "?")
        if st == "solved":
            solved += 1
        elif st == "solving":
            solving += 1
        elif st == "needs_hint":
            needs_hint += 1
        u = _usage_cached(c.get("cid", "?"))
        cost += u["cost"]
        tokens += u["totalTokens"]
    return jsonify({"solved": solved, "solving": solving, "needs_hint": needs_hint,
                    "total": len(challenges), "cost": round(cost, 4), "tokens": tokens})


@app.get("/api/kali-status")
def api_kali_status():
    """Kali 健康（SSH 通道 ping + REST /health 双检）。"""
    import requests as _req
    ok = False
    try:
        r = _req.get("http://10.174.153.128:5000/health", timeout=4)
        ok = r.status_code == 200 and "healthy" in r.text
    except Exception:
        ok = False
    return jsonify({"ok": ok})


@app.get("/api/state")
def api_state():
    session_id = (request.args.get("session") or "").strip() or None
    if session_id:
        data = session_archive.session_state_raw(WORKSPACE, session_id)
        views = [_challenge_view(c, session_archive.session_log_dir(WORKSPACE, session_id, c.get("cid", "")))
                 for c in data.get("challenges", [])]
    else:
        data = state()
        views = [_challenge_view(c) for c in data.get("challenges", [])]
    return jsonify({"challenges": views})


@app.get("/api/state-raw")
def api_state_raw():
    session_id = (request.args.get("session") or "").strip() or None
    if session_id:
        return jsonify(session_archive.session_state_raw(WORKSPACE, session_id))
    return jsonify(state())


@app.get("/api/digest/<cid>")
def api_digest(cid: str):
    import digest  # 同目录模块
    session_id = (request.args.get("session") or "").strip() or None
    if session_id:
        return jsonify({"cid": cid, "digest": digest.digest(
            WORKSPACE, cid, session_archive.session_log_dir(WORKSPACE, session_id, cid))})
    return jsonify({"cid": cid, "digest": digest.digest(WORKSPACE, cid)})


@app.post("/api/hints/<cid>")
def api_hints(cid: str):
    body = request.get_json(silent=True) or {}
    text = str(body.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "msg": "empty text"}), 400
    f = WORKSPACE / "hints" / f"{cid}.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(f"\n## {stamp}\n{text}\n")
    return jsonify({"ok": True, "cid": cid})


@app.post("/api/confirm/<cid>")
def api_confirm(cid: str):
    body = request.get_json(silent=True) or {}
    flag = str(body.get("flag") or "").strip()
    if not flag:
        return jsonify({"ok": False, "msg": "empty flag"}), 400
    d = WORKSPACE / "requests" / "confirm"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{cid}.json").write_text(json.dumps({"flag": flag}), encoding="utf-8")
    return jsonify({"ok": True, "cid": cid})


@app.post("/api/verify/<cid>")
def api_verify(cid: str):
    d = WORKSPACE / "requests" / "verify"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{cid}.toggle").write_text("", encoding="utf-8")
    return jsonify({"ok": True, "cid": cid, "verify_required": True})


@app.get("/api/logs/<cid>")
def api_logs(cid: str):
    tail = int(request.args.get("tail", 200))
    tail = max(1, min(tail, 500))
    session_id = (request.args.get("session") or "").strip() or None
    if session_id:
        log_dir = session_archive.session_log_dir(WORKSPACE, session_id, cid)
        logs = sorted(log_dir.glob("worker_*.log"),
                      key=lambda p: p.stat().st_mtime, reverse=True) if log_dir.is_dir() else []
        text = ""
        if logs:
            raw = logs[0].read_text(encoding="utf-8", errors="replace")
            text = "\n".join(line[:2000] for line in raw.splitlines()[-tail:])
        return jsonify({"cid": cid, "text": text})
    return jsonify({"cid": cid, "text": worker_log_tail(cid, tail, line_cap=2000)})


@app.get("/api/transcript/<cid>")
def api_transcript(cid: str):
    """全程记录：worker 事件流 → 指令/思考/回复/工具（T-T1）。"""
    import tracing
    session_id = (request.args.get("session") or "").strip() or None
    if session_id:
        logs = tracing.worker_logs(session_archive.session_log_dir(WORKSPACE, session_id, cid))
    else:
        logs = tracing.worker_logs(WORKSPACE / "challenges" / cid)
    wi = max(0, min(int(request.args.get("worker", 0) or 0), len(logs) - 1)) if logs else 0
    limit = max(100, min(int(request.args.get("limit", 600) or 600), 2000))
    entries = tracing.parse_transcript(logs[wi], limit) if logs else []
    return jsonify({"cid": cid, "workers": len(logs),
                    "worker": wi, "worker_files": [p.name for p in logs],
                    "entries": entries})


@app.get("/api/board/<cid>")
def api_board(cid: str):
    session_id = (request.args.get("session") or "").strip() or None
    data = session_archive.session_state_raw(WORKSPACE, session_id) if session_id else state()
    for c in data.get("challenges", []):
        if c.get("cid") == cid:
            return jsonify({"cid": cid, "board": c.get("board") or {}})
    return jsonify({"cid": cid, "board": {}})


@app.get("/api/hints/<cid>")
def api_hints_get(cid: str):
    return jsonify({"cid": cid, "text": hints_text(cid)})


# ---- React UI 静态资源（生产模式：ui/dist 构建产物，base=/ui/）----
@app.get("/ui")
def ui_no_slash():
    return redirect("/ui/")


@app.get("/ui/")
def ui_index():
    return send_from_directory(UI_DIST, "index.html")


@app.get("/ui/<path:path>")
def ui_static(path: str):
    return send_from_directory(UI_DIST, path)


# ---- Benchmark 模块（T-B1：题库清单 + 跑分进程管理 + 同款挑战视图）----
import bench_admin  # noqa: E402
import session_archive  # noqa: E402


@app.get("/api/session/history")
def api_session_history():
    return jsonify({"sessions": session_archive.list_sessions(WORKSPACE)})


@app.post("/api/session/archive")
def api_session_archive():
    body = request.get_json(silent=True) or {}
    reason = str(body.get("reason") or "").strip()
    ok, sid = session_archive.archive_current(WORKSPACE, reason)
    if not ok:
        return jsonify({"ok": False, "msg": sid}), 400
    return jsonify({"ok": True, "session_id": sid})


@app.get("/api/bench/list")
def api_bench_list():
    return jsonify({"benchmarks": bench_admin.list_benchmarks()})


@app.post("/api/bench/run")
def api_bench_run():
    body = request.get_json(silent=True) or {}
    bid = str(body.get("id") or "")
    ok, msg = bench_admin.start(bid, body.get("filters") or {})
    return jsonify({"ok": ok, "msg": msg})


@app.post("/api/bench/stop")
def api_bench_stop():
    ok, msg = bench_admin.stop()
    return jsonify({"ok": ok, "msg": msg})


@app.post("/api/bench/resume/<run_id>")
def api_bench_resume(run_id: str):
    ok, msg = bench_admin.resume(run_id)
    return jsonify({"ok": ok, "msg": msg})


@app.get("/api/bench/audit/<run_id>")
def api_bench_audit(run_id: str):
    import audit
    return jsonify(audit.audit_run(run_id))


# ---- 统一配置中心（config/agent.json + config/secrets.json，Web UI「配置」页）----
@app.get("/api/config")
def api_config_get():
    import agent_config
    cfg = agent_config.load()
    return jsonify({
        "llm": cfg.get("llm"),
        "runtime": cfg.get("runtime"),
        "providers": [{"id": p.get("id"), "label": p.get("label"),
                       "base_url": p.get("base_url"), "models": p.get("models")}
                      for p in cfg.get("providers") or []],
        "keys": agent_config.secrets_status(),
    })


@app.post("/api/config")
def api_config_post():
    import agent_config
    body = request.get_json(silent=True) or {}
    errors = agent_config.validate(body)
    if errors:
        return jsonify({"ok": False, "msg": "；".join(errors)}), 400
    partial = {}
    if isinstance(body.get("llm"), dict):
        partial["llm"] = body["llm"]
    if isinstance(body.get("runtime"), dict):
        partial["runtime"] = body["runtime"]
    if isinstance(body.get("providers"), list):
        partial["providers"] = body["providers"]
    if partial:
        agent_config.save(partial)
    keys = body.get("api_keys")
    if isinstance(keys, dict) and keys:
        agent_config.set_secrets(keys)
    else:
        agent_config.apply_env()  # 至少刷新当前进程环境（供 digest 等使用）
    # providers/模型变化时，把新 provider 合并进 pi 模型注册表（worker 才认识新模型）
    sync_msg = ""
    try:
        synced, sync_msg = agent_config.sync_pi_models()
    except Exception as e:
        sync_msg = f"pi 模型注册表同步失败：{e}"
    msg = "配置已保存"
    if sync_msg:
        msg += f"；{sync_msg}"
    return jsonify({"ok": True, "msg": msg, "keys": agent_config.secrets_status()})


@app.get("/api/bench/status")
def api_bench_status():
    return jsonify(bench_admin.status())


def _bench_ws() -> Path:
    return bench_admin.BENCH_WS


def _bench_state_file(run_id: str | None) -> Path:
    if run_id:
        return bench_admin.RUNS_DIR / run_id / "state.json"
    return _bench_ws() / "state.json"


def _bench_log_dir(cid: str, run_id: str | None) -> Path:
    if run_id:
        return bench_admin.RUNS_DIR / run_id / "logs" / cid
    return _bench_ws() / "challenges" / cid


def _bench_state_raw(run_id: str | None) -> dict:
    f = _bench_state_file(run_id)
    if not f.exists():
        return {"challenges": []}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {"challenges": []}


@app.get("/api/bench/history")
def api_bench_history():
    return jsonify({"runs": bench_admin.history()})


@app.get("/api/bench/state")
def api_bench_state():
    run_id = (request.args.get("run") or "").strip() or None
    data = _bench_state_raw(run_id)
    return jsonify({"challenges": [_challenge_view_ws(c, _bench_ws())
                                  for c in data.get("challenges", [])]})


def _challenge_view_ws(c: dict, ws: Path) -> dict:
    raw = c.get("raw") or {}
    attempts = c.get("attempts") or []
    elapsed = 0.0
    for a in attempts:
        elapsed += float(a.get("elapsed") or 0.0)
    cid = c.get("cid", "?")
    usage = _usage_cached(cid, ws)
    return {
        "cid": cid,
        "name": raw.get("name", ""),
        "category": raw.get("category", "?"),
        "status": c.get("status", "?"),
        "races": int(c.get("races") or len(attempts)),
        "attempts": len(attempts),
        "elapsed": round(elapsed, 1),
        "wrong_submits": int(c.get("wrong_submits") or 0),
        "verify_required": bool(c.get("verify_required")),
        "pending_flags": (c.get("triage") or {}).get("pending_flags") or [],
        "tokens": usage["totalTokens"],
        "cost": round(usage["cost"], 4),
        "digest_first": str(raw.get("description", "") or "")[:80],
        "connection": str(raw.get("connection") or ""),
        "liveness": str(raw.get("liveness") or "unknown"),
        "revived": bool(raw.get("revived")),
    }


@app.get("/api/bench/digest/<cid>")
def api_bench_digest(cid: str):
    import digest
    run_id = (request.args.get("run") or "").strip() or None
    return jsonify({"cid": cid,
                    "digest": digest.digest(_bench_ws(), cid, _bench_log_dir(cid, run_id))})


@app.get("/api/bench/logs/<cid>")
def api_bench_logs(cid: str):
    tail = int(request.args.get("tail", 200))
    tail = max(1, min(tail, 500))
    run_id = (request.args.get("run") or "").strip() or None
    log_dir = _bench_log_dir(cid, run_id)
    logs = sorted(log_dir.glob("worker_*.log"),
                  key=lambda p: p.stat().st_mtime, reverse=True) if log_dir.is_dir() else []
    text = ""
    if logs:
        raw = logs[0].read_text(encoding="utf-8", errors="replace")
        text = "\n".join(line[:2000] for line in raw.splitlines()[-tail:])
    return jsonify({"cid": cid, "text": text})


@app.get("/api/bench/transcript/<cid>")
def api_bench_transcript(cid: str):
    import tracing
    run_id = (request.args.get("run") or "").strip() or None
    logs = tracing.worker_logs(_bench_log_dir(cid, run_id))
    wi = max(0, min(int(request.args.get("worker", 0) or 0), len(logs) - 1)) if logs else 0
    limit = max(100, min(int(request.args.get("limit", 600) or 600), 2000))
    entries = tracing.parse_transcript(logs[wi], limit) if logs else []
    return jsonify({"cid": cid, "workers": len(logs),
                    "worker": wi, "worker_files": [p.name for p in logs],
                    "entries": entries})


@app.get("/api/bench/board/<cid>")
def api_bench_board(cid: str):
    run_id = (request.args.get("run") or "").strip() or None
    data = _bench_state_raw(run_id)
    for c in data.get("challenges", []):
        if c.get("cid") == cid:
            return jsonify({"cid": cid, "board": c.get("board") or {}})
    return jsonify({"cid": cid, "board": {}})


@app.post("/api/bench/hints/<cid>")
def api_bench_hints(cid: str):
    body = request.get_json(silent=True) or {}
    text = str(body.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "msg": "empty text"}), 400
    f = _bench_ws() / "hints" / f"{cid}.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(f"\n## {stamp}\n{text}\n")
    return jsonify({"ok": True, "cid": cid})


@app.post("/api/bench/confirm/<cid>")
def api_bench_confirm(cid: str):
    body = request.get_json(silent=True) or {}
    flag = str(body.get("flag") or "").strip()
    if not flag:
        return jsonify({"ok": False, "msg": "empty flag"}), 400
    d = _bench_ws() / "requests" / "confirm"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{cid}.json").write_text(json.dumps({"flag": flag}), encoding="utf-8")
    return jsonify({"ok": True, "cid": cid})


@app.post("/api/bench/verify/<cid>")
def api_bench_verify(cid: str):
    d = _bench_ws() / "requests" / "verify"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{cid}.toggle").write_text("", encoding="utf-8")
    return jsonify({"ok": True, "cid": cid})


@app.get("/api/bench/usage/<cid>")
def api_bench_usage(cid: str):
    import tracing
    run_id = (request.args.get("run") or "").strip() or None
    if run_id:
        u = tracing.summarize_challenge(_bench_log_dir(cid, run_id))
    else:
        u = _usage_cached(cid, _bench_ws())
    return jsonify({"cid": cid, "tokens": u["totalTokens"], "cost": round(u["cost"], 4)})


def main(argv: list[str] | None = None) -> int:
    global WORKSPACE
    p = argparse.ArgumentParser(prog="dashboard")
    p.add_argument("--workspace", default="D:/ctf-agent/workspace")
    p.add_argument("--port", type=int, default=8088)
    args = p.parse_args(argv)
    WORKSPACE = Path(args.workspace)
    app.run(host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
