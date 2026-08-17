# -*- coding: utf-8 -*-
"""worker 容器沙箱（benchmark 专用，2026-08-17）。

把 worker 的执行环境从"Kali 宿主机 root"降级为"rootless + userns 独立容器"：
- ctfworker 用户 rootless podman（userns：容器内 root ≠ 宿主机 root）；
- --network slirp4netns:allow_host_loopback=true：容器内 10.0.2.2 = 宿主机 127.0.0.1
  （编排器把题目 connection 的 127.0.0.1 改写成 10.0.2.2）；
- 出网由宿主机 iptables 按 uid 物理封死（只放行 127.0.0.0/8 的靶机段/回连段）——
  写脚本绕过 NET_POLICY 正则也没用；
- 不挂宿主机任何目录（无 /var/lib/containers、无其他题目目录），只挂自己工作区；
- Windows ↔ 容器经 paramiko direct-tcpip 隧道（本机回环端口 → Kali 127.0.0.1:22xxx）。
比赛路径（bench_mode=False）完全不走本模块。
"""
from __future__ import annotations

import secrets
import socket
import threading
import time
from pathlib import Path
from typing import Any, Optional

SANDBOX_IMG = "worker:latest"
SANDBOX_WS_HOST = Path("/data/worker-ws")          # Kali 宿主侧工作区根（挂进容器 /root/ctf）
PORT_BASE, PORT_SPAN = 22900, 100
CALLBACK_PORTS = (23100, 23199)                    # 回连场景：worker 发布端口供靶机回连


def _sh(cmd: str, timeout: int = 300) -> dict[str, Any]:
    from workers import kali_exec
    return kali_exec(cmd, timeout=timeout)


def ensure_iptables() -> bool:
    """幂等应用 ctfworker 出站封锁（镜像构建完、跑分前调用）。"""
    rules = [
        f"-A OUTPUT -m owner --uid-owner ctfworker -d 127.0.0.0/8 -p tcp "
        f"--dport 22000:22499 -j ACCEPT",
        f"-A OUTPUT -m owner --uid-owner ctfworker -d 127.0.0.0/8 -p tcp "
        f"--dport {CALLBACK_PORTS[0]}:{CALLBACK_PORTS[1]} -j ACCEPT",
        "-A OUTPUT -m owner --uid-owner ctfworker -d 127.0.0.0/8 -j REJECT",
        "-A OUTPUT -m owner --uid-owner ctfworker -j REJECT",
    ]
    for rule in rules:
        r = _sh(f"iptables -C OUTPUT {rule} 2>/dev/null || iptables -A OUTPUT {rule}", timeout=60)
        if not r.get("success"):
            print(f"[sandbox] iptables rule failed: {rule}")
            return False
    print("[sandbox] iptables egress lock applied (uid ctfworker)")
    return True


def alloc_host_port() -> int:
    r = _sh(f"p={PORT_BASE}; for ((i=0;i<{PORT_SPAN};i++)); do "
            f"if ! (exec 3<>/dev/tcp/127.0.0.1/$((p+i))) 2>/dev/null; then "
            f"echo $((p+i)); exit 0; fi; done; echo 0", timeout=60)
    try:
        return int((r.get("stdout", "0") or "0").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0


def spawn_worker_container(cid: str, idx: int) -> Optional[tuple[str, int, str]]:
    """起一个 worker 容器，返回 (host, port, password) 供 SSH 隧道；失败返回 None。"""
    password = secrets.token_urlsafe(18).replace("-", "x").replace("_", "y")
    hp = alloc_host_port()
    if not hp:
        print(f"[sandbox] {cid}/w{idx} no free port")
        return None
    ws = SANDBOX_WS_HOST / cid / f"w{idx}"
    _sh(f"mkdir -p -m 777 {ws} && chown -R ctfworker:ctfworker {ws}", timeout=60)
    name = f"ws-{cid[:32]}-{idx}".lower()
    r = _sh(f"runuser -u ctfworker -- podman rm -f {name} >/dev/null 2>&1; true; "
            f"runuser -u ctfworker -- podman run -d --rm --name {name} "
            f"--network slirp4netns:allow_host_loopback=true "
            f"-p 127.0.0.1:{hp}:22 -e WORKER_PASS={password} "
            f"-v {ws}:/root/ctf --cap-drop=ALL --cap-add=SYS_PTRACE --cap-add=NET_RAW "
            f"--security-opt no-new-privileges {SANDBOX_IMG}", timeout=300)
    out = r.get("stdout", "") + r.get("stderr", "")
    if not r.get("success") or "Error" in out:
        print(f"[sandbox] {cid}/w{idx} run failed: {out[-200:]}")
        return None
    for _ in range(40):
        r2 = _sh(f"(exec 3<>/dev/tcp/127.0.0.1/{hp}) 2>/dev/null && echo OPEN || echo CLOSED",
                 timeout=30)
        if "OPEN" in r2.get("stdout", ""):
            print(f"[sandbox] {cid}/w{idx} container up ({name} -> 127.0.0.1:{hp})")
            return ("127.0.0.1", hp, password)
        time.sleep(1.0)
    _sh(f"runuser -u ctfworker -- podman rm -f {name} >/dev/null 2>&1; true", timeout=60)
    print(f"[sandbox] {cid}/w{idx} sshd not ready in 40s")
    return None


def kill_worker_container(cid: str, idx: int) -> None:
    name = f"ws-{cid[:32]}-{idx}".lower()
    _sh(f"runuser -u ctfworker -- podman rm -f {name} >/dev/null 2>&1; true", timeout=60)


def cleanup_stale() -> None:
    """跑分开始前清理上一 run 残留的 worker 容器。"""
    _sh("runuser -u ctfworker -- podman rm -af >/dev/null 2>&1; true", timeout=120)


class SshTunnel:
    """Windows 本机回环端口 →（编排器 paramiko 通道）→ Kali 127.0.0.1:<容器 sshd>。

    每个连接临时取当前 paramiko 客户端开 direct-tcpip（自动重连语义），
    隧道生命周期覆盖单个 worker。"""

    def __init__(self, remote_host: str, remote_port: int) -> None:
        self.remote = (remote_host, remote_port)
        self.lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.lsock.bind(("127.0.0.1", 0))
        self.lsock.listen(16)
        self.lport = self.lsock.getsockname()[1]
        self._stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _addr = self.lsock.accept()
            except OSError:
                break
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        try:
            from ssh_exec import _get_client
            client = _get_client()
            chan = client.get_transport().open_channel(
                "direct-tcpip", self.remote, conn.getpeername())
        except Exception:
            try:
                conn.close()
            except OSError:
                pass
            return
        threading.Thread(target=self._pump, args=(conn, chan), daemon=True).start()
        threading.Thread(target=self._pump, args=(chan, conn), daemon=True).start()

    @staticmethod
    def _pump(src: Any, dst: Any) -> None:
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except Exception:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.close()
                except Exception:
                    pass

    def close(self) -> None:
        self._stop.set()
        try:
            self.lsock.close()
        except OSError:
            pass
