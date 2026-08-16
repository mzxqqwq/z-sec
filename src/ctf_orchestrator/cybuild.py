# -*- coding: utf-8 -*-
"""cybuild.py —— Cybench 服务题本地构建 + 起容器流水线（2026-08-16 晚）。

Cybench 服务题与 CSAW 不同：镜像没有发布到公共仓库，每题自带 build 上下文
（compose services[].build + Dockerfile）。流程：
  打包题目录（排除 metadata/solution 真值）→ SFTP 传 Kali → podman build →
  podman run（端口映射 127.0.0.1 空闲端口）→ 覆盖连接点。

完整性纪律（与 showdown 同款）：
- metadata/、solution/ 不下 Kali（真值）；
- 构建上下文里 flag.txt 是 Dockerfile 需要的材料（烘进镜像=攻击面），
  构建完立刻删除 Kali 上的构建目录；
- worker 禁 podman 命令（提示词禁令 + 审计）。
"""
from __future__ import annotations

import io
import json
import re
import tarfile
import time
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:
    yaml = None

from workers import kali_exec  # noqa: E402  (同目录模块)

EXCLUDE_DIRS = {"metadata", "solution", "solutions", ".git"}
BUILD_BASE = "/tmp/cybench-builds"  # 临时构建上下文（kali 用户可写；镜像落 /data 的 podman 存储）


