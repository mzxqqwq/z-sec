#!/usr/bin/env python3
"""
DASCTF 竞赛平台 AI Agent API 客户端（v0.2，2026-08-19 依据官方《AI Agent API 文档》）

Base URL: {serverHost}/slab-match/api/v1/agent
认证: 请求头 X-Agent-AccessKey（Agent 专用 AccessKey，来自选手控制台「环境配置」页）
统一响应: {"code":"00000","message":"","data":{}}；code != "00000" 为失败。

用法:
    python dasctf_client.py match-info
    python dasctf_client.py exercise-list
    python dasctf_client.py exercise --id 1001
    python dasctf_client.py submit --id 1001 --flag xxx
    python dasctf_client.py build-env --id 1001
    python dasctf_client.py recover-env --id 1001
    python dasctf_client.py overview
    python dasctf_client.py notices
    python dasctf_client.py notice --id 501

账号来源（env > config/secrets.json dasctf 段，secrets.json 不入库）：
    config/secrets.json: {"dasctf": {"base_url": ..., "access_key": "ak_xxx", ...}}
依赖：requests。
"""
from __future__ import annotations

import argparse
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
# 平台账号来源（env > config/secrets.json dasctf 段；secrets.json gitignore）
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
SECRETS_PATH = ROOT / "config" / "secrets.json"


def load_dasctf_credentials() -> dict[str, str]:
    """返回 {base_url, username, password, access_key, gateway_url}。env 优先，其次文件。"""
    env = {
        "base_url": os.environ.get("DASCTF_BASE_URL", "").strip(),
        "username": os.environ.get("DASCTF_USERNAME", "").strip(),
        "password": os.environ.get("DASCTF_PASSWORD", "").strip(),
        "access_key": os.environ.get("DASCTF_ACCESS_KEY", "").strip(),
        "gateway_url": os.environ.get("DASCTF_GATEWAY_URL", "").strip(),
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
        "access_key": env["access_key"] or file_creds.get("access_key", ""),
        "gateway_url": env["gateway_url"] or file_creds.get("gateway_url", ""),
    }


# 常见 flag 形态（DASCTF 历史格式以 DASCTF{...}/flag{...} 为主）
FLAG_PATTERNS: list[re.Pattern] = [
    re.compile(rb"flag\{[0-9a-zA-Z_\-!@#$%^&*]{4,64}\}", re.I),
    re.compile(rb"dasctf\{[0-9a-zA-Z_\-!@#$%^&*]{4,64}\}", re.I),
    re.compile(rb"ctf\{[0-9a-zA-Z_\-!@#$%^&*]{4,64}\}", re.I),
    re.compile(rb"[0-9a-f]{32}", re.I),  # md5 形态兜底
]


class ClientError(RuntimeError):
    pass


