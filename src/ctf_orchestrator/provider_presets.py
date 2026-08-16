# -*- coding: utf-8 -*-
"""provider_presets.py —— 供应商预设与模型元数据目录（移植自 cc-switch）。

cc-switch（github.com/farion1231/cc-switch，v3.18.0）的 piProviderPresets /
piModelCatalog 是人工审核过的模型元数据（reasoning/contextWindow/maxTokens），
本模块移植了其中常用条目（约 43 条，来源 src/config/piModelCatalog.ts），
用于：
- 配置页「从预设添加」中转站模板（url 与推荐模型预填，用户只填 key）；
- 同步 pi 模型注册表时给出正确的模型元数据（替代按名字猜 reasoning 的旧逻辑）；
- 「一键应用到角色」按 reasoning 属性给 strong/weak/digest 分配合适模型。

模型 id 匹配支持前缀形式（"openai/gpt-5.2"）与裸 id（"gpt-5.2"）后缀匹配，
因为中转站通常暴露裸 id。
"""
from __future__ import annotations

from typing import Any, Optional

# 人工审核元数据（cc-switch piModelCatalog.ts 移植子集，2026-08-16）
MODEL_CATALOG: dict[str, dict[str, Any]] = {
    "anthropic/claude-fable-5": {"name": "Claude Fable 5", "reasoning": True, "contextWindow": 1000000, "maxTokens": 128000},
    "anthropic/claude-haiku-4.5": {"name": "Claude Haiku 4.5", "reasoning": True, "contextWindow": 200000, "maxTokens": 64000},
    "anthropic/claude-opus-4.6": {"name": "Claude Opus 4.6", "reasoning": True, "contextWindow": 1000000, "maxTokens": 128000},
    "anthropic/claude-opus-4.7": {"name": "Claude Opus 4.7", "reasoning": True, "contextWindow": 1000000, "maxTokens": 128000},
    "anthropic/claude-opus-4.8": {"name": "Claude Opus 4.8", "reasoning": True, "contextWindow": 1000000, "maxTokens": 128000},
    "anthropic/claude-opus-5": {"name": "Claude Opus 5", "reasoning": True, "contextWindow": 1000000, "maxTokens": 128000},
    "anthropic/claude-sonnet-4.6": {"name": "Claude Sonnet 4.6", "reasoning": True, "contextWindow": 1000000, "maxTokens": 128000},
    "anthropic/claude-sonnet-5": {"name": "Claude Sonnet 5", "reasoning": True, "contextWindow": 1000000, "maxTokens": 128000},
    "deepseek/deepseek-r1": {"name": "DeepSeek R1", "reasoning": True, "contextWindow": 128000, "maxTokens": 32768},
    "deepseek/deepseek-v4-flash": {"name": "DeepSeek V4 Flash", "reasoning": True, "contextWindow": 1000000, "maxTokens": 384000},
    "deepseek/deepseek-v4-pro": {"name": "DeepSeek V4 Pro", "reasoning": True, "contextWindow": 1000000, "maxTokens": 384000},
    "google/gemini-2.5-flash": {"name": "Gemini 2.5 Flash", "reasoning": True, "contextWindow": 1048576, "maxTokens": 65536},
    "google/gemini-2.5-pro": {"name": "Gemini 2.5 Pro", "reasoning": True, "contextWindow": 1048576, "maxTokens": 65536},
    "google/gemini-3.1-pro-preview": {"name": "Gemini 3.1 Pro Preview", "reasoning": True, "contextWindow": 1048576, "maxTokens": 65536},
    "google/gemini-3.5-flash": {"name": "Gemini 3.5 Flash", "reasoning": True, "contextWindow": 1048576, "maxTokens": 65536},
    "google/gemini-3.6-flash": {"name": "Gemini 3.6 Flash", "reasoning": True, "contextWindow": 1048576, "maxTokens": 65536},
    "minimax/minimax-m2.7": {"name": "MiniMax-M2.7", "reasoning": True, "contextWindow": 204800, "maxTokens": 131072},
    "minimax/minimax-m3": {"name": "MiniMax-M3", "reasoning": True, "contextWindow": 1000000, "maxTokens": 128000},
    "moonshotai/kimi-k2.5": {"name": "Kimi K2.5", "reasoning": True, "contextWindow": 262144, "maxTokens": 262144},
    "moonshotai/kimi-k2.6": {"name": "Kimi K2.6", "reasoning": True, "contextWindow": 262144, "maxTokens": 262144},
    "moonshotai/kimi-k2.7-code": {"name": "Kimi K2.7 Code", "reasoning": True, "contextWindow": 262144, "maxTokens": 262144},
    "moonshotai/kimi-k3": {"name": "Kimi K3", "reasoning": True, "contextWindow": 1048576, "maxTokens": 131072},
    "openai/gpt-5": {"name": "GPT-5", "reasoning": True, "contextWindow": 400000, "maxTokens": 128000},
    "openai/gpt-5-mini": {"name": "GPT-5 Mini", "reasoning": True, "contextWindow": 400000, "maxTokens": 128000},
    "openai/gpt-5.1": {"name": "GPT-5.1", "reasoning": True, "contextWindow": 400000, "maxTokens": 128000},
    "openai/gpt-5.2": {"name": "GPT-5.2", "reasoning": True, "contextWindow": 400000, "maxTokens": 128000},
    "openai/gpt-5.2-codex": {"name": "GPT-5.2 Codex", "reasoning": True, "contextWindow": 400000, "maxTokens": 128000},
    "openai/gpt-5.3-codex": {"name": "GPT-5.3 Codex", "reasoning": True, "contextWindow": 400000, "maxTokens": 128000},
    "openai/gpt-5.3-codex-spark": {"name": "GPT-5.3 Codex Spark", "reasoning": True, "contextWindow": 128000, "maxTokens": 32000},
    "openai/gpt-5.4": {"name": "GPT-5.4", "reasoning": True, "contextWindow": 272000, "maxTokens": 128000},
    "openai/gpt-5.4-mini": {"name": "GPT-5.4 mini", "reasoning": True, "contextWindow": 400000, "maxTokens": 128000},
    "openai/gpt-5.5": {"name": "GPT-5.5", "reasoning": True, "contextWindow": 272000, "maxTokens": 128000},
    "openai/gpt-5.6-luna": {"name": "GPT-5.6 Luna", "reasoning": True, "contextWindow": 272000, "maxTokens": 128000},
    "openai/gpt-5.6-sol": {"name": "GPT-5.6 Sol", "reasoning": True, "contextWindow": 272000, "maxTokens": 128000},
    "openai/gpt-5.6-terra": {"name": "GPT-5.6 Terra", "reasoning": True, "contextWindow": 272000, "maxTokens": 128000},
    "openai/o3": {"name": "o3", "reasoning": True, "contextWindow": 200000, "maxTokens": 100000},
    "openai/o4-mini": {"name": "o4-mini", "reasoning": True, "contextWindow": 200000, "maxTokens": 100000},
    "volcengine/doubao-seed-2.1-pro": {"name": "Doubao Seed 2.1 Pro", "reasoning": True, "contextWindow": 128000, "maxTokens": 16384},
    "xai/grok-4.3": {"name": "Grok 4.3", "reasoning": True, "contextWindow": 1000000, "maxTokens": 30000},
    "xai/grok-4.5": {"name": "Grok 4.5", "reasoning": True, "contextWindow": 500000, "maxTokens": 500000},
    "zai/glm-5.1": {"name": "GLM-5.1", "reasoning": True, "contextWindow": 200000, "maxTokens": 131072},
    "zai/glm-5.2": {"name": "GLM-5.2", "reasoning": True, "contextWindow": 1000000, "maxTokens": 131072},
}

