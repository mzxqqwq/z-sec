"""Cybench 评测平台适配器（BasePlatform 实现）——本地数据源版。

数据源：Windows 本地 D:\\ctf-agent\\benchmarks\\cybench（40 题，4 个 CTF 赛事）
结构：benchmark/<赛事>/<组>/<题目>/metadata/metadata.json
  - categories: ["crypto"]  difficulty: "1"~"4"（或 easy/medium/hard 字样）
  - easy_prompt / hard_prompt：题目描述（不含真值，可用）
  - subtasks[].answer：最后一个 flag 形态的 answer 即真值
  - challenge/ 目录：下发给选手的文件（init_script.sh 决定具体清单，我们整目录给）
服务类题（target_host 非空 + start_docker.sh）需要容器，Kali podman 镜像仓库被墙，
默认跳过（skip_services=True）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

try:
    from platform import BasePlatform, NormalizedChallenge, SubmitResult  # noqa: F401
except ImportError:  # 允许从 ctf_orchestrator 目录内直接 import
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from platform import BasePlatform, NormalizedChallenge, SubmitResult

CYBENCH_ROOT_DEFAULT = r"D:\ctf-agent\benchmarks\cybench"

DIFFICULTY_MAP = {"1": "easy", "2": "moderate", "3": "hard", "4": "hard",
                  "very_easy": "very_easy", "easy": "easy", "medium": "moderate",
                  "moderate": "moderate", "hard": "hard"}

FLAG_RE = re.compile(r"[A-Za-z0-9_]{2,}\{[^\s\r\n{}]{3,120}\}")

# 附件的排除目录/文件（防把 solution/官方解与 metadata/真值下发给 worker）
EXCLUDE_NAMES = {"metadata", "solution", "solutions"}


def _cid_from_rel(rel: Path) -> str:
    """相对路径 → 安全 cid（工作目录/文件名可用）。"""
    parts = []
    for p in rel.parts:
        s = re.sub(r"[^A-Za-z0-9._-]+", "_", p).strip("_")
        if s:
            parts.append(s)
    return "__".join(parts) or "unknown"


def _cat_of(meta: dict[str, Any]) -> str:
    cats = [str(c).lower() for c in (meta.get("categories") or [])]
    cat = cats[0] if cats else "misc"
    return {"reverse": "rev", "forensics": "misc", "forensic": "misc",
            "cryptography": "crypto", "pwn": "pwn", "web": "web",
            "misc": "misc", "crypto": "crypto", "rev": "rev"}.get(cat, cat)


class CybenchPlatform(BasePlatform):
    name = "cybench"

    def __init__(self, root: str = CYBENCH_ROOT_DEFAULT,
                 categories: Optional[list[str]] = None,
                 exclude: Optional[list[str]] = None,
                 max_files_mb: float = 20.0,
                 skip_services: bool = True,
                 revive: bool = True) -> None:
        self.root = Path(root)
        self.categories = categories
        self.exclude = set(exclude or [])
        self.max_files_mb = max_files_mb
        self.skip_services = skip_services
        self.revive = revive  # 服务题构建流水线（cybuild）开关，默认开
        self._entries: dict[str, dict[str, Any]] = {}
        self._revived: dict[str, int] = {}  # cid -> Kali 本地端口
        self._load()

    def _load(self) -> None:
        for meta_path in self.root.rglob("metadata.json"):
            rel_dir = meta_path.parent.parent  # 题目根目录（metadata/ 的上一级）
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(meta, dict):
                continue
            rel = rel_dir.relative_to(self.root)
            self._entries[str(rel).replace("\\", "/")] = {
                "meta": meta, "rel": rel,
            }

    @staticmethod
    def _flag_of(meta: dict[str, Any]) -> str:
        """从 subtasks 里取 flag 形态的 answer 作真值。"""
        for st in meta.get("subtasks", []) or []:
            ans = str(st.get("answer", ""))
            if FLAG_RE.search(ans):
                return FLAG_RE.search(ans).group(0)
        return ""

    def _normalized(self, key: str, meta: dict[str, Any],
                    rel: Path) -> NormalizedChallenge:
        cat = _cat_of(meta)
        diff_raw = str(meta.get("difficulty", "")).lower()
        diff = DIFFICULTY_MAP.get(diff_raw, "moderate")
        desc = str(meta.get("easy_prompt") or meta.get("hard_prompt") or "")
        files = self._task_files(rel)
        target_host = str(meta.get("target_host") or "")
        cid = _cid_from_rel(rel)
        return NormalizedChallenge(
            platform=self.name,
            challenge_id=cid,
            name=str(rel.name),
            category=cat,
            description=desc,
            points=None,
            files=files,
            target_kind="remote" if target_host else "static",
            host=target_host or None,
            raw={**meta, "_rel": str(rel), "_key": key,
                 "difficulty": diff},
        )

    def _task_files(self, rel: Path) -> list[str]:
        """任务下发给选手的文件清单（相对路径）：
        除 metadata/solution/*.sh/README*/flag* 外全部给——flag 文件是构建材料/真值，
        绝不下发（服务题由容器承载，静态题的真值在 metadata，2026-08-16 完整性收紧）。"""
        task_root = self.root / rel
        out: list[str] = []
        for f in sorted(task_root.rglob("*")):
            if not f.is_file():
                continue
            rel_parts = f.relative_to(task_root).parts
            if any(p in EXCLUDE_NAMES for p in rel_parts):
                continue
            if f.suffix == ".sh" or f.name.startswith("README"):
                continue
            if f.name.lower().startswith("flag"):
                continue
            out.append("/".join(rel_parts))
        return out

    def list_challenges(self) -> list[NormalizedChallenge]:
        out: list[NormalizedChallenge] = []
        for key, entry in self._entries.items():
            meta = entry["meta"]
            ch = self._normalized(key, meta, entry["rel"])
            if self.exclude and ch.challenge_id in self.exclude:
                continue
            if self.categories and ch.category not in self.categories:
                continue
            if self.skip_services and ch.target_kind == "remote":
                continue
            # 服务题本地构建流水线（cybuild）：构建+起容器，覆盖连接点为 127.0.0.1
            if ch.target_kind == "remote" and self.revive:
                try:
                    import cybuild
                    ok, hp, err = cybuild.build_and_run(
                        ch.challenge_id, self.root / entry["rel"],
                        str(meta.get("target_host") or ""))
                except Exception as e:
                    ok, hp, err = False, None, f"cybuild 异常: {e}"
                if ok and hp:
                    ch.host, ch.port = "127.0.0.1", int(hp)
                    ch.raw["liveness"] = "alive"
                    ch.raw["revived"] = True
                    self._revived[ch.challenge_id] = int(hp)
                    print(f"[cybuild] {ch.challenge_id} -> 127.0.0.1:{hp}")
                else:
                    ch.raw["liveness"] = "dead"
                    print(f"[cybuild] {ch.challenge_id} 失败: {err}")
            out.append(ch)
        return out

    def download_attachments(self, challenge: NormalizedChallenge,
                             dest_dir: Path) -> list[Path]:
        rel = Path(challenge.raw["_rel"]) if challenge.raw.get("_rel") else None
        if rel is None:
            return []
        task_root = self.root / rel
        if not task_root.is_dir():
            return []
        paths: list[Path] = []
        dest_dir.mkdir(parents=True, exist_ok=True)
        for rel_name in self._task_files(rel):
            src = task_root / Path(*rel_name.split("/"))
            if src.stat().st_size > self.max_files_mb * 1024 * 1024:
                print(f"[cybench] skip large file {rel_name}")
                continue
            out = dest_dir / Path(*rel_name.split("/"))
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(src.read_bytes())
            paths.append(out)
        return paths

    def submit_flag(self, challenge: NormalizedChallenge, flag: str) -> SubmitResult:
        expected = self._flag_of(challenge.raw).strip()
        accepted = bool(expected) and flag.strip() == expected
        return SubmitResult(accepted=accepted,
                            message="correct" if accepted else "wrong",
                            already_solved=False)
