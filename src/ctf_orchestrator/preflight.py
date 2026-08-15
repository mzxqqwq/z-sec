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
    # 1. DeepSeek key
    key_file = ROOT / "secrets" / "deepseek.key"
    check("DeepSeek key", key_file.exists() and key_file.read_text(encoding="ascii").strip().startswith("sk-"))

    # 2. pi CLI
    pi_cli = ROOT / "pi-mono" / "packages" / "coding-agent" / "dist" / "cli.js"
    check("pi CLI 已构建", pi_cli.exists())

    # 3. Kali API
    try:
        r = requests.get("http://10.174.153.128:5000/health", timeout=8)
        check("Kali API", r.status_code == 200 and "healthy" in r.text, r.text[:60])
    except Exception as e:
        check("Kali API", False, str(e))

    # 4. Kali 关键工具
    try:
        r = requests.post("http://10.174.153.128:5000/api/command",
                          json={"command": "python3 -c 'import pwn,z3,angr,sympy; print(\"ok\")' 2>&1 | tail -1"},
                          timeout=30)
        check("Kali CTF 工具链", "ok" in r.json().get("stdout", ""))
    except Exception as e:
        check("Kali CTF 工具链", False, str(e))

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

    # 8. 孤儿 worker
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -match 'cli.js' }).Count"],
                             capture_output=True, text=True, timeout=30)
        n = int(out.stdout.strip() or 0)
        check("无孤儿 worker", n == 0, f"{n} 个")
    except Exception as e:
        check("无孤儿 worker", False, str(e))

    # 9. 环境变量提示
    has_url = bool(os.environ.get("DASCTF_BASE_URL"))
    has_user = bool(os.environ.get("DASCTF_USERNAME"))
    has_pwd = bool(os.environ.get("DASCTF_PASSWORD"))
    check("平台凭证 env", has_url and has_user and has_pwd,
          f"BASE={'Y' if has_url else 'N'} USER={'Y' if has_user else 'N'} PWD={'Y' if has_pwd else 'N'}（测试赛当天设置）")

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
