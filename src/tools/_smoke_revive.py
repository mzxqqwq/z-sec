# -*- coding: utf-8 -*-
"""端到端冒烟：revive(cry-describeme) → 探测 → 清理。真实验证 Kali podman 路径。"""
import sys
from pathlib import Path

sys.path.insert(0, r"D:\ctf-agent\src\ctf_orchestrator")
from revival import ServiceReviver  # noqa: E402

r = ServiceReviver(enabled=True)
ok, port, err = r.revive("cry-describeme", "ctftiny/cry/DescribeMe", 21200)
print(f"revive ok={ok} port={port} err={err}")
if ok:
    # 用 worker 的同一通道从 Kali 侧连一次，证明服务真的可交互
    from workers import kali_exec
    out = kali_exec("timeout 8 bash -c 'echo | timeout 5 nc 127.0.0.1 %d | head -c 200'" % port,
                    timeout=20)
    print("nc output:", repr(out.get("stdout") or "")[:200])
    n = r.stop_all()
    print("stopped:", n)
