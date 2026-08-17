# -*- coding: utf-8 -*-
"""ssh_exec.py —— Kali 命令执行（SSH 直连，paramiko），编排器旁路通道的统一实现。

为什么（2026-08-16 晚）：worker 解题早已切 SSH（kali.ts），但编排器旁路
（健康闸门/存活探测/容器运行/附件同步）还在走 Kali REST :5000——REST 服务一挂
benchmark 直接死在健康闸门（实测三次）。本模块把旁路也切到 SSH，与 worker 同通道，
REST 降级为可选调试接口。

与 kali.ts 的语义对齐：
- 凭据读 secrets/kali.json（host/port/username/password/sudo）；
- kali 用户登录 + `sudo -S -p ''` 提权（密码经 stdin 写入后立即 EOF）；
- 命令 base64 传输，杜绝引号/heredoc/换行被转义破坏；
- 连接按进程复用，失败自动重连一次。

依赖：pip install paramiko（Windows 编排机）。
"""
from __future__ import annotations

import base64
import json
import threading
import time
from pathlib import Path
from typing import Any, Optional

SECRETS_PATH = Path(r"D:\ctf-agent\secrets\kali.json")
_SSH_TIMEOUT_DEFAULT = 300


def _load_config() -> dict:
    try:
        cfg = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"secrets/kali.json 缺失或损坏: {e}")
    need = ("host", "username", "password")
    for k in need:
        if not cfg.get(k):
            raise RuntimeError(f"secrets/kali.json 缺少字段 {k}")
    return cfg


def _connect() -> Any:
    import paramiko
    cfg = _load_config()
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(hostname=cfg["host"], port=int(cfg.get("port") or 22),
              username=cfg["username"], password=cfg["password"],
              timeout=15, banner_timeout=15, auth_timeout=15,
              look_for_keys=False, allow_agent=False)
    return c


# 线程本地连接（2026-08-17 实测重构）：共享单连接在多线程并发（dispatch 同时 spawn 6 个
# worker，每线程 ~30 次 kali_exec + 隧道 open_channel）下会被互相踩踏——任一线程的异常
# 关闭公共连接，其他线程在途通道全部空失败（"run failed(SSH 抖动)"）。改线程本地：
# 每线程一条独立连接，互不干扰；重连只影响本线程。
_tls = threading.local()


def _get_client() -> Any:
    c = getattr(_tls, "client", None)
    if c is not None:
        try:
            t = c.get_transport()
            if t is not None and t.is_active():
                return c
        except Exception:
            pass
        try:
            c.close()
        except Exception:
            pass
        _tls.client = None
    try:
        c = _connect()
    except Exception:
        _tls.client = None
        raise
    _tls.client = c
    return c


def kali_ssh_exec(command: str, timeout: int = _SSH_TIMEOUT_DEFAULT) -> dict[str, Any]:
    """执行一条命令（经 base64 + bash，语义与 kali.ts buildRemoteCommand 对齐）。

    返回 {"stdout": str, "stderr": str, "returncode": int, "success": bool}。
    """
    cfg = _load_config()
    sudo = bool(cfg.get("sudo", cfg.get("username") != "root"))
    b64 = base64.b64encode(command.encode("utf-8")).decode()
    # kali.ts buildRemoteCommand 同款：sudo 包住整个管道（密码从通道 stdin 喂给 sudo），
    # 内层 timeout + bash -c 'echo <b64> | base64 -d | bash' 在提权后执行
    inner = f"timeout -k 5 {timeout} bash -c {json.dumps('echo ' + b64 + ' | base64 -d | bash')}"
    remote = f"sudo -S -p '' {inner}" if sudo else inner
    last_err = ""
    for attempt in (1, 2, 3):
        try:
            client = _get_client()
            chan = client.get_transport().open_session()
            # 不用 pty：sudo -S 走 stdin 管道即可（pty 会把密码回显进输出，且 rc 不可靠）
            chan.exec_command(remote)
            if sudo:
                chan.sendall((cfg["password"] + "\n").encode())
                chan.shutdown_write()
            chan.settimeout(max(timeout, 30))
            out = _read_stream(chan, timeout)
            try:
                rc = chan.recv_exit_status()
            except Exception:
                rc = -1
            # 2026-08-17 实测：并发/网络抖动下会出现"rc=126 且零输出"的假失败
            # （命令实际没执行/通道异常），不能直接判死——空输出+非零 rc 一律重试。
            if rc == 0 or out.strip():
                return {"stdout": out, "stderr": "", "returncode": rc, "success": rc == 0}
            last_err = f"rc={rc} empty output"
        except Exception as e:
            last_err = f"ssh exec failed: {e}"
            try:
                c = getattr(_tls, "client", None)
                if c is not None:
                    c.close()
            except Exception:
                pass
            _tls.client = None
        time.sleep(1.0 * attempt)
    return {"stdout": "", "stderr": f"ssh exec failed: {last_err}",
            "returncode": -1, "success": False}


def _read_stream(chan: Any, timeout: int) -> str:
    """读 stdout 直到通道关闭或超时（sudo -p '' 无提示词，stdout 是干净的命令输出）。"""
    buf = bytearray()
    deadline = time.time() + max(timeout, 30)
    while time.time() < deadline:
        if chan.recv_ready():
            chunk = chan.recv(65536)
            if chunk:
                buf.extend(chunk)
                continue
        if chan.exit_status_ready():
            # 退出后把残留在缓冲里的输出吸完
            while chan.recv_ready():
                chunk = chan.recv(65536)
                if not chunk:
                    break
                buf.extend(chunk)
            break
        time.sleep(0.05)
    return buf.decode("utf-8", errors="replace")


def kali_healthy() -> tuple[bool, str]:
    """SSH 健康检查：python3 关键工具导入 + 一句 echo。"""
    cmd = ("python3 -c 'import pwn,z3,angr,sympy; print(\"tools-ok\")' 2>&1 | tail -1; "
           "echo healthy")
    r = kali_ssh_exec(cmd, timeout=60)
    ok = r.get("success") is True and "healthy" in r.get("stdout", "")
    return ok, (r.get("stdout", "") or r.get("stderr", ""))[:120]


def close() -> None:
    """关闭本线程的连接（进程退出/测试用）。"""
    c = getattr(_tls, "client", None)
    if c is not None:
        try:
            c.close()
        except Exception:
            pass
        _tls.client = None
