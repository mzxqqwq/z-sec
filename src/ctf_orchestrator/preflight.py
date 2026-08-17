#!/usr/bin/env python3
"""
preflight.py —— 赛前体检（8/18 测试赛早晨跑一次，全绿才能开赛）

检查项：DeepSeek key、pi CLI、Kali API、mock 平台（可选）、看板端口、
孤儿 worker、技能包、平台客户端导入、workspace 可写。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import requests

ROOT = Path(r"D:\ctf-agent")
CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    print(f"[{'OK' if ok else 'FAIL'}] {name} {detail}")


def main() -> int:
    # Kali 检查统一走 SSH 直连（2026-08-16 起弃 REST，与 worker 同通道）
    from workers import kali_exec, kali_healthy_gate

    # 1. DeepSeek key
    key_file = ROOT / "secrets" / "deepseek.key"
    check("DeepSeek key", key_file.exists() and key_file.read_text(encoding="ascii").strip().startswith("sk-"))

    # 2. pi CLI
    pi_cli = ROOT / "pi-mono" / "packages" / "coding-agent" / "dist" / "cli.js"
    check("pi CLI 已构建", pi_cli.exists())

    # 3. Kali SSH 连通性（2026-08-16 起弃 REST，与 worker 同通道）
    try:
        ok, detail = kali_healthy_gate()
        check("Kali SSH", ok, detail[:60])
    except Exception as e:
        check("Kali SSH", False, str(e))

    # 4. Kali 关键工具
    try:
        r = kali_exec("python3 -c 'import pwn,z3,angr,sympy; print(\"ok\")' 2>&1 | tail -1",
                      timeout=60)
        check("Kali CTF 工具链", "ok" in r.get("stdout", ""))
    except Exception as e:
        check("Kali CTF 工具链", False, str(e))

    # 4b. SageMath（podman 容器包装 /usr/local/bin/sage；worker 直敲 sage xxx.sage）
    try:
        r = kali_exec("mkdir -p /root/ctf/preflight && "
                      "printf 'print(factor(15))\\n' > /root/ctf/preflight/t.sage && "
                      "cd /root/ctf/preflight && sage t.sage 2>&1 | tail -1", timeout=120)
        check("Kali SageMath", "3 * 5" in r.get("stdout", ""),
              (r.get("stdout", "") or "")[-60:])
    except Exception as e:
        check("Kali SageMath", False, str(e))

    # 4c. pwndbg
    try:
        r = kali_exec("gdb -q -batch -ex 'quit' 2>&1 | grep -ci pwndbg", timeout=60)
        check("Kali pwndbg", int((r.get("stdout", "") or "0").strip() or 0) > 0)
    except Exception as e:
        check("Kali pwndbg", False, str(e))

    # 4d. podman（SageMath 包装 + benchmark 服务题容器运行依赖）
    try:
        r = kali_exec("podman --version 2>&1 | head -1", timeout=60)
        check("Kali podman", "podman version" in r.get("stdout", ""))
    except Exception as e:
        check("Kali podman", False, str(e))

    # 5. 技能包
    skills = Path.home() / ".pi" / "agent" / "skills"
    n = len([d for d in skills.iterdir() if d.is_dir()]) if skills.exists() else 0
    check("技能包", n >= 5, f"{n} 个")

    # 6. 平台客户端可导入
    try:
        sys.path.insert(0, str(ROOT / "src" / "dasctf_client"))
        import dasctf_client  # noqa: F401
        check("平台客户端导入", True)
    except Exception as e:
        check("平台客户端导入", False, str(e))

    # 7. 编排器可导入
    try:
        sys.path.insert(0, str(ROOT / "src" / "ctf_orchestrator"))
        import ctf_orchestrator  # noqa: F401
        check("编排器导入", True)
    except Exception as e:
        check("编排器导入", False, str(e))

    # 8. 孤儿 worker（只数我们自己的 pi worker：命令行含 coding-agent\cli.js；
    #    不匹配 npx/@playwright/mcp 等外部 node 进程——2026-08-17 误报实锤）
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -match 'coding-agent' }).Count"],
                             capture_output=True, text=True, timeout=30)
        n = int(out.stdout.strip() or 0)
        check("无孤儿 worker", n == 0, f"{n} 个")
    except Exception as e:
        check("无孤儿 worker", False, str(e))

    # 9. 平台凭证（config/secrets.json dasctf 段，env 兜底；BASE 必须指向白名单域）
    try:
        sys.path.insert(0, str(ROOT / "src" / "dasctf_client"))
        from dasctf_client import load_dasctf_credentials
        creds = load_dasctf_credentials()
        base_ok = str(creds.get("base_url", "")).startswith("https://gcsis.dasctf.com")
        check("平台凭证", base_ok and bool(creds.get("username")) and bool(creds.get("password")),
              f"BASE={'Y' if base_ok else 'N'} "
              f"USER={'Y' if creds.get('username') else 'N'} "
              f"PWD={'Y' if creds.get('password') else 'N'}（config/secrets.json dasctf 段，env 兜底）")
    except Exception as e:
        check("平台凭证", False, str(e))

    # 10. workspace 可写
    ws = ROOT / "workspace"
    try:
        ws.mkdir(parents=True, exist_ok=True)
        (ws / ".write-test").write_text("ok", encoding="utf-8")
        (ws / ".write-test").unlink()
        check("workspace 可写", True)
    except Exception as e:
        check("workspace 可写", False, str(e))

    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{'=' * 40}\n结论: {len(CHECKS) - len(failed)}/{len(CHECKS)} 通过")
    if failed:
        print("失败项:", ", ".join(c[0] for c in failed))
        return 1
    print("全绿，可以开赛 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
