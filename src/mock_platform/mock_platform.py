#!/usr/bin/env python3
"""
mock_platform.py —— 本地 DASCTF 平台模拟（演练用）

按 dasctf_client 的端点约定实现一个最小 Jeopardy 平台：
  GET  /api/config                    -> {"captcha": false, "apiPublicKey": null}
  POST /api/login                     -> 任意账号密码通过
  GET  /api/challenges                -> 题目列表
  GET  /api/challenges/<id>           -> 题目详情
  GET  /api/challenges/<id>/attachment-> 附件（内含 flag 的文件）
  POST /api/challenges/<id>/submit    -> 校验 flag，记录提交

用法：python mock_platform.py --port 7777
"""
import argparse
import io
from pathlib import Path
from flask import Flask, jsonify, request, send_file

FLAGS = {
    "1": "DASCTF{mock_misc_flag_001}",
    "2": "DASCTF{mock_crypto_flag_002}",
    "3": "DASCTF{mock_web_flag_003}",
    "4": "DASCTF{mock_pwn_flag_004}",
    "5": "DASCTF{rsa_small_e_2026}",
    "6": "DASCTF{png_trailing_2026}",
    "7": "DASCTF{web_ssti_flag_007}",
    "8": "DASCTF{pwn_bof_flag_008}",
}

# M2 演练集：真实题型（RSA/png/SSTI/bof），1-4 题已注释（M0/M1 演练用）
CHALLENGES = [
    {
        "id": "5", "name": "crypto-rsa-小指数", "category": "crypto",
        "description": "附件 crypto.txt 给出 RSA 的 n/e/c，明文是 flag，请解密提交。",
        "points": 300, "solved": 0,
    },
    {
        "id": "6", "name": "misc-png-藏数据", "category": "misc",
        "description": "附件是一张 PNG 图片，flag 被藏在图片数据里（不在画面中）。",
        "points": 200, "solved": 0,
    },
    {
        "id": "7", "name": "web-ssti-模板注入", "category": "web",
        "description": "服务运行在 http://127.0.0.1:8300/ ，路由 /hello/<名字> 存在模板注入漏洞，flag 在 /root/ctf/web_flag.txt。请利用漏洞读取 flag。",
        "points": 400, "solved": 0,
    },
    {
        "id": "8", "name": "pwn-bof-栈溢出", "category": "pwn",
        "description": "附件是本地二进制 pwn_bof：栈溢出，存在 win 函数（会打印 /root/ctf/pwn_flag.txt）。用 pwntools 打本地进程拿 flag。",
        "points": 500, "solved": 0,
    },
]

app = Flask(__name__)
SUBMISSIONS = []


@app.get("/api/config")
def config():
    return jsonify({"captcha": False, "apiPublicKey": None, "encrypt": False})


@app.post("/api/login")
def login():
    return jsonify({"code": 0, "msg": "ok", "token": "mock-session"})


@app.get("/api/challenges")
def challenges():
    return jsonify({"challenges": CHALLENGES})


@app.get("/api/challenges/<cid>")
def detail(cid):
    for ch in CHALLENGES:
        if ch["id"] == cid:
            return jsonify(ch)
    return jsonify({"error": "not found"}), 404


@app.get("/api/challenges/<cid>/attachment")
def attachment(cid):
    if cid == "1":
        data = f"提示：flag 藏在下面一行\n{FLAGS['1']}\n".encode()
        return send_file(io.BytesIO(data), mimetype="text/plain", as_attachment=True,
                         download_name=f"challenge_{cid}.txt")
    files_dir = Path(__file__).resolve().parent / "files" / cid
    if files_dir.exists():
        files = [f for f in files_dir.iterdir() if f.is_file()]
        if files:
            f = files[0]
            return send_file(f, as_attachment=True, download_name=f.name)
    return jsonify({"error": "no attachment"}), 404


@app.post("/api/challenges/<cid>/submit")
def submit(cid):
    body = request.get_json(force=True, silent=True) or {}
    flag = (body.get("flag") or "").strip()
    expected = FLAGS.get(cid)
    correct = bool(expected and flag == expected)
    SUBMISSIONS.append({"challenge": cid, "flag": flag[:24], "correct": correct})
    print(f"[submit] cid={cid} flag={flag[:24]}... correct={correct}")
    return jsonify({"correct": correct, "msg": "right" if correct else "wrong"})


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=7777)
    args = p.parse_args()
    app.run(host="127.0.0.1", port=args.port)
