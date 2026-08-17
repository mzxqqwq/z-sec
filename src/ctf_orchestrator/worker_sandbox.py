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
SANDBOX_IMG = "worker:latest"
SANDBOX_WS_HOST = Path("/data/worker-ws")          # Kali 宿主侧工作区根（挂进容器 /root/ctf）
SANDBOX_WS_HOST_STR = "/data/worker-ws"            # 命令拼接必须用正斜杠（见 _spawn_locked 注释）
PORT_BASE, PORT_SPAN = 22900, 100
CALLBACK_PORTS = (23100, 23199)                    # 回连场景：worker 发布端口供靶机回连
# host 回退模式的受限账号：容器 spawn 失败时 worker 直接以 ctfworker uid 登录宿主机
# （不经 sudo），吃同一套 iptables 出站封锁——否则回退 worker 有完整外网，
# 写脚本绕过 NET_POLICY 就能拉公开题解（2026-08-17 实测事故：fallback 题 curl google 200）。
CTFWORKER_PASSWORD = "SBX-worker-2026!"            # Kali 侧 chpasswd 已设（见 setup）


def _sh(cmd: str, timeout: int = 300) -> dict[str, Any]:
    from workers import kali_exec
    return kali_exec(cmd, timeout=timeout)


# rootless podman 并发 run 的挂载竞态锁（2026-08-17 实测：6 并发只有 1 个挂载生效）
_RUN_LOCK = threading.Lock()


def ensure_iptables() -> bool:
    """幂等应用 ctfworker 出站封锁。直接 -F OUTPUT 再按序重建（该链只含本组规则，
    已核验；-D 逐条清理不可靠——iptables 归一化后 -D 可能匹配失败残留旧规则，
    实测残留 REJECT 会把 rootlessport 端口转发一起掐死）。

    实测约束（2026-08-17 二分验证）：不能对 ctfworker 全封 127.0.0.0/8——
    rootlessport/slirp 的端口转发内部依赖 loopback 随机端口，全封会把 -p 发布端口
    也掐死（容器在、sshd 在，但宿主连不上）。因此：
    - 10.0.2.0/24（slirp 内部）、22000-22499（靶机）、23100-23199（回连）放行；
    - podman 网桥段 10.88/16 + 10.89/16：revival/cybench 靶机的 netavark DNAT 会把
      127.0.0.1:<端口> 的目的地改写成容器网桥 IP——不放行则 ctfworker 的 SYN 落到
      全 REJECT（CTFTiny 21000 段 TIMEOUT 实锤，2026-08-17）；
    - 127/8 只拒敏感宿主端口 22/80/5000（sshd/nginx/REST）；
    - 外网（非 loopback/非内网桥段）一律 REJECT——这是真正的断网封锁。"""
    specs = [
        "-m owner --uid-owner ctfworker -d 10.0.2.0/24 -j ACCEPT",
        "-m owner --uid-owner ctfworker -d 10.88.0.0/16 -j ACCEPT",
        "-m owner --uid-owner ctfworker -d 10.89.0.0/16 -j ACCEPT",
        f"-m owner --uid-owner ctfworker -d 127.0.0.0/8 -p tcp "
        f"--dport 22000:22499 -j ACCEPT",
        f"-m owner --uid-owner ctfworker -d 127.0.0.0/8 -p tcp "
        f"--dport {CALLBACK_PORTS[0]}:{CALLBACK_PORTS[1]} -j ACCEPT",
        "-m owner --uid-owner ctfworker -d 127.0.0.0/8 -p tcp "
        "-m multiport --dports 22,80,5000 -j REJECT",
        "-m owner --uid-owner ctfworker -d 127.0.0.0/8 -j ACCEPT",
        "-m owner --uid-owner ctfworker -j REJECT",
    ]
    r = _sh("iptables -F OUTPUT && echo flushed", timeout=60)
    if not r.get("success") or "flushed" not in str(r.get("stdout", "")):
        print("[sandbox] iptables flush failed:", r.get("stderr", "")[:120])
        return False
    for spec in specs:
        r = _sh(f"iptables -A OUTPUT {spec}", timeout=60)
        if not r.get("success"):
            print(f"[sandbox] iptables rule failed: {spec} err={r.get('stderr', '')[:120]}")
            return False
    print("[sandbox] iptables egress lock applied (uid ctfworker)")
    return True


