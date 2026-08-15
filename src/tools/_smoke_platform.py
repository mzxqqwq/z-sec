# -*- coding: utf-8 -*-
"""平台级冒烟：CtftinyPlatform(revive=True) 只留 describeme，走完整 list_challenges。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, r"D:\ctf-agent\src\ctf_orchestrator")
from eval_platform import CtftinyPlatform  # noqa: E402

meta = json.loads(Path(r"D:\ctf-agent\benchmarks\ctftiny\ctftiny.json").read_text(encoding="utf-8"))
exclude = [c for c in meta if c != "cry-describeme"]

p = CtftinyPlatform(exclude=exclude, revive=True)
chs = p.list_challenges()
for ch in chs:
    print(f"{ch.challenge_id}: kind={ch.target_kind} host={ch.host} port={ch.port} "
          f"liveness={ch.raw.get('liveness')} revived={ch.raw.get('revived')} "
          f"connection={ch.connection_info}")
    print("  prompt json:", ch.to_prompt_json())
p.close()
