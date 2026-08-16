# -*- coding: utf-8 -*-
"""传送 showdown 构建上下文到 Kali 并 podman build（构建完立刻删除含 flag 的临时目录）。"""
import base64
import sys
from pathlib import Path

sys.path.insert(0, r"D:\ctf-agent\src\ctf_orchestrator")
from workers import kali_exec

TGZ = Path(r"D:\ctf-agent\tmp\showdown-build.tgz")
b64 = base64.b64encode(TGZ.read_bytes()).decode()
print(f"tgz {TGZ.stat().st_size} bytes, b64 {len(b64)} chars")

r = kali_exec(f"echo {b64} | base64 -d > /tmp/showdown-build.tgz && "
              f"rm -rf /tmp/showdown-build && mkdir -p /tmp/showdown-build && "
              f"tar -xzf /tmp/showdown-build.tgz -C /tmp/showdown-build --strip-components=1 && "
              f"ls /tmp/showdown-build", timeout=60)
print("transfer:", r.get("success"), "|", r.get("stdout", "").strip().splitlines()[-6:])
print("stderr:", r.get("stderr", "")[:200])

# build（ubuntu:18.04 拉取 + apt 装 sudo，VPN 开着；约 3-6 分钟）
r = kali_exec("cd /tmp/showdown-build && podman build -t docker.io/llmctf/2018f-msc-showdown-container:latest . 2>&1 | tail -5",
              timeout=900)
print("build tail:", r.get("stdout", "")[-600:])

# 清掉含 flag 的构建目录
r = kali_exec("rm -rf /tmp/showdown-build /tmp/showdown-build.tgz; "
              "podman image exists docker.io/llmctf/2018f-msc-showdown-container:latest && echo EXISTS || echo MISSING",
              timeout=60)
print("cleanup:", r.get("stdout", "").strip())
