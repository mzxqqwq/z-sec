#!/usr/bin/env python3
"""
DASCTF 竞赛平台 API 客户端（骨架版 v0.1）

目标赛事：第九届西湖论剑「AI Agent 解题夺旗」（game.gcsis.cn，仅开放 API）。
设计：独立实现（cookie jar 持久化 + captcha 钩子 + 加密钩子 + 提交冷却等标准做法）。

注意：比赛平台真实 API 路径要到 8/18 测试赛才能确认。
本骨架把"确认后要改的点"全部集中在 _EP 数据类与 resolve_* 方法里，
其余逻辑（会话、重试、限频、flag 检测、提交队列）与平台无关。

用法：
    python dasctf_client.py login && python dasctf_client.py challenges
    python dasctf_client.py submit --challenge 1 --flag 'DASCTF{test}'

账号来源（env > config/secrets.json dasctf 段，secrets.json 不入库）：
    config/secrets.json: { "dasctf": { "base_url": ..., "username": ..., "password": ... } }
    或环境变量 DASCTF_BASE_URL / DASCTF_USERNAME / DASCTF_PASSWORD 兜底。
平台：第九届西湖论剑 AI 赛道 gcsis.dasctf.com。
依赖：requests（本机已装）。仅标准库 + requests。
"""
from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import requests


# ---------------------------------------------------------------------------
# 平台端点（测试赛确认后集中修改这里）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EP:
    """真实 API 路径占位。8/18 测试赛抓包后替换。"""
    config: str = "/api/config"
    login: str = "/api/login"
    whoami: str = "/api/user"
    challenges: str = "/api/challenges"
    challenge_detail: str = "/api/challenges/{id}"
    submit: str = "/api/challenges/{id}/submit"
    scoreboard: str = "/api/scoreboard"
    attachment: str = "/api/challenges/{id}/attachment"


# 常见 flag 形态（DASCTF 历史格式以 DASCTF{...}/flag{...} 为主）
FLAG_PATTERNS: list[re.Pattern] = [
    re.compile(rb"flag\{[0-9a-zA-Z_\-!@#$%^&*]{4,64}\}", re.I),
    re.compile(rb"dasctf\{[0-9a-zA-Z_\-!@#$%^&*]{4,64}\}", re.I),
    re.compile(rb"ctf\{[0-9a-zA-Z_\-!@#$%^&*]{4,64}\}", re.I),
    re.compile(rb"[0-9a-f]{32}", re.I),  # md5 形态兜底
]


# ---------------------------------------------------------------------------
# 平台账号来源（env > config/secrets.json dasctf 段；secrets.json gitignore）
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
SECRETS_PATH = ROOT / "config" / "secrets.json"


def load_dasctf_credentials() -> dict[str, str]:
    """返回 {base_url, username, password}。env 优先，其次 secrets.json dasctf 段。"""
    env = {
        "base_url": os.environ.get("DASCTF_BASE_URL", "").strip(),
        "username": os.environ.get("DASCTF_USERNAME", "").strip(),
        "password": os.environ.get("DASCTF_PASSWORD", "").strip(),
    }
    file_creds: dict[str, str] = {}
    try:
        if SECRETS_PATH.exists():
            data = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
            d = data.get("dasctf")
            if isinstance(d, dict):
                file_creds = {str(k): str(v).strip() for k, v in d.items() if str(v).strip()}
    except (OSError, json.JSONDecodeError):
        pass
    return {
        "base_url": env["base_url"] or file_creds.get("base_url", "https://gcsis.dasctf.com"),
        "username": env["username"] or file_creds.get("username", ""),
        "password": env["password"] or file_creds.get("password", ""),
    }


class ClientError(RuntimeError):
    pass


class CaptchaRequired(ClientError):
    """平台要求验证码/风控，需要人工介入或接入解题器。"""


