"""规划器（planning 阶段）——用便宜模型在派 worker 前生成解题计划。

设计参照 D-CIPHER 的 Planner 思想（arXiv 2502.10931）自研实现：
一次轻量 LLM 调用，输出 3-6 步 JSON 计划，注入 worker 提示词。
任何失败都优雅降级为 None（无计划 worker 照样能解）。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import requests

PLAN_PROMPT = """\
你是 CTF 解题规划师。阅读下面的题目信息，给出一个 3-6 步的解题计划。
每步要具体可执行（用什么工具、试什么攻击、怎么验证）。
只输出 JSON：{{"steps": ["步骤1", "步骤2", ...]}}

题目信息：
{challenge_json}
"""

PLAN_MODEL = "deepseek-chat"
PLAN_MAX_TOKENS = 700
PLAN_TIMEOUT = 90  # 规划器不允许拖时间


class Planner:
    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com",
                 enabled: bool = True) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.enabled = enabled
        self._cache: dict[str, Optional[str]] = {}

    @staticmethod
    def load_key_from_secrets(path: str = r"D:\ctf-agent\secrets\deepseek.key") -> str:
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
            "model": PLAN_MODEL,
            "messages": [{"role": "user", "content": PLAN_PROMPT.format(challenge_json=challenge_json)}],
            "max_tokens": PLAN_MAX_TOKENS,
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        }
        try:
            r = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body, timeout=PLAN_TIMEOUT,
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
            steps = data.get("steps")
            if isinstance(steps, list) and steps:
                lines = [f"{i+1}. {s}" for i, s in enumerate(steps[:6]) if isinstance(s, str)]
                return "\n".join(lines)
        except Exception as e:
            print(f"[planner] failed: {e}")
        return None