class ApiError(ClientError):
    """平台返回非 00000 的错误码。"""

    def __init__(self, code: str, message: str, path: str = ""):
        super().__init__(f"[{path}] code={code} message={message}")
        self.code = code
        self.message = message
        self.path = path


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------
@dataclass
class DasctfClient:
    base_url: str
    access_key: str
    timeout: float = 30.0
    session: requests.Session = field(default_factory=requests.Session, repr=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self.agent_base = f"{self.base_url}/slab-match/api/v1/agent"
        self.session.headers.update({
            "User-Agent": "dasctf-agent/0.2",
            "Accept": "application/json",
            "X-Agent-AccessKey": self.access_key,
        })

    # ---- 底层 ----
    def _request(self, method: str, path: str, *,
                 params: Optional[dict] = None, json_body: Optional[dict] = None) -> dict:
        url = self.agent_base + path
        last: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = self.session.request(method, url, params=params, json=json_body,
                                            timeout=self.timeout)
            except requests.RequestException as e:
                last = e
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise ClientError(f"request failed: {e}") from e
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", 5))
                if attempt < 2:
                    time.sleep(retry_after)
                    continue
                raise ClientError(f"rate limited (429): retry after {retry_after}s")
            try:
                data = resp.json()
            except ValueError:
                raise ClientError(f"non-json {resp.status_code}: {resp.text[:300]}")
            if not isinstance(data, dict):
                return {"_raw": data}
            return data
        raise ClientError(f"unreachable: {last}")

    def _ok(self, method: str, path: str, *, params=None, json_body=None) -> Any:
        """调接口并检查 code==00000，返回 data；失败抛 ApiError。"""
        data = self._request(method, path, params=params, json_body=json_body)
        if "_raw" in data:
            return data["_raw"]
        code = str(data.get("code", ""))
        if code != "00000":
            raise ApiError(code, str(data.get("message", "")), path)
        return data.get("data")

    # ---- 9 个 Agent 接口 ----
    def match_info(self) -> dict:
        """竞赛注意事项与规则 {note, rule}。"""
        return self._ok("GET", "/match/notice/match-info") or {}

    def overview(self) -> dict:
        """得分与排名 {stagePoint, stageRank}。"""
        return self._ok("GET", "/answer-panel/overview") or {}

    def exercise_list(self) -> list[dict]:
        """题目列表（分组 corpus）。"""
        return self._ok("GET", "/ctf/exercise-list") or []

    def exercise(self, exercise_id: int) -> dict:
        """题目详情（含 attachment/endpoints/isNeedInit/isNeedCheck）。"""
        return self._ok("GET", "/ctf/exercise", params={"exerciseId": int(exercise_id)}) or {}

    def build_env(self, exercise_id: int) -> bool:
        """启动题目环境（异步）。"""
        self._ok("POST", "/ctf/build-exercise-env", json_body={"exerciseId": int(exercise_id)})
        return True

    def recover_env(self, exercise_id: int) -> bool:
        """回收题目环境。"""
        self._ok("POST", "/ctf/recover-exercise-env", json_body={"exerciseId": int(exercise_id)})
        return True

    def submit_answer(self, exercise_id: int, flag: str) -> dict:
        """提交答案，返回 data（含 isCorrect）。"""
        return self._ok("POST", "/answer-panel/answer",
                        json_body={"exerciseId": int(exercise_id), "flag": flag[:256]}) or {}

    def notice_list(self) -> list[dict]:
        return self._ok("GET", "/match/notice/now-list") or []

    def notice_detail(self, notice_id: int) -> dict:
        return self._ok("GET", "/match/notice/detail", params={"id": int(notice_id)}) or {}

    def download(self, url: str, dest_dir: Path, name: str = "") -> Optional[Path]:
        """下载附件（attachment.files[].url 或公告 url）。

        实测（2026-08-19）：附件 URL 带时效签名，编排器每轮 _run_one 都会重复下载，
        平台附件服务器会限流/重置连接（10054）。策略：目标文件已存在且非空 → 直接复用；
        下载失败 → 指数退避重试 3 次。
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        if not name:
            name = url.rstrip("/").split("/")[-1] or f"file_{int(time.time())}"
        path = dest_dir / name
        if path.exists() and path.stat().st_size > 0:
            return path
        last: Optional[Exception] = None
        for attempt in range(3):
            try:
                r = self.session.get(url, timeout=60, allow_redirects=True)
                if r.status_code == 200:
                    path.write_bytes(r.content)
                    return path
                return None
            except requests.RequestException as e:
                last = e
                if attempt < 2:
                    time.sleep(2 ** attempt)
        print(f"[dasctf] attachment download failed after retries: {last}")
        return None


# ---------------------------------------------------------------------------
# flag 检测器
# ---------------------------------------------------------------------------
def extract_flags(text: bytes | str) -> list[str]:
    raw = text.encode() if isinstance(text, str) else text
    found: list[str] = []
    for pat in FLAG_PATTERNS:
        for m in pat.finditer(raw):
            found.append(m.group(0).decode(errors="replace"))
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
    p.add_argument("--access-key", default=creds["access_key"], help="X-Agent-AccessKey")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("match-info")
    sub.add_parser("overview")
    sub.add_parser("exercise-list")
    sub.add_parser("notices")
    sp = sub.add_parser("exercise")
    sp.add_argument("--id", required=True, type=int)
    sp2 = sub.add_parser("submit")
    sp2.add_argument("--id", required=True, type=int)
    sp2.add_argument("--flag", required=True)
    sp3 = sub.add_parser("build-env")
    sp3.add_argument("--id", required=True, type=int)
    sp4 = sub.add_parser("recover-env")
    sp4.add_argument("--id", required=True, type=int)
    sp5 = sub.add_parser("notice")
    sp5.add_argument("--id", required=True, type=int)
    args = p.parse_args(argv)

    if not args.access_key:
        print("error: 缺少 access_key（--access-key 或 config/secrets.json dasctf 段）",
              file=sys.stderr)
        return 2
    c = DasctfClient(args.base_url, args.access_key)
    try:
        if args.cmd == "match-info":
            print(json.dumps(c.match_info(), ensure_ascii=False, indent=2))
        elif args.cmd == "overview":
            print(json.dumps(c.overview(), ensure_ascii=False, indent=2))
        elif args.cmd == "exercise-list":
            print(json.dumps(c.exercise_list(), ensure_ascii=False, indent=2))
        elif args.cmd == "exercise":
            print(json.dumps(c.exercise(args.id), ensure_ascii=False, indent=2))
        elif args.cmd == "submit":
            print(json.dumps(c.submit_answer(args.id, args.flag), ensure_ascii=False, indent=2))
        elif args.cmd == "build-env":
            print("ok" if c.build_env(args.id) else "failed")
        elif args.cmd == "recover-env":
            print("ok" if c.recover_env(args.id) else "failed")
        elif args.cmd == "notices":
            print(json.dumps(c.notice_list(), ensure_ascii=False, indent=2))
        elif args.cmd == "notice":
            print(json.dumps(c.notice_detail(args.id), ensure_ascii=False, indent=2))
    except ApiError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
