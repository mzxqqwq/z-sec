# -*- coding: utf-8 -*-
"""服务题本地复活 —— 用 Kali 上的 podman 把「靶机已停」的服务题跑起来。

背景（T-S1，2026-08-17）：
- CTFTiny/NYU 服务题的 box 是 CSAW 当年的公网靶机，早已下线 → liveness=dead；
- llmctf/* 镜像发布在 docker.io（flag 当年构建时烘焙进镜像内部，本就是攻击面）；
- 方案：镜像已本地存在 → podman run 起容器，容器端口映射到 Kali 127.0.0.1 空闲高端口
  → 平台把 box 覆盖为 127.0.0.1、port 覆盖为该端口 → worker 在 Kali 内直连解题；
- 完整性：复活不复制任何题库文件到 Kali；flag 只存在于容器内部；worker 禁 podman/docker
  命令（提示词已有禁令，transcript 可审计）。

依赖：src/tools/service-manifest.json（extract-service-images.py 产出）。
镜像预拉：src/tools/pull-service-images.sh（一次性，之后跑 benchmark 不需要 VPN）。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Optional

MANIFEST_PATH = Path(r"D:\ctf-agent\src\tools\service-manifest.json")

# 题目声明的容器端口 → 探测/重试参数
PROBE_DEADLINE = 20  # 起容器后等待服务可连的最长时间（秒）
PODMAN_TIMEOUT = 120  # 单条 podman 命令上限（秒）

# 特殊服务题：通用 podman run -d -p 跑不起来的（docker-in-docker/多阶段交互），
# 用宿主 socat 服务 + 每连接容器替代。镜像名/端口/启动命令都在这里声明。
# 注意：这类题的 flag 烘焙在镜像内部（不落 Kali 磁盘），会话容器 --rm 即销毁。
import base64 as _b64

_SHOWDOWN_SVC = ("#!/bin/bash\n"
                 "exec podman run --rm -i -t docker.io/llmctf/2018f-msc-showdown-container:latest\n")
_SHOWDOWN_SVC_B64 = _b64.b64encode(_SHOWDOWN_SVC.encode()).decode()

HOST_OVERRIDES: dict[str, dict[str, Any]] = {
    "msc-showdown": {
        "port": 9222,
        "start": (f"echo {_SHOWDOWN_SVC_B64} | base64 -d > /root/ctf/showdown-svc.sh && "
                  "chmod +x /root/ctf/showdown-svc.sh; "
                  "test -f /tmp/showdown-svc.pid && kill -0 $(cat /tmp/showdown-svc.pid) 2>/dev/null && exit 0; "
                  "nohup socat TCP-LISTEN:9222,fork,reuseaddr "
                  "EXEC:/root/ctf/showdown-svc.sh,pty,stderr,setsid,sane "
                  ">/tmp/showdown-svc.log 2>&1 & echo $! > /tmp/showdown-svc.pid"),
        "stop": ("test -f /tmp/showdown-svc.pid && kill $(cat /tmp/showdown-svc.pid) 2>/dev/null; "
                 "pkill -f 'socat TCP-LISTEN:9222' 2>/dev/null; rm -f /tmp/showdown-svc.pid; true"),
    },
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _safe(name: str) -> str:
    return (re.sub(r"[^a-zA-Z0-9_-]", "-", str(name)) or "svc")[:40]


def _kali(api: str, cmd: str, timeout: int = PODMAN_TIMEOUT) -> dict[str, Any]:
    import requests
    resp = requests.post(f"{api}/api/command", json={"command": cmd}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _port_pairs(ports: list[str]) -> list[tuple[int, int]]:
    """'21200:21200' / '0:8000' / '1337' → [(host, container)]。"""
    out: list[tuple[int, int]] = []
    for raw in ports:
        p = str(raw).strip()
        m = re.match(r"^(?:\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:)?(\d+):(\d+)(?:/(?:tcp|udp))?$", p)
        if m:
            out.append((int(m.group(1)), int(m.group(2))))
            continue
        m2 = re.match(r"^(\d+)(?:/(?:tcp|udp))?$", p)
        if m2:
            v = int(m2.group(1))
            out.append((v, v))
    return out


class ServiceReviver:
    """挑战 → podman 容器。有状态（记录本 run 起过的容器，close 时统一清理）。"""

    def __init__(self, kali_api: str = "http://10.174.153.128:5000",
                 manifest_path: Path = MANIFEST_PATH,
                 enabled: bool = True) -> None:
        self.kali_api = kali_api.rstrip("/")
        self.enabled = enabled
        self._index: dict[str, dict[str, Any]] = {}
        self._running: dict[str, dict[str, Any]] = {}
        self._podman_sock: Optional[bool] = None  # Kali 上 /run/podman/podman.sock 是否存在
        self._path_exists: dict[str, bool] = {}
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        for key, entry in (data or {}).items():
            if entry.get("services"):
                self._index[_norm(key)] = entry

    # ---------- 清单匹配 ----------
    def match(self, meta_path: str) -> Optional[dict[str, Any]]:
        n = _norm(meta_path)
        if n in self._index:
            return self._index[n]
        tail = n.rstrip("/").split("/")[-1]
        for key, entry in self._index.items():
            if key.rstrip("/").split("/")[-1] == tail:
                return entry
        return None

    # ---------- Kali 端原子操作 ----------
    def _sh(self, cmd: str, timeout: int = PODMAN_TIMEOUT) -> tuple[str, str]:
        """REST /api/command 用 /bin/sh 执行，/dev/tcp 与算术 for 都不支持；
        base64 包裹后交给 bash（kali.ts 的同款教训，2026-08-16）。"""
        import base64
        b64 = base64.b64encode(cmd.encode("utf-8")).decode()
        try:
            r = _kali(self.kali_api, f"echo {b64} | base64 -d | bash", timeout=timeout)
        except Exception as e:
            return "", f"kali api error: {e}"
        return str(r.get("stdout") or ""), str(r.get("stderr") or "")

    def image_ready(self, image: str) -> bool:
        out, _ = self._sh(f"podman image exists docker.io/{image} && echo yes || echo no",
                          timeout=30)
        return "yes" in out

    def alloc_port(self, base: int = 21000, span: int = 500) -> int:
        cmd = (f"p={base}; for ((i=0;i<{span};i++)); do "
               f"if ! (exec 3<>/dev/tcp/127.0.0.1/$((p+i))) 2>/dev/null; then "
               f"echo $((p+i)); exit 0; fi; done; echo 0")
        out, _ = self._sh(cmd, timeout=30)
        try:
            return int((out.strip().splitlines() or ["0"])[-1])
        except ValueError:
            return 0

    def _podman_sock_ready(self) -> bool:
        if self._podman_sock is None:
            out, _ = self._sh("test -S /run/podman/podman.sock && echo yes || echo no",
                              timeout=30)
            self._podman_sock = "yes" in out
        return self._podman_sock

    def _path_ok(self, path: str) -> bool:
        if path not in self._path_exists:
            out, _ = self._sh(f"test -e {path} && echo yes || echo no", timeout=30)
            self._path_exists[path] = "yes" in out
        return self._path_exists[path]

    def _volume_args(self, svc: dict[str, Any]) -> list[str]:
        """compose volumes → podman -v 参数。

        - /var/run/docker.sock：Kali 有 podman.sock 就映射替身（docker API 兼容），
          没有则跳过该卷——docker-in-docker 题先跑起来，行为由服务自身决定；
        - 宿主路径不存在（编排机特有目录）→ 跳过，不因一个卷废掉整题。
        """
        args: list[str] = []
        for vol in svc.get("volumes") or []:
            v = str(vol)
            if ":" not in v:
                continue
            src, dst = v.split(":", 1)
            if src == "/var/run/docker.sock":
                if self._podman_sock_ready():
                    args.extend(["-v", f"/run/podman/podman.sock:{dst}:ro"])
                continue
            if src.startswith("/") and not self._path_ok(src):
                continue
            args.extend(["-v", v])
        return args

    @staticmethod
    def _env_args(svc: dict[str, Any]) -> list[str]:
        args: list[str] = []
        for e in svc.get("environment") or []:
            e = str(e)
            if "=" in e:
                args.extend(["-e", e])
        return args

    # ---------- 主流程 ----------
    def revive(self, cid: str, meta_path: str,
               internal_port: Optional[int]) -> tuple[bool, Optional[int], str]:
        """返回 (ok, host_port, err)。成功时服务已在 Kali 上可连。"""
        if not self.enabled:
            return False, None, "revive disabled"
        if cid in self._running:
            return True, int(self._running[cid].get("port") or 0), "already revived"

        # 特殊服务题：宿主 socat 服务（docker-in-docker 等通用 podman run 跑不起来的）
        ov = HOST_OVERRIDES.get(cid)
        if ov is None:
            tail = _norm(meta_path).rstrip("/").split("/")[-1]
            for key, spec in HOST_OVERRIDES.items():
                if _norm(key) == tail:
                    ov = spec
                    break
        if ov is not None:
            return self._revive_override(cid, ov)

        entry = self.match(meta_path)
        if entry is None:
            return False, None, "no manifest entry"
        services = entry.get("services") or []
        if not services:
            return False, None, "manifest has no services"

        cp = int(internal_port or 0)
        # 前置服务 = 端口映射含 internal_port 的服务；否则最后一个声明了 ports 的；否则最后一个
        front_idx = len(services) - 1
        if cp:
            for i, svc in enumerate(services):
                pairs = _port_pairs(svc.get("ports") or [])
                if any(c == cp for _, c in pairs):
                    front_idx = i
                    break
            else:
                for i, svc in enumerate(services):
                    if _port_pairs(svc.get("ports") or []):
                        front_idx = i
                        break
        else:
            for i, svc in enumerate(services):
                pairs = _port_pairs(svc.get("ports") or [])
                if pairs:
                    front_idx = i
                    cp = pairs[0][1]  # 无 internal_port 时取 compose 声明的容器端口
                    break
        if not cp:
            return False, None, "no container port (internal_port/ports both missing)"

        # 镜像齐备检查（缺镜像不硬拉——拉取是人工/脚本的事）
        missing = [s["normalized"] for s in services
                   if not self.image_ready(s["normalized"])]
        if missing:
            return False, None, f"image_missing:{missing[0]}"

        net = _safe(cid) + "-net"
        names: list[str] = []
        host_port = 0
        try:
            out, err = self._sh(f"podman network exists {net} || podman network create {net}",
                                timeout=60)
            if "rror" in err:
                raise RuntimeError(f"network create failed: {err[:80]}")
            for i, svc in enumerate(services):
                name = f"{_safe(cid)}-svc{i}"
                names.append(name)
                self._sh(f"podman rm -f {name} >/dev/null 2>&1; true", timeout=60)
                extra = " ".join(self._volume_args(svc) + self._env_args(svc))
                cmd = f"podman run -d --rm --name {name} --network {net}"
                if extra:
                    cmd += " " + extra
                if i == front_idx:
                    host_port = self.alloc_port()
                    if not host_port:
                        raise RuntimeError("no free host port")
                    cmd += f" -p 127.0.0.1:{host_port}:{cp} docker.io/{svc['normalized']}"
                else:
                    cmd += f" docker.io/{svc['normalized']}"
                out, err = self._sh(cmd)
                if "rror" in err or not (out or "").strip():
                    raise RuntimeError(f"run failed {svc['normalized']}: {err[:100]}")
            # 等容器起来：轮询探测 127.0.0.1:host_port
            from eval_platform import probe_host
            deadline = time.time() + PROBE_DEADLINE
            while time.time() < deadline:
                if probe_host("127.0.0.1", host_port):
                    self._running[cid] = {"net": net, "names": names,
                                          "port": host_port, "front": front_idx}
                    return True, host_port, ""
                time.sleep(2)
            raise RuntimeError("service did not answer probe")
        except Exception as e:
            for n in names:
                self._sh(f"podman rm -f {n} >/dev/null 2>&1; true", timeout=30)
            self._sh(f"podman network rm {net} >/dev/null 2>&1; true", timeout=30)
            return False, None, str(e)[:120]

    def _revive_override(self, cid: str, ov: dict[str, Any]) -> tuple[bool, Optional[int], str]:
        """宿主 socat 服务式复活（HOST_OVERRIDES）：跑 start 命令 → 探测端口。"""
        port = int(ov.get("port") or 0)
        try:
            out, err = self._sh(str(ov.get("start", "")), timeout=90)
            if "rror" in err:
                raise RuntimeError(f"start failed: {err[:100]}")
            from eval_platform import probe_host
            deadline = time.time() + PROBE_DEADLINE
            while time.time() < deadline:
                if probe_host("127.0.0.1", port):
                    self._running[cid] = {"override": True, "port": port,
                                          "stop_cmd": str(ov.get("stop", "true"))}
                    return True, port, ""
                time.sleep(2)
            raise RuntimeError("service did not answer probe")
        except Exception as e:
            self._sh(str(ov.get("stop", "true")), timeout=30)
            return False, None, str(e)[:120]

    def stop(self, cid: str) -> None:
        info = self._running.pop(cid, None)
        if not info:
            return
        if info.get("override"):
            self._sh(str(info.get("stop_cmd") or "true"), timeout=30)
            return
        for n in info.get("names", []):
            self._sh(f"podman rm -f {n} >/dev/null 2>&1; true", timeout=30)
        self._sh(f"podman network rm {info.get('net', '')} >/dev/null 2>&1; true", timeout=30)

    def stop_all(self) -> int:
        n = len(self._running)
        for cid in list(self._running):
            self.stop(cid)
        return n
