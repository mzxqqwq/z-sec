# -*- coding: utf-8 -*-
"""检查：声明了 box 但匹配不到 manifest 的服务题（复活盲区清单）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, r"D:\ctf-agent\src\ctf_orchestrator")
from revival import ServiceReviver  # noqa: E402

r = ServiceReviver(enabled=False)
blind = []
total_svc = 0
for root, meta_name in ((Path(r"D:\ctf-agent\benchmarks\ctftiny"), "ctftiny.json"),
                        (Path(r"D:\ctf-agent\benchmarks\nyu-ctf-bench"), "test_dataset.json"),
                        (Path(r"D:\ctf-agent\benchmarks\nyu-ctf-bench"), "development_dataset.json")):
    p = root / meta_name
    if not p.exists():
        continue
    d = json.loads(p.read_text(encoding="utf-8"))
    for cid, e in d.items():
        path = e.get("path") or ""
        # 读 challenge.json 判断是否服务题
        cj = root / path / "challenge.json" if path else None
        box = ""
        if cj is not None and cj.exists():
            try:
                box = str(json.loads(cj.read_text(encoding="utf-8")).get("box") or "")
            except Exception:
                box = ""
        if box:
            total_svc += 1
            if r.match(path) is None:
                blind.append((cid, path, box))

print(f"服务题总数（box 非空）: {total_svc}")
print(f"匹配不到 compose 的: {len(blind)}")
for cid, path, box in blind:
    print(f"  {cid}  {path}  box={box}")