class RateLimited(ClientError):
    """被限频。携带 retry_after 秒数。"""

    def __init__(self, retry_after: float = 5.0):
        super().__init__(f"rate limited, retry after {retry_after:.1f}s")
        self.retry_after = retry_after


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------
@dataclass
class DasctfClient:
    base_url: str
    cookie_jar_path: Path = field(default_factory=lambda: Path.home() / ".cache" / "dasctf_agent" / "cookies.txt")
    session: requests.Session = field(default_factory=requests.Session, repr=False)
    # 提交纪律：每题最多错误提交次数、两次提交最小间隔（防封号）
    max_wrong_submits: int = 3
    min_submit_interval: float = 5.0
    _wrong_submits: dict[int, int] = field(default_factory=dict, repr=False)
    _last_submit: float = 0.0
    _challenge_cache: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self.session.headers.update({"User-Agent": "dasctf-agent/0.1", "Accept": "application/json"})
        self.cookie_jar_path.parent.mkdir(parents=True, exist_ok=True)
        jar = http.cookiejar.MozillaCookieJar(str(self.cookie_jar_path))
        if self.cookie_jar_path.exists():
            try:
                jar.load(ignore_discard=True, ignore_expires=True)
            except Exception:
                pass
        self.session.cookies = jar  # type: ignore[assignment]

    def _save_cookies(self) -> None:
        self.session.cookies.save(ignore_discard=True, ignore_expires=True)  # type: ignore[attr-defined]

    # ---- 底层请求 ----
    def _request(self, method: str, path: str, *, json_body: Optional[dict] = None,
                 params: Optional[dict] = None, _retries: int = 3) -> Any:
        url = self.base_url + path
        for attempt in range(_retries):
            try:
                resp = self.session.request(method, url, json=json_body, params=params, timeout=30)
            except requests.RequestException as e:
                if attempt == _retries - 1:
                    raise ClientError(f"request failed: {e}") from e
                time.sleep(2 ** attempt)
                continue
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", 5))
                if attempt == _retries - 1:
                    raise RateLimited(retry_after)
                time.sleep(retry_after)
                continue
            if resp.status_code in (401, 403):
                raise ClientError(f"auth rejected ({resp.status_code}): {resp.text[:200]}")
            if resp.status_code >= 400:
                raise ClientError(f"http {resp.status_code}: {resp.text[:300]}")
            self._save_cookies()
            try:
                return resp.json()
            except ValueError:
                return {"_raw": resp.text}
        raise ClientError("unreachable")

    # ---- 平台能力探测（测试赛先跑这个）----
    def probe(self) -> dict[str, Any]:
        """探测平台配置：是否要验证码、密码是否要加密、API 结构。"""
        data = self._request("GET", EP.config)
        return {"captcha": bool(data.get("captcha")), "encrypt": bool(data.get("apiPublicKey")), "_raw_keys": sorted(data.keys())}

    # ---- 登录 ----
    def login(self, username: str, password: str) -> bool:
        body: dict[str, str] = {"username": username, "password": password}
        data = self._request("POST", EP.login, json_body=body)
        if isinstance(data, dict) and (data.get("code") in (401, 403) or "captcha" in json.dumps(data).lower()):
            raise CaptchaRequired()
        return True

    def whoami(self) -> dict[str, Any]:
        return self._request("GET", EP.whoami)

    # ---- 题目 ----
    def challenges(self) -> list[dict[str, Any]]:
        data = self._request("GET", EP.challenges)
        if isinstance(data, dict):
            for key in ("challenges", "data", "list", "games"):
                if isinstance(data.get(key), list):
                    return data[key]
        if isinstance(data, list):
            return data
        raise ClientError(f"unrecognized challenge list shape: {str(data)[:200]}")

    def challenge_detail(self, challenge_id: str | int) -> dict[str, Any]:
        key = str(challenge_id)
        if key not in self._challenge_cache:
            self._challenge_cache[key] = self._request("GET", EP.challenge_detail.format(id=challenge_id))
        return self._challenge_cache[key]

    def download_attachment(self, challenge_id: str | int, dest_dir: Path) -> Optional[Path]:
        url = self.base_url + EP.attachment.format(id=challenge_id)
        resp = self.session.get(url, timeout=60, allow_redirects=True)
        if resp.status_code != 200:
            return None
        dest_dir.mkdir(parents=True, exist_ok=True)
        cd = resp.headers.get("Content-Disposition", "")
        m = re.search(r'filename="?([^";]+)"?', cd)
        name = m.group(1) if m else f"challenge_{challenge_id}.bin"
        path = dest_dir / name
        path.write_bytes(resp.content)
        return path

    # ---- 交 flag ----
    def submit(self, challenge_id: str | int, flag: str) -> dict[str, Any]:
        now = time.time()
        if now - self._last_submit < self.min_submit_interval:
            time.sleep(self.min_submit_interval - (now - self._last_submit))
        wrong = self._wrong_submits.get(int(challenge_id), 0)
        if wrong >= self.max_wrong_submits:
            raise ClientError(f"wrong-submit budget exhausted for challenge {challenge_id}")
        data = self._request("POST", EP.submit.format(id=challenge_id), json_body={"flag": flag})
        self._last_submit = time.time()
        if isinstance(data, dict) and data.get("correct") in (False, "false", 0):
            self._wrong_submits[int(challenge_id)] = wrong + 1
        return data

    def scoreboard(self) -> Any:
        return self._request("GET", EP.scoreboard)