def parse_services(ch_dir: Path) -> list[dict[str, Any]]:
    """找 compose（根目录或 env/ 等子目录），返回 [{name, compose_dir, build_ctx, dockerfile, ports, image}]。

    build 上下文相对 compose 文件所在目录解析（cybench 布局：env/docker-compose.yml +
    env/chall/Dockerfile）。"""
    compose = None
    for p in (sorted(ch_dir.rglob("docker-compose.yml")) + sorted(ch_dir.rglob("docker-compose.yaml"))
              + sorted(ch_dir.rglob("compose.yml"))):
        compose = p
        break
    if compose is None or yaml is None:
        return []
    base = compose.parent
    try:
        data = yaml.safe_load(compose.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    out = []
    for name, svc in (data.get("services") or {}).items():
        if not isinstance(svc, dict):
            continue
        build = svc.get("build")
        ctx = ""
        df = ""
        if isinstance(build, str):
            ctx = build
        elif isinstance(build, dict):
            ctx = str(build.get("context") or ".")
            df = str(build.get("dockerfile") or "")
        ports = svc.get("ports") or []
        if isinstance(ports, str):
            ports = [ports]
        out.append({"name": str(name),
                    "image": str(svc.get("image") or "").strip(),
                    "compose_dir": str(base.relative_to(ch_dir)).replace("\\", "/") or ".",
                    "build_ctx": ctx, "dockerfile": df,
                    "ports": [str(p) for p in ports]})
    return out


def _tar_filter(rel_path: str) -> bool:
    """打包时排除真值/无关目录（metadata/solution/.git/writeup）。"""
    parts = rel_path.replace("\\", "/").split("/")
    if any(p in EXCLUDE_DIRS for p in parts):
        return False
    return not parts[-1].lower().startswith("writeup")


# 老 Debian/Ubuntu 基础镜像 apt 源修复（2026 年 EOL 源已归档，构建时 apt update 404）
APT_FIX_LINE = (
    "RUN sed -i 's|deb.debian.org|archive.debian.org|g; "
    "s|security.debian.org|archive.debian.org|g' "
    "/etc/apt/sources.list /etc/apt/sources.list.d/*.list 2>/dev/null; "
    "sed -i 's|archive.ubuntu.com|old-releases.ubuntu.com|g; "
    "s|security.ubuntu.com|old-releases.ubuntu.com|g' "
    "/etc/apt/sources.list /etc/apt/sources.list.d/*.list 2>/dev/null; true"
)


def _patch_dockerfile(text: str) -> str:
    """每个 FROM 后注入 apt 源替换（新源上 sed 无害 no-op，幂等）。"""
    out: list[str] = []
    for line in text.splitlines():
        out.append(line)
        if line.strip().upper().startswith("FROM") and APT_FIX_LINE not in text:
            out.append(APT_FIX_LINE)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _package(ch_dir: Path) -> bytes:
    """题目录 → tar.gz 字节流（排除 metadata/solution/.git/writeup；Dockerfile 注入 apt 源修复）。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for p in sorted(ch_dir.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(ch_dir).as_posix()
            if not _tar_filter(rel):
                continue
            if p.stat().st_size > 100 * 1024 * 1024:
                print(f"[cybuild] skip huge file {rel}")
                continue
            if p.name == "Dockerfile":
                data = _patch_dockerfile(p.read_text(encoding="utf-8", errors="replace")).encode("utf-8")
                info = tarfile.TarInfo(name=rel)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
                continue
            tf.add(p, arcname=rel)
    return buf.getvalue()


def _sftp_put_bytes(remote_path: str, data: bytes) -> None:
    """paramiko SFTP 上传字节流（大 tar 走文件通道，不走 base64 exec）。"""
    import paramiko
    from ssh_exec import _get_client
    client = _get_client()
    sftp = client.open_sftp()
    try:
        with sftp.open(remote_path, "wb") as f:
            f.write(data)
    finally:
        sftp.close()


def _port_of(ports: list[str]) -> Optional[int]:
    """compose ports 里取容器端口：'1337:1337'→1337、'80'→80、'0:8080'→8080。"""
    for raw in ports:
        m = re.match(r"^(?:\d+\.\d+\.\d+\.\d+:)?(\d+):(\d+)(?:/\w+)?$", raw)
        if m:
            return int(m.group(2))
        m2 = re.match(r"^(\d+)(?:/\w+)?$", raw)
        if m2:
            return int(m2.group(1))
    return None


def alloc_host_port(base: int = 22000, span: int = 500) -> int:
    r = kali_exec(
        f"p={base}; for ((i=0;i<{span};i++)); do "
        f"if ! (exec 3<>/dev/tcp/127.0.0.1/$((p+i))) 2>/dev/null; then "
        f"echo $((p+i)); exit 0; fi; done; echo 0", timeout=60)
    try:
        return int((r.get("stdout", "0") or "0").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0


def build_and_run(cid: str, ch_dir: Path, target_host: str,
                  timeout_build: int = 900) -> tuple[bool, Optional[int], str]:
    """构建 + 起服务。返回 (ok, host_port, err)。"""
    services = parse_services(ch_dir)
    if not services:
        return False, None, "无 compose/build 定义"
    # target_host = "svc:port" → 前置服务与容器端口
    m = re.match(r"^([^:]+):(\d+)$", (target_host or "").strip())
    if m:
        svc_name, cport = m.group(1), int(m.group(2))
    else:
        svc_name, cport = "", 0
    front = next((s for s in services if s["name"] == svc_name), None)
    if front is None and len(services) == 1:
        front = services[0]
    if front is None:
        front = services[0]
    if not cport:
        cport = _port_of(front.get("ports") or []) or 0
    if not cport:
        return False, None, "无法确定容器端口（target_host 与 compose ports 都没有）"

    safe_cid = re.sub(r"[^a-zA-Z0-9_-]", "-", cid)
    safe_cid = safe_cid.strip("_-.")[:48].rstrip("_-.")  # 防截断尾部留下分隔符（podman tag 非法）
    remote_dir = f"{BUILD_BASE}/{safe_cid}"

    # 1. 打包 + 上传 + 解包（目录 777：SFTP 以 kali 用户写，build/run 以 root 执行）
    data = _package(ch_dir)
    r = kali_exec(f"rm -rf {remote_dir} && mkdir -p -m 777 {remote_dir}", timeout=60)
    if not r.get("success"):
        return False, None, f"kali mkdir 失败: {r.get('stderr')}"
    try:
        _sftp_put_bytes(f"{remote_dir}/src.tgz", data)
    except Exception as e:
        return False, None, f"上传失败: {e}"
    r = kali_exec(f"cd {remote_dir} && tar -xzf src.tgz && rm src.tgz && ls | head -5",
                  timeout=120)
    if not r.get("success"):
        return False, None, f"解包失败: {r.get('stderr', '')[:150]}"

    # 2. podman build 每个有 build 的服务（镜像 tag: cybench-<cid>-<svc>）
    net = f"cb-{safe_cid}"
    names: list[str] = []
    tags: dict[str, str] = {}
    try:
        for svc in services:
            tag = f"cybench-{safe_cid}-{svc['name']}".lower()
            tags[svc["name"]] = tag
            if svc.get("image") and not svc.get("build_ctx"):
                tags[svc["name"]] = svc["image"]  # 直接用镜像（含 gcr 拉不到的→失败可见）
                continue
            ctx = svc.get("build_ctx") or "."
            compose_rel = svc.get("compose_dir") or "."
            ctx_path = f"{compose_rel}/{ctx}".replace("//", "/").rstrip("/") or "."
            df = f" -f {svc['dockerfile']}" if svc.get("dockerfile") else ""
            r = kali_exec(f"cd {remote_dir}/{ctx_path} && podman build -t {tag}{df} . 2>&1 | tail -15",
                          timeout=timeout_build)
            out = r.get("stdout", "") + r.get("stderr", "")
            if "Successfully tagged" not in out and "COMMIT" not in out:
                raise RuntimeError(f"build {svc['name']} 失败: {out[-160:]}")
        # 3. 起容器（网络 + 前置服务端口映射）
        r = kali_exec(f"podman network exists {net} || podman network create {net}",
                      timeout=60)
        hp = alloc_host_port()
        if not hp:
            raise RuntimeError("无空闲端口")
        for svc in services:
            name = f"{safe_cid}-{svc['name']}".lower()[:40]
            names.append(name)
            kali_exec(f"podman rm -f {name} >/dev/null 2>&1; true", timeout=60)
            cmd = f"podman run -d --rm --name {name} --network {net} "
            if svc is front:
                cmd += f"-p 127.0.0.1:{hp}:{cport} "
            r = kali_exec(cmd + tags[svc["name"]], timeout=300)
            if not r.get("success") or "Error" in r.get("stderr", ""):
                raise RuntimeError(f"run {svc['name']} 失败: {r.get('stderr', '')[:120]}")
        # 4. 探测（服务冷启动可能慢，Java 类 60s 兜底）
        from eval_platform import probe_host
        deadline = time.time() + 60
        while time.time() < deadline:
            if probe_host("127.0.0.1", hp):
                break
            time.sleep(3)
        else:
            raise RuntimeError("服务探测无响应")
    except Exception as e:
        for n in names:
            kali_exec(f"podman rm -f {n} >/dev/null 2>&1; true", timeout=60)
        kali_exec(f"podman network rm {net} >/dev/null 2>&1; true", timeout=60)
        return False, None, str(e)[:160]
    finally:
        kali_exec(f"rm -rf {remote_dir}", timeout=300)  # 构建目录（含 flag）即删
    return True, hp, ""