def alloc_host_port() -> int:
    """选一个"秒拒"的空闲端口。僵尸 rootlessport 监听器会让 connect 挂起（实测）——
    挂起(124)=当作占用跳过，只接受立即被 RST(失败) 的端口。"""
    r = _sh(
        f"p={PORT_BASE}; for ((i=0;i<{PORT_SPAN};i++)); do "
        f"timeout 1 bash -c '(exec 3<>/dev/tcp/127.0.0.1/'$((p+i))') 2>/dev/null'; st=$?; "
        f"if [ $st -eq 0 ]; then echo B; elif [ $st -eq 124 ]; then echo H; "
        f"else echo F; fi | grep -q '^F$' && {{ echo $((p+i)); exit 0; }}; "
        f"done; echo 0", timeout=240)
    try:
        return int((r.get("stdout", "0") or "0").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0


def spawn_worker_container(cid: str, idx: int) -> Optional[tuple[str, int, str]]:
    """起一个 worker 容器，返回 (host, port, password) 供 SSH 隧道；失败返回 None。

    2026-08-17 实测三连击：① rootless podman 并发 run 存在 mount 初始化竞态；
    ② 并发下 kali_ssh_exec 出现 rc=126 空输出假失败（已加空输出重试）；
    ③ 并发下 exec 校验命令 rc=0 但 stdout 丢数据（挂载误判）。最终方案：整个
    spawn 串行化（_RUN_LOCK），从根上消灭并发；代价是首轮 6 个 worker 逐个起，
    多花 1-2 分钟，可接受。"""
    with _RUN_LOCK:
        return _spawn_locked(cid, idx)


def _spawn_locked(cid: str, idx: int) -> Optional[tuple[str, int, str]]:
    password = secrets.token_urlsafe(18).replace("-", "x").replace("_", "y")
    # 关键：ws 必须正斜杠字符串！Path("/data/worker-ws") 在 Windows 上是 WindowsPath，
    # str() 输出反斜杠 \data\worker-ws\...，bash 把 \d 吃掉后 -v 变成无前导斜杠的
    # "命名卷"而不是 bind 挂载——容器 /root/ctf 永远是空目录（2026-08-17 实测根因）。
    ws = f"{SANDBOX_WS_HOST_STR}/{cid}/w{idx}"
    name = f"ws-{cid[:32]}-{idx}".lower()
    marker = f"SBX-MARKER-{idx}-{password[:6]}"
    last_err = ""
    for outer in range(3):
        # 1) 工作目录必须真实存在（失败静默 = podman 建空源目录 = 假性挂载失败）
        ws_ok = False
        for _w in range(3):
            rw = _sh(f"mkdir -p -m 777 {ws} && chown -R ctfworker:ctfworker {ws} "
                     f"&& echo OK", timeout=60)
            if rw.get("success") and "OK" in str(rw.get("stdout", "")):
                ws_ok = True
                break
            time.sleep(2)
        if not ws_ok:
            last_err = "workspace mkdir failed"
            print(f"[sandbox] {cid}/w{idx} {last_err}")
            return None
        # 2) 挂载校验用的 marker（写入失败同样重试）
        marker_ok = False
        for _m in range(3):
            rm = _sh(f"echo '{marker}' > {ws}/.sbx-marker && cat {ws}/.sbx-marker", timeout=60)
            if rm.get("success") and marker in str(rm.get("stdout", "")):
                marker_ok = True
                break
            time.sleep(2)
        if not marker_ok:
            last_err = "marker write failed"
            print(f"[sandbox] {cid}/w{idx} {last_err}")
            return None
        hp = 0
        for attempt in range(3):
            hp = alloc_host_port()
            if not hp:
                last_err = "no free port"
                continue
            run_cmd = (f"runuser -u ctfworker -- podman rm -f {name} >/dev/null 2>&1; true; "
                       f"runuser -u ctfworker -- podman run -d --rm --name {name} "
                       f"--network slirp4netns:allow_host_loopback=true "
                       f"-p 127.0.0.1:{hp}:22 -e WORKER_PASS={password} "
                       f"-v {ws}:/root/ctf --cap-drop=ALL "
                       # sshd 最小能力（chroot privsep/会话 setuid/绑 22 端口）+ 解题必需（gdb 的
                       # SYS_PTRACE、nmap 半开扫描的 NET_RAW）；无 NET_ADMIN/SYS_ADMIN/DAC_READ_SEARCH。
                       f"--cap-add=SYS_PTRACE --cap-add=NET_RAW --cap-add=NET_BIND_SERVICE "
                       f"--cap-add=SYS_CHROOT --cap-add=SETUID --cap-add=SETGID "
                       f"--cap-add=CHOWN --cap-add=DAC_OVERRIDE --cap-add=FOWNER "
                       f"--cap-add=FSETID --cap-add=AUDIT_WRITE --cap-add=KILL "
                       f"--security-opt no-new-privileges {SANDBOX_IMG}")
            r = _sh(run_cmd, timeout=300)
            out = r.get("stdout", "") + r.get("stderr", "")
            if r.get("success") and "Error" not in out and out.strip():
                break
            last_err = out[-200:] or f"run failed(SSH 抖动 rc={r.get('return_code')})"
            print(f"[sandbox] {cid}/w{idx} run attempt {attempt + 1} failed: {last_err}")
            time.sleep(2)  # 让 ssh_exec 自动重连
            hp = 0
        if not hp:
            continue
        for _ in range(40):
            r2 = _sh(f"timeout 2 bash -c '(exec 3<>/dev/tcp/127.0.0.1/{hp}) 2>/dev/null' && "
                     f"echo OPEN || echo CLOSED", timeout=30)
            if "OPEN" in r2.get("stdout", ""):
                break
            time.sleep(1.0)
        else:
            last_err = "sshd not ready in 40s"
            print(f"[sandbox] {cid}/w{idx} {last_err}; retrying spawn (outer {outer + 1})")
            _sh(f"runuser -u ctfworker -- podman rm -f {name} >/dev/null 2>&1; true", timeout=60)
            continue
        # 3) 挂载内容校验：marker 可见 = -v 挂载真生效（源目录/并发竞态下可能空 overlay）
        r3 = _sh(f"runuser -u ctfworker -- podman exec {name} cat /root/ctf/.sbx-marker 2>&1",
                 timeout=60)
        if marker in str(r3.get("stdout", "")):
            _sh(f"runuser -u ctfworker -- podman exec {name} rm -f /root/ctf/.sbx-marker "
                f">/dev/null 2>&1; rm -f {ws}/.sbx-marker", timeout=60)
            print(f"[sandbox] {cid}/w{idx} container up ({name} -> 127.0.0.1:{hp}, mount ok)")
            return ("127.0.0.1", hp, password)
        print(f"[sandbox] {cid}/w{idx} mount check: rc={r3.get('return_code')} "
              f"out={str(r3.get('stdout', ''))[:60]!r}")
        last_err = "mount not visible"
        print(f"[sandbox] {cid}/w{idx} {last_err}; retrying spawn (outer {outer + 1})")
        _sh(f"runuser -u ctfworker -- podman rm -f {name} >/dev/null 2>&1; true", timeout=60)
    print(f"[sandbox] {cid}/w{idx} spawn failed: {last_err}")
    return None


def kill_worker_container(cid: str, idx: int) -> None:
    name = f"ws-{cid[:32]}-{idx}".lower()
    _sh(f"runuser -u ctfworker -- podman rm -f {name} >/dev/null 2>&1; true", timeout=60)


def cleanup_stale() -> None:
    """跑分开始前清理上一 run 残留的 worker 容器、僵尸 rootlessport 与垃圾命名卷。"""
    _sh("runuser -u ctfworker -- podman rm -af >/dev/null 2>&1; "
        "pkill -9 -u ctfworker -f rootlessport 2>/dev/null; "
        "runuser -u ctfworker -- podman volume prune -f >/dev/null 2>&1; true", timeout=120)


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
        # open_channel 失败重试（编排器 SSH 并发重连窗口内会短暂不可用——实测
        # "Connection lost before handshake" 崩 worker；3 次重试后仍失败才放弃）
        chan = None
        for _attempt in range(3):
            try:
                from ssh_exec import _get_client
                client = _get_client()
                chan = client.get_transport().open_channel(
                    "direct-tcpip", self.remote, conn.getpeername())
                break
            except Exception:
                chan = None
                time.sleep(0.5)
        if chan is None:
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