# ---------------------------------------------------------------------------
# flag 检测器（EnIGMA 教训：只信真实执行输出；Koshary 教训：正则+候选值）
# ---------------------------------------------------------------------------
def extract_flags(text: bytes | str) -> list[str]:
    raw = text.encode() if isinstance(text, str) else text
    found: list[str] = []
    for pat in FLAG_PATTERNS:
        for m in pat.finditer(raw):
            found.append(m.group(0).decode(errors="replace"))
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for f in found:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    creds = load_dasctf_credentials()
    p = argparse.ArgumentParser(prog="dasctf_client")
    p.add_argument("--base-url", default=creds["base_url"])
    p.add_argument("--username", default=creds["username"])
    p.add_argument("--password-env", default="DASCTF_PASSWORD", help="env var holding the password")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe")
    sub.add_parser("login")
    sub.add_parser("whoami")
    sub.add_parser("challenges")
    sp = sub.add_parser("submit")
    sp.add_argument("--challenge", required=True)
    sp.add_argument("--flag", required=True)
    sp2 = sub.add_parser("detail")
    sp2.add_argument("--challenge", required=True)
    sp3 = sub.add_parser("attachment")
    sp3.add_argument("--challenge", required=True)
    sp3.add_argument("--dest", default="attachments")
    args = p.parse_args(argv)

    c = DasctfClient(args.base_url)
    if args.cmd == "probe":
        print(json.dumps(c.probe(), ensure_ascii=False, indent=2))
    elif args.cmd == "login":
        password = os.environ.get(args.password_env, "").strip() or creds.get("password", "")
        if not args.username or not password:
            print("error: 缺少账号（--username / 环境变量 / config/secrets.json dasctf 段）", file=sys.stderr)
            return 2
        print("ok" if c.login(args.username, password) else "failed")
    elif args.cmd == "whoami":
        print(json.dumps(c.whoami(), ensure_ascii=False, indent=2))
    elif args.cmd == "challenges":
        for ch in c.challenges():
            print(json.dumps(ch, ensure_ascii=False))
    elif args.cmd == "detail":
        print(json.dumps(c.challenge_detail(args.challenge), ensure_ascii=False, indent=2))
    elif args.cmd == "attachment":
        path = c.download_attachment(args.challenge, Path(args.dest))
        print(f"saved: {path}" if path else "no attachment")
    elif args.cmd == "submit":
        print(json.dumps(c.submit(args.challenge, args.flag), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
