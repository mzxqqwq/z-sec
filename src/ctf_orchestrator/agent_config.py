# -*- coding: utf-8 -*-
"""agent_config.py —— 统一 LLM 配置中心（2026-08-16）。

所有角色的模型配置（strong/weak/planner/observer/digest）+ 运行时开关
集中到 config/agent.json；各 provider 的 API key 存 config/secrets.json（gitignore，
不上传 GitHub）。Web UI「配置」页读写这两个文件（dashboard.py 的 /api/config）。

兼容性：
- 旧 secrets/deepseek.key 仍作 deepseek key 的兜底，存量跑分不受影响；
- 各调用方（planning/digest/workers/编排器）改为从本模块取配置，散落的硬编码
  （PLAN_MODEL / digest.MODEL / L1_CONFIG 等）保留为最终兜底默认值。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

ROOT = Path(r"D:\ctf-agent")
CONFIG_DIR = ROOT / "config"
CONFIG_PATH = CONFIG_DIR / "agent.json"
SECRETS_PATH = CONFIG_DIR / "secrets.json"
LEGACY_DEEPSEEK_KEY = ROOT / "secrets" / "deepseek.key"

DEFAULT_CONFIG: dict[str, Any] = {
    "llm": {
        "strong":   {"model": "deepseek-v4-pro",  "thinking": "medium"},
        "weak":     {"model": "deepseek-v4-flash", "thinking": "low"},
        "planner":  {"model": "deepseek-v4-pro"},
        "observer": {"model": "deepseek-v4-pro",  "thinking": "medium"},
        "digest":   {"model": "deepseek-chat"},
    },
    "runtime": {
        "max_parallel_challenges": 3,
        "planning_enabled": True,
        "supervisor_enabled": True,
        "kb_enabled": False,
    },
    "providers": [
        {"id": "deepseek", "label": "DeepSeek",
         "base_url": "https://api.deepseek.com",
         "api_key_env": "DEEPSEEK_API_KEY",
         "models": ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat"]},
        {"id": "openai", "label": "OpenAI",
         "base_url": "https://api.openai.com/v1",
         "api_key_env": "OPENAI_API_KEY",
         "models": ["gpt-4o"]},
        {"id": "anthropic", "label": "Anthropic",
         "base_url": "https://api.anthropic.com",
         "api_key_env": "ANTHROPIC_API_KEY",
         "models": ["claude-sonnet-4-20250514"]},
    ],
}

_THINKING_CHOICES = ("low", "medium", "high")


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict[str, Any]:
    """读 agent.json（缺则返回默认），深合并默认值保证新字段有兜底。"""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg = _deep_merge(cfg, data)
    except (OSError, json.JSONDecodeError):
        pass
    return cfg


def save(partial: dict[str, Any]) -> dict[str, Any]:
    """合并保存 agent.json，返回合并后的完整配置。"""
    cfg = _deep_merge(load(), partial)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)
    return cfg


def llm(role: str) -> dict[str, str]:
    """角色 → {model, thinking?}。"""
    out = load()["llm"].get(role) or DEFAULT_CONFIG["llm"].get(role) or {}
    return {k: str(v) for k, v in out.items()}


def runtime() -> dict[str, Any]:
    return dict(load().get("runtime") or {})


def providers() -> list[dict[str, Any]]:
    return list(load().get("providers") or [])


def provider_of(model: str) -> Optional[dict[str, Any]]:
    m = (model or "").strip()
    for p in providers():
        if m in (p.get("models") or []):
            return p
    return None


def _load_secrets() -> dict[str, str]:
    try:
        if SECRETS_PATH.exists():
            data = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v).strip() for k, v in data.items() if str(v).strip()}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def secrets_status() -> dict[str, bool]:
    """provider id → 是否已配置 key（不返回 key 本体）。"""
    have = _load_secrets()
    status: dict[str, bool] = {}
    for p in providers():
        pid = p.get("id", "")
        env_name = p.get("api_key_env", "")
        status[pid] = bool(have.get(pid) or os.environ.get(env_name, "").strip())
    return status


def set_secrets(partial: dict[str, str]) -> dict[str, bool]:
    """写入/删除 provider key（空字符串=删除）。返回新的状态。"""
    have = _load_secrets()
    for pid, key in (partial or {}).items():
        key = str(key or "").strip()
        if key:
            have[pid] = key
        else:
            have.pop(pid, None)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SECRETS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(have, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SECRETS_PATH)
    _apply_to_env(have)
    return secrets_status()


def _apply_to_env(have: dict[str, str]) -> None:
    for p in providers():
        key = have.get(p.get("id", ""))
        if key:
            os.environ[str(p.get("api_key_env", ""))] = key


def apply_env() -> None:
    """把 secrets.json 里的 key 注入当前进程环境（worker/digest/planner 共用）。"""
    _apply_to_env(_load_secrets())


def deepseek_api_key() -> str:
    """deepseek key：config/secrets.json > 环境变量 > 旧 secrets/deepseek.key。"""
    have = _load_secrets()
    if have.get("deepseek"):
        return have["deepseek"]
    env = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env:
        return env
    try:
        if LEGACY_DEEPSEEK_KEY.exists():
            return LEGACY_DEEPSEEK_KEY.read_text(encoding="ascii").strip()
    except OSError:
        pass
    return ""


def raw_llm(model: str) -> dict[str, str]:
    """裸调 /chat/completions 用（planner/digest）：{base_url, api_key}。"""
    p = provider_of(model) or {}
    return {
        "base_url": str(p.get("base_url") or "https://api.deepseek.com"),
        "api_key": deepseek_api_key() if p.get("id") == "deepseek" or not p
        else os.environ.get(str(p.get("api_key_env", "")), ""),
    }


def build_model_config() -> dict[str, Any]:
    """编排器/评测入口期望的 model_config 形态（strong/weak/observer/runtime 开关）。"""
    cfg = load()
    return {
        "strong": llm("strong"),
        "weak": llm("weak"),
        "observer": llm("observer"),
        **{k: v for k, v in runtime().items()},
    }


def all_models() -> list[str]:
    out: list[str] = []
    for p in providers():
        for m in p.get("models") or []:
            if m not in out:
                out.append(m)
    return out


def validate(partial: dict[str, Any]) -> list[str]:
    """校验 UI 提交的配置，返回错误列表（空 = 通过）。"""
    errors: list[str] = []
    if "llm" in partial:
        for role, v in (partial["llm"] or {}).items():
            if not isinstance(v, dict) or not v.get("model"):
                errors.append(f"llm.{role} 缺少 model")
            elif v.get("model") not in all_models():
                errors.append(f"llm.{role}.model「{v.get('model')}」不在 providers.models 里")
            if role in ("strong", "weak", "observer"):
                if v.get("thinking") not in _THINKING_CHOICES:
                    errors.append(f"llm.{role}.thinking 必须是 {_THINKING_CHOICES}")
    if "runtime" in partial:
        mp = (partial["runtime"] or {}).get("max_parallel_challenges")
        if mp is not None and (not isinstance(mp, int) or not (1 <= mp <= 8)):
            errors.append("runtime.max_parallel_challenges 必须是 1-8 的整数")
    return errors
