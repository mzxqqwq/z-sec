"""平台抽象层（Koshary BasePlatform 思想的独立实现）。

编排器只与 BasePlatform 接口交互；平台差异（mock/DASCTF/评测床）全部封装在适配器内。
接口与数据类为自研实现，不复制任何第三方代码。
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class NormalizedChallenge:
    """平台无关的题目视图（供编排器与 worker 提示词使用）。"""
    platform: str
    challenge_id: str
    name: str
    category: str
    description: str
    points: Optional[float] = None
    solved: bool = False
    files: list[str] = field(default_factory=list)
    # 动态靶机信息（web/pwn 远程题；static 题为空）
    target_kind: str = "static"  # static / remote
    host: Optional[str] = None
    port: Optional[int] = None
    url: Optional[str] = None
    # 平台原始数据（调试与平台特有动作用，如 CTFd 的整数 id）
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def connection_info(self) -> Optional[str]:
        if self.url:
            return self.url
        if self.host and self.port:
            return f"{self.host}:{self.port}"
        if self.host:
            return self.host
        return None

    def to_prompt_json(self) -> dict[str, Any]:
        """worker 提示词用的紧凑视图。"""
        out = {
            "id": self.challenge_id,
            "name": self.name,
            "category": self.category,
            "points": self.points,
            "description": self.description,
        }
        if self.connection_info:
            out["connection"] = self.connection_info
        if self.files:
            out["files"] = self.files
        return out


@dataclass
class SubmitResult:
    accepted: bool
    message: str = ""
    already_solved: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


class BasePlatform(ABC):
    """所有平台适配器必须实现的接口。"""

    name: str = "base"

    @abstractmethod
    def list_challenges(self) -> list[NormalizedChallenge]:
        """返回当前比赛的全部题目。"""

    @abstractmethod
    def download_attachments(self, challenge: NormalizedChallenge, dest_dir: Path) -> list[Path]:
        """下载附件到 dest_dir，返回本地文件路径列表。"""

    @abstractmethod
    def submit_flag(self, challenge: NormalizedChallenge, flag: str) -> SubmitResult:
        """提交 flag，返回归一化结果。"""

    def scoreboard(self) -> list[dict[str, Any]]:
        return []

    def close(self) -> None:
        return None


class MockHttpPlatform(BasePlatform):
    """本地 mock 平台适配器（演练/回归用）。"""

    name = "mock"

    def __init__(self, base_url: str, session: Any = None) -> None:
        import requests
        self.base_url = base_url.rstrip("/")
        self.s = session or requests.Session()

    def list_challenges(self) -> list[NormalizedChallenge]:
        r = self.s.get(f"{self.base_url}/api/challenges", timeout=10)
        r.raise_for_status()
        data = r.json()
        rows = data.get("challenges", data) if isinstance(data, dict) else data
        out = []
        for row in rows:
            out.append(NormalizedChallenge(
                platform=self.name,
                challenge_id=str(row.get("id")),
                name=row.get("name", ""),
                category=(row.get("category") or "unknown").lower(),
                description=row.get("description", ""),
                points=row.get("points"),
                raw=row,
            ))
        return out

    def download_attachments(self, challenge: NormalizedChallenge, dest_dir: Path) -> list[Path]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        r = self.s.get(f"{self.base_url}/api/challenges/{challenge.challenge_id}/attachment",
                       timeout=30)
        if r.status_code != 200:
            return []
        out = dest_dir / f"challenge_{challenge.challenge_id}.bin"
        out.write_bytes(r.content)
        return [out]

    def submit_flag(self, challenge: NormalizedChallenge, flag: str) -> SubmitResult:
        r = self.s.post(f"{self.base_url}/api/challenges/{challenge.challenge_id}/submit",
                        json={"flag": flag}, timeout=15)
        r.raise_for_status()
        data = r.json()
        accepted = bool(data.get("correct") or data.get("success"))
        return SubmitResult(accepted=accepted, message=data.get("msg", ""), raw=data)


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "challenge"


class DasctfPlatform(BasePlatform):
    """DASCTF 真实平台适配器（包 dasctf_client；端点到 8/18 测试赛确认后填实）。"""

    name = "dasctf"

    def __init__(self, base_url: str) -> None:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dasctf_client"))
        from dasctf_client import DasctfClient
        self.client = DasctfClient(base_url)

    def list_challenges(self) -> list[NormalizedChallenge]:
        out = []
        for row in self.client.challenges():
            out.append(NormalizedChallenge(
                platform=self.name,
                challenge_id=str(row.get("id") or row.get("challenge_id") or row.get("name", "")),
                name=str(row.get("name") or row.get("title") or ""),
                category=(row.get("category") or "unknown").lower(),
                description=row.get("description", ""),
                points=row.get("points"),
                raw=row,
            ))
        return out

    def download_attachments(self, challenge: NormalizedChallenge, dest_dir: Path) -> list[Path]:
        p = self.client.download_attachment(challenge.challenge_id, dest_dir)
        return [p] if p else []

    def submit_flag(self, challenge: NormalizedChallenge, flag: str) -> SubmitResult:
        data = self.client.submit(challenge.challenge_id, flag)
        accepted = bool(data.get("correct") or data.get("success"))
        return SubmitResult(accepted=accepted, message=str(data.get("msg", "")), raw=data)