# 中转站模板（用户只填 url + key；模型清单是目录里有元数据的常用裸 id）
GATEWAY_PRESETS: list[dict[str, Any]] = [
    {
        "id": "newapi",
        "name": "中转站/NewAPI 网关",
        "base_url": "",
        "models": ["gpt-5.6-sol", "gpt-5.4-mini", "claude-sonnet-4.6", "claude-haiku-4.5",
                   "gemini-3.5-flash", "deepseek-v4-pro", "deepseek-v4-flash",
                   "kimi-k3", "kimi-k2.7-code", "glm-5.2", "grok-4.5",
                   "doubao-seed-2.1-pro", "minimax-m3"],
    },
    {
        "id": "custom-gateway",
        "name": "自定义网关（空白模板）",
        "base_url": "",
        "models": [],
    },
]


def catalog_meta(model_id: str) -> Optional[dict[str, Any]]:
    """按 id 或 'provider/id' 后缀匹配目录条目，返回 {name, reasoning, contextWindow, maxTokens}。"""
    mid = (model_id or "").strip()
    if not mid:
        return None
    if mid in MODEL_CATALOG:
        return dict(MODEL_CATALOG[mid])
    suffix = f"/{mid}"
    for k, v in MODEL_CATALOG.items():
        if k.endswith(suffix):
            return dict(v)
    return None


def suggest_roles(models: list[str]) -> dict[str, str]:
    """cc-switch switch() 语义的简化：从 provider 模型清单给五个角色挑模型。

    strong/planner/observer → 第一个 reasoning 模型（没有则第一个）；
    weak → 第一个模型；digest → 第一个模型。
    用户可在角色卡片里再微调。
    """
    mids = [str(m).strip() for m in (models or []) if str(m).strip()]
    if not mids:
        return {}
    reasoning = [m for m in mids if catalog_meta(m) and catalog_meta(m)["reasoning"]]
    heavy = reasoning[0] if reasoning else mids[0]
    return {"strong": heavy, "weak": mids[0], "planner": heavy,
            "observer": heavy, "digest": mids[0]}
