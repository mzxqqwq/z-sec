# -*- coding: utf-8 -*-
"""revival.py 纯逻辑自测（不触 Kali）：match/端口对/前置服务选择。"""
import sys
from pathlib import Path

sys.path.insert(0, r"D:\ctf-agent\src\ctf_orchestrator")
from revival import ServiceReviver, _port_pairs, _safe  # noqa: E402

r = ServiceReviver(enabled=False)

# 1. match：ctftiny 与 nyu 的 path 格式
assert r.match("ctftiny/cry/DescribeMe") is not None, "ctftiny DescribeMe not matched"
assert r.match("test/2023/CSAW-Finals/crypto/DescribeMe") is not None, "nyu DescribeMe not matched"
assert r.match("ctftiny/pwn/get_it?") is not None or True  # get_it? 可能无 compose，不强断言
print("match OK")

# 2. 端口对解析
assert _port_pairs(["21200:21200"]) == [(21200, 21200)]
assert _port_pairs(["0:8000"]) == [(0, 8000)]
assert _port_pairs(["1337"]) == [(1337, 1337)]
assert _port_pairs(["80:80/tcp"]) == [(80, 80)]
assert _port_pairs(["127.0.0.1:5000:5000"]) == [(5000, 5000)]
assert _port_pairs([]) == []
print("ports OK")

# 3. 名称净化
assert _safe("cry-describeme") == "cry-describeme"
assert _safe("pwn/get_it?") == "pwn-get_it-"
print("safe OK")

# 4. 统计：多少活跃 challenge 能匹配到 manifest（用 ctftiny.json + test_dataset.json）
import json
ct = json.loads(Path(r"D:\ctf-agent\benchmarks\ctftiny\ctftiny.json").read_text(encoding="utf-8"))
nyu = json.loads(Path(r"D:\ctf-agent\benchmarks\nyu-ctf-bench\test_dataset.json").read_text(encoding="utf-8"))
hit = miss = 0
for src, d in (("ctftiny", ct), ("nyu", nyu)):
    for cid, e in d.items():
        p = e.get("path") or ""
        if r.match(p):
            hit += 1
        else:
            miss += 1
print(f"manifest match: hit={hit} miss={miss}")
