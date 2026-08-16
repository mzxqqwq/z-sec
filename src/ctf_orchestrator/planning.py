"""规划器（planning 阶段）——强模型在派 worker 前给出解题总体思路。

定版（2026-08-16）：只出总体思路（核心方向 + 2-3 个关键要点），不列死板工具步骤；
模型用强模型（deepseek-v4-pro）；无质量门禁、无失败重规划（纠偏交给 Supervisor 与人工 hint）。
设计参照 D-CIPHER Planner 思想 + Koshary"plan 复用求解模型"实践（orchestrator.py:677-708）。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import requests

PLAN_PROMPT = """\
你是 CTF 解题规划师。阅读下面的题目信息，给出解题的总体思路。

要求：
- 指出核心解题方向（这道题最可能的突破口）；
- 给出路径上最关键 2-3 个要点或易错点；
- 不要罗列死板的工具步骤——解题是动态尝试的过程，worker 会边做边调整。

直接输出 3-6 行中文思路文本，不要输出 JSON，不要复述题目。

题目信息：
{challenge_json}
"""

PLAN_MODEL = "deepseek-v4-pro"  # 定版：强模型（pi 内置 deepseek provider 同款 id，直连 api.deepseek.com）
PLAN_MAX_TOKENS = 700
PLAN_TIMEOUT = 90  # 规划器不允许拖时间


class Planner:
    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com",
                 model: str = PLAN_MODEL, enabled: bool = True) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.enabled = enabled
        self._cache: dict[str, Optional[str]] = {}

    @staticmethod
    def load_key_from_secrets(path: str = r"D:\ctf-agent\secrets\deepseek.key") -> str:
        try:
            import agent_config  # 统一配置中心优先（Web UI 可设 key）
            key = agent_config.deepseek_api_key()
            if key:
                return key
        except Exception:
            pass
        return Path(path).read_text(encoding="ascii").strip()

    def plan(self, cid: str, challenge_json: str, attempt: int) -> Optional[str]:
        if not self.enabled:
            return None
        cache_key = f"{cid}#{attempt}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        plan = self._call(challenge_json)
        self._cache[cache_key] = plan
        return plan

    def _call(self, challenge_json: str) -> Optional[str]:
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": PLAN_PROMPT.format(challenge_json=challenge_json)}],
            "max_tokens": PLAN_MAX_TOKENS,
            "temperature": 0.3,
        }
        try:
            r = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body, timeout=PLAN_TIMEOUT,
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            text = content.strip()
            # 思路型输出直接采用；长度兜底截断
            if len(text) > 1200:
                text = text[:1200]
            return text or None
        except Exception as e:
            print(f"[planner] failed: {e}")
        return None
