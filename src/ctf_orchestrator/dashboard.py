#!/usr/bin/env python3
"""
dashboard.py —— 人机协同看板（Flask，只读 + 文件协议写回）

职责（对应官方考察"人类四价值"）：
- 过程监督：题目状态/耗时/尝试/提交记录一览 + 实时日志尾部
- 目标设定/策略判断：网页写 hints（写 hints/<cid>.md，编排器下一轮注入）
- 结果复核：人工确认候选 flag 提交（写 requests/confirm/<cid>.json）或开关复核模式
  （写 requests/verify/<cid>.toggle）

与编排器的通信全部走文件协议（看板不直接写 state.json，避免跨进程竞态）。

用法：python dashboard.py --workspace D:/ctf-agent/workspace --port 8088
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from string import Template

from flask import Flask, jsonify, redirect, request

app = Flask(__name__)
WORKSPACE: Path = Path("D:/ctf-agent/workspace")


def state() -> dict:
    f = WORKSPACE / "state.json"
    if not f.exists():
        return {"challenges": []}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {"challenges": []}


def hints_text(cid: str) -> str:
    f = WORKSPACE / "hints" / f"{cid}.md"
    return f.read_text(encoding="utf-8") if f.exists() else ""


def worker_log_tail(cid: str, lines: int = 40) -> str:
    wd = WORKSPACE / "challenges" / cid
    if not wd.exists():
        return ""
    logs = sorted(wd.glob("worker_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        return ""
    text = logs[0].read_text(encoding="utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])


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


@app.get("/api/state")
def api_state():
    return jsonify(state())


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
