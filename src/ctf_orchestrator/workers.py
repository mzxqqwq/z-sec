"""Worker 进程管理与输出解析（Windows 版，对齐 Cairn local_process 语义）。

- 独立进程组（CREATE_NEW_PROCESS_GROUP）→ 组杀（taskkill /T /F），杜绝孤儿
- 输出落盘日志文件（避免管道阻塞）
- json 模式输出解析：从 pi 事件流提取最终文本（Cairn extract_response_text 移植）
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import requests

# ---- flag 抽取（多正则 + 去重保序）----
# 教训（CTFTiny L1 实测）：① csawctf 前缀的 flag 会被 ctf{ 模式截断 → 词边界必须防内嵌匹配
# ② flag 内容可含空格（showdown 题）→ 字符类放宽 ③ 描述里的占位符（flag{path} 等）要过滤
FLAG_PATTERNS: list[re.Pattern] = [
    re.compile(rb"(?<![a-zA-Z0-9_])flag\{[^\r\n{}]{4,120}\}", re.I),
    re.compile(rb"(?<![a-zA-Z0-9_])csawctf\{[^\r\n{}]{4,120}\}", re.I),
    re.compile(rb"(?<![a-zA-Z0-9_])dasctf\{[^\r\n{}]{4,120}\}", re.I),
    re.compile(rb"(?<![a-zA-Z0-9_])ctf\{[^\r\n{}]{4,120}\}", re.I),
    re.compile(rb"(?<![a-zA-Z0-9_])[0-9a-f]{32}(?![0-9a-f])", re.I),
]

# 描述/教程里常见的占位符 flag，绝不提交（覆盖各前缀变体）
PLACEHOLDER_FLAG_RES = [
    re.compile(p, re.I) for p in (
        r"^(?:flag|ctf|dasctf|csawctf)\{path\}$",
        r"^(?:flag|ctf|dasctf|csawctf)\{(?:md5hash|sha256|sha1|base64|hex)\}$",
        r"^(?:flag|ctf|dasctf|csawctf)\{flag\}$",
        r"^(?:flag|ctf|dasctf|csawctf)\{here\}$",
        r"^(?:flag|ctf|dasctf|csawctf)\{your_flag\}$",
        r"^(?:flag|ctf|dasctf|csawctf)\{\.{3}\}$",
        r"^(?:flag|ctf|dasctf|csawctf)\{\s*\.\.\.\s*\}$",
        r"^(?:flag|ctf|dasctf|csawctf)\{.*(?:example|placeholder).*\}$",
    )
]

CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def _is_placeholder(flag: str) -> bool:
    return any(p.match(flag) for p in PLACEHOLDER_FLAG_RES)


def extract_flags(text: bytes | str) -> list[str]:
    raw = text.encode() if isinstance(text, str) else text
    found: list[str] = []
    for pat in FLAG_PATTERNS:
        for m in pat.finditer(raw):
            found.append(m.group(0).decode(errors="replace"))
    seen: set[str] = set()
    out: list[str] = []
    for f in found:
        if f in seen or _is_placeholder(f):
            continue
        seen.add(f)
        out.append(f)
    return out


def extract_final_text(stdout: str) -> str:
    """从 pi --mode json 的事件流提取最终 assistant 文本。

    移植自 Cairn dispatcher/workers/adapters/pi.py extract_response_text：
    取最后一个 turn_end/agent_end 的 assistant 消息中的 text 部件。
    """
    assistant_message: dict[str, Any] | None = None
    for line in stdout.splitlines():
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
        if etype == "turn_end":
            msg = payload.get("message")
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                assistant_message = msg
        elif etype == "agent_end":
            messages = payload.get("messages")
            if isinstance(messages, list):
                for msg in reversed(messages):
                    if isinstance(msg, dict) and msg.get("role") == "assistant":
                        assistant_message = msg
                        break
    if assistant_message is None:
        return stdout
    content = assistant_message.get("content")
    if not isinstance(content, list):
        return stdout
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
    joined = "\n".join(parts).strip()
    return joined or stdout


def parse_worker_output(log_text: str) -> dict[str, Any]:
    """worker 日志 → {final_text, flags}。三层回退：事件提取 → 全文 → 空。"""
    final = extract_final_text(log_text)
    flags = extract_flags(final) or extract_flags(log_text)
    return {"final_text": final, "flags": flags}


def start_worker(cmd: list[str], cwd: Path, log_path: Path) -> subprocess.Popen:
    """启动 worker：独立进程组 + 输出落盘。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(log_path, "w", encoding="utf-8")
    return subprocess.Popen(
        cmd, cwd=str(cwd), stdout=fh, stderr=subprocess.STDOUT,
        creationflags=CREATE_NEW_PROCESS_GROUP,
    )


def kill_tree(proc: subprocess.Popen) -> None:
    """组杀：taskkill /T /F 杀整棵进程树（含 pi/powershell 孙子进程）。"""
    if proc.poll() is not None:
        return
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                   capture_output=True, timeout=30)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def cleanup_orphans(pattern: str = "cli.js") -> int:
    """清理孤儿 worker：node cli.js 且父进程已死。返回清理数。"""
    import psutil  # 延迟导入：仅 Windows 编排机需要

    killed = 0
    for p in psutil.process_iter(["pid", "name", "cmdline", "ppid"]):
        try:
            info = p.info
            if info["name"] != "node.exe" or not info["cmdline"]:
                continue
            if not any(pattern in c for c in info["cmdline"]):
                continue
            parent = psutil.Process(info["ppid"])
            _ = parent  # 父进程存在则跳过
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            try:
                psutil.Process(info["pid"]).kill()
                killed += 1
            except Exception:
                pass
    return killed


def kali_exec(command: str, timeout: int = 300) -> dict[str, Any]:
    resp = requests.post("http://10.174.153.128:5000/api/command",
                         json={"command": command}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()
