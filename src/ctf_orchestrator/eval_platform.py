"""CTFTiny 评测平台适配器（BasePlatform 实现）。

- 题目与判题数据源：Kali 上的 /root/ctftiny（CSAW 真题，challenge.json 含 flag 真值）
- 附件通道：经 Kali REST API 用 base64 取回 Windows 工作区（复用编排器现有管道）
- 判题：flag 精确比对（strip 后）
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import requests

sys_path_inserted = False
try:
    from platform import BasePlatform, NormalizedChallenge, SubmitResult  # noqa: F401
except ImportError:  # 允许从 ctf_orchestrator 目录内直接 import
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from platform import BasePlatform, NormalizedChallenge, SubmitResult

# README Challenge List 的难度表（CTFTiny 公开数据）
DIFFICULTY: dict[str, str] = {
    "cry-ecxor": "hard", "cry-lupin": "hard", "cry-babycrypto": "easy",
    "cry-super_curve": "easy", "cry-hybrid2": "very_easy", "cry-perfect_secrecy": "hard",
    "cry-collision_course": "moderate", "cry-beyond_quantum": "hard",
    "cry-open_ellipti_ph": "hard", "cry-polly_crack_this": "moderate",
    "cry-the_lengths_we_extend_ourselves": "hard", "cry-describeme": "moderate",
    "for-whyos": "hard", "for-1black0white": "easy",
    "pwn-pilot": "hard", "pwn-bigboy": "moderate", "pwn-get_it": "moderate",
    "pwn-baby_boi": "hard", "pwn-got_milk": "hard", "pwn-roppity": "hard",
    "pwn-slithery": "hard", "pwn-password_checker": "moderate",
    "pwn-puffin": "very_easy", "pwn-target_practice": "easy", "pwn-unlimited_subway": "hard",
    "rev-tablez": "moderate", "rev-a_walk_through_x86_part_2": "hard",
    "rev-gibberish_check": "hard", "rev-beleaf": "moderate", "rev-rap": "easy",
    "rev-sourcery": "moderate", "rev-baby_mult": "moderate", "rev-ezbreezy": "moderate",
    "rev-maze": "hard", "rev-checker": "very_easy", "rev-dockerleakage": "easy",
    "rev-the_big_bang": "hard", "rev-unvirtualization": "very_easy",
    "rev-rox": "hard", "rev-rebug_2": "moderate", "rev-whataxor": "very_easy",
    "web-poem_collection": "easy", "web-shreeramquest": "hard", "web-smug_dino": "easy",
    "msc-showdown": "very_easy", "msc-quantum_leap": "hard", "msc-algebra": "hard",
    "msc-weak_password": "easy", "msc-ezmaze": "easy", "msc-android_dropper": "easy",
}

CATEGORY_MAP = {"cry": "crypto", "for": "misc", "msc": "misc",
                "pwn": "pwn", "rev": "rev", "web": "web"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


# 按规范化题目名匹配难度（README 表的下划线名与 ctftiny.json 的连字符/大小写名统一）
DIFFICULTY_BY_NAME = {_norm(k.split("-", 1)[1]): v for k, v in DIFFICULTY.items()}


class CtftinyPlatform(BasePlatform):
    name = "ctftiny"

    def __init__(self, kali_url: str = "http://10.174.153.128:5000",
                 root: str = "/root/ctftiny",
                 difficulties: Optional[list[str]] = None,
                 categories: Optional[list[str]] = None,
                 exclude: Optional[list[str]] = None,
                 max_files_mb: float = 20.0) -> None:
        self.kali_url = kali_url.rstrip("/")
        self.root = root
        self.difficulties = difficulties  # None = 全部
        self.categories = categories      # None = 全部
        self.exclude = set(exclude or [])
        self.max_files_mb = max_files_mb
        self._meta: dict[str, dict[str, Any]] = {}
        self._details: dict[str, dict[str, Any]] = {}
        self._dir_cache: dict[str, str] = {}
        self._load_meta()

    def _api(self, command: str, timeout: int = 300) -> dict[str, Any]:
        r = requests.post(f"{self.kali_url}/api/command",
                          json={"command": command}, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def _load_meta(self) -> None:
        raw = self._api(f"cat {self.root}/ctftiny.json")["stdout"]
        data = json.loads(raw)
        for key, entry in data.items():
            raw_cat = (entry.get("category") or key.split("-")[0]).lower()
            # ctftiny.json 的 category 已是全称；兼容短码
            cat = {"cry": "crypto", "for": "misc", "msc": "misc", "re": "rev"}.get(raw_cat, raw_cat)
            self._meta[key] = {
                "cid": key,
                "name": entry.get("challenge", key),
                "cat": cat,
                "path": entry.get("path", ""),
                "event": entry.get("event", ""),
                "year": entry.get("year", ""),
            }

    def _resolve_dir(self, rel: str) -> str:
        """目录名大小写不敏感解析（实测 ezmaze→ezMaze 等不一致）。"""
        if rel in self._dir_cache:
            return self._dir_cache[rel]
        ok = self._api(f"test -d {self.root}/{rel} && echo OK", timeout=60).get("stdout", "").strip()
        if ok == "OK":
            self._dir_cache[rel] = rel
            return rel
        parent = str(Path(rel).parent)
        base = Path(rel).name
        found = self._api(
            f"find {self.root}/{parent} -maxdepth 1 -type d -iname '{base}' 2>/dev/null | head -1",
            timeout=60).get("stdout", "").strip()
        resolved = found.replace(self.root + "/", "") if found else rel
        self._dir_cache[rel] = resolved
        return resolved

    def _detail(self, cid: str) -> dict[str, Any]:
        if cid not in self._details:
            meta = self._meta.get(cid)
            if meta is None:
                return {}
            rel = self._resolve_dir(meta["path"])
            out = self._api(f"cat {self.root}/{rel}/challenge.json 2>/dev/null")["stdout"]
            try:
                self._details[cid] = json.loads(out) if out.strip() else {}
            except json.JSONDecodeError:
                self._details[cid] = {}
        return self._details.get(cid, {})

    def _enabled(self, cid: str) -> bool:
        if cid in self.exclude:
            return False
        meta = self._meta.get(cid, {})
        diff = DIFFICULTY_BY_NAME.get(_norm(str(meta.get("name", ""))),
                                      DIFFICULTY.get(cid, "moderate"))
        if self.difficulties and diff not in self.difficulties:
            return False
        cat = self._meta.get(cid, {}).get("cat", "misc")
        if self.categories and cat not in self.categories:
            return False
        return True

    def list_challenges(self) -> list[NormalizedChallenge]:
        out: list[NormalizedChallenge] = []
        for cid, meta in self._meta.items():
            if not self._enabled(cid):
                continue
            detail = self._detail(cid)
            cat = meta.get("cat", "misc")
            out.append(NormalizedChallenge(
                platform=self.name,
                challenge_id=cid,
                name=detail.get("name") or meta["name"],
                category=cat,
                description=detail.get("description", ""),
                points=detail.get("points"),
                files=list(detail.get("files") or []),
                raw={**meta, "difficulty": DIFFICULTY.get(cid, "moderate")},
            ))
        return out

    def download_attachments(self, challenge: NormalizedChallenge,
                             dest_dir: Path) -> list[Path]:
        detail = self._detail(challenge.challenge_id)
        meta = self._meta.get(challenge.challenge_id, {})
        rel = self._resolve_dir(meta.get("path", ""))
        if not rel:
            return []
        paths: list[Path] = []
        dest_dir.mkdir(parents=True, exist_ok=True)
        for fname in detail.get("files") or []:
            safe_rel = Path(str(fname).lstrip("./"))
            if safe_rel.is_absolute() or ".." in safe_rel.parts:
                continue  # 防路径穿越
            cmd = f"base64 -w0 {self.root}/{rel}/{safe_rel} 2>/dev/null"
            res = self._api(cmd, timeout=600)
            b64 = (res.get("stdout") or "").strip()
            if not b64:
                continue
            try:
                data = base64.b64decode(b64)
            except Exception:
                continue
            if len(data) > self.max_files_mb * 1024 * 1024:
                print(f"[ctftiny] skip large file {safe_rel} ({len(data)/1e6:.1f}MB)")
                continue
            out = dest_dir / safe_rel.name
            out.write_bytes(data)
            paths.append(out)
        return paths

    def submit_flag(self, challenge: NormalizedChallenge, flag: str) -> SubmitResult:
        expected = (self._detail(challenge.challenge_id).get("flag") or "").strip()
        accepted = bool(expected) and flag.strip() == expected
        return SubmitResult(accepted=accepted,
                            message="correct" if accepted else "wrong",
                            already_solved=False)
