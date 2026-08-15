"""CTFTiny 评测平台适配器（BasePlatform 实现）——本地数据源版。

- 题目与判题数据源：Windows 本地 D:\\ctf-agent\\benchmarks\\ctftiny（公开仓库检出）
- 附件：直接从本地目录复制到 Windows 工作区，再由编排器推送到 Kali
- 判题：flag 精确比对（strip 后），description 中出现的真值一律脱敏

为什么改成本地版：此前题库整树放在 Kali /root/ctftiny，worker 的 bash
以 root 跑在 Kali 上，可直接 cat challenge.json/flag.txt 拿到真值
（2026-08-15 polly 复测即因 worker 直读 challenge.json 作弊解，成绩作废）。
题库数据放 Windows 后，worker 运行时（Kali）与真值数据物理隔离。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

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

CATEGORY_MAP = {"cry": "crypto", "for": "misc", "msc": "misc", "forensics": "misc",
                "re": "rev", "pwn": "pwn", "rev": "rev", "web": "web"}

# 仓库内 Windows 非法路径 → 本地净化目录（get_it? 的 ? 在 NTFS 不合法）
PATH_OVERRIDES: dict[str, str] = {
    "ctftiny/pwn/get_it?": "ctftiny/pwn/get_it_q",
    "test/2018/CSAW-Quals/pwn/get_it?": "test/2018/CSAW-Quals/pwn/get_it_q",
}

LOCAL_ROOT_DEFAULT = r"D:\ctf-agent\benchmarks\ctftiny"
# NYU_CTF_Bench（CTFTiny 的全量上游：test 200 题 + development 57 题，2013-2023）
NYU_ROOT_DEFAULT = r"D:\ctf-agent\benchmarks\nyu-ctf-bench"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


# 按规范化题目名匹配难度（README 表的下划线名与 ctftiny.json 的连字符/大小写名统一）
DIFFICULTY_BY_NAME = {_norm(k.split("-", 1)[1]): v for k, v in DIFFICULTY.items()}


class CtftinyPlatform(BasePlatform):
    name = "ctftiny"

    def __init__(self, kali_url: str = "http://10.174.153.128:5000",
                 root: str = LOCAL_ROOT_DEFAULT,
                 meta_files: tuple[str, ...] = ("ctftiny.json",),
                 difficulties: Optional[list[str]] = None,
                 categories: Optional[list[str]] = None,
                 exclude: Optional[list[str]] = None,
                 max_files_mb: float = 20.0) -> None:
        self.kali_url = kali_url.rstrip("/")  # 保留：worker 运行时健康检查等仍用它
        self.root = Path(root)
        self.meta_files = meta_files  # 元数据文件（ctftiny.json 或 test_dataset.json 等，可多个合并）
        self.difficulties = difficulties  # None = 全部
        self.categories = categories      # None = 全部
        self.exclude = set(exclude or [])
        self.max_files_mb = max_files_mb
        self._meta: dict[str, dict[str, Any]] = {}
        self._details: dict[str, dict[str, Any]] = {}
        self._dir_cache: dict[str, str] = {}
        self._load_meta()

    # ---------- 本地文件访问 ----------
    def _local(self, rel: str) -> Path:
        """仓库相对路径 → 本地 Path（先精确，再 Windows 大小写差异由 OS 吸收，
        get_it? 等非法路径用 PATH_OVERRIDES 映射）。"""
        rel = rel.replace("/", "\\")
        if rel in PATH_OVERRIDES:
            return self.root / PATH_OVERRIDES[rel]
        return self.root / rel

    def _load_meta(self) -> None:
        for meta_file in self.meta_files:
            path = self.root / meta_file
            if not path.exists():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            for key, entry in data.items():
                raw_cat = (entry.get("category") or key.split("-")[0]).lower()
                cat = CATEGORY_MAP.get(raw_cat, raw_cat)
                self._meta[key] = {
                    "cid": key,
                    "name": entry.get("challenge", key),
                    "cat": cat,
                    "path": entry.get("path", ""),
                    "event": entry.get("event", ""),
                    "year": entry.get("year", ""),
                }

    def _resolve_dir(self, rel: str) -> str:
        """目录名解析：先精确，再规范化匹配（下划线/空格/大小写/非法字符差异）。"""
        if rel in self._dir_cache:
            return self._dir_cache[rel]
        override = PATH_OVERRIDES.get(rel)
        if override and (self.root / override).is_dir():
            self._dir_cache[rel] = override
            return override
        full = self._local(rel)
        if full.is_dir():
            self._dir_cache[rel] = rel
            return rel
        parent = self._local(str(Path(rel).parent))
        base = Path(rel).name
        nbase = _norm(base)
        if parent.is_dir():
            for name in parent.iterdir():
                if name.is_dir() and _norm(name.name) == nbase:
                    resolved = str(Path(rel).parent) + "/" + name.name
                    self._dir_cache[rel] = resolved
                    return resolved
        self._dir_cache[rel] = rel
        return rel

    def _detail(self, cid: str) -> dict[str, Any]:
        if cid not in self._details:
            meta = self._meta.get(cid)
            if meta is None:
                return {}
            rel = self._resolve_dir(meta["path"])
            path = self._local(rel) / "challenge.json"
            try:
                detail = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                detail = {}
            self._details[cid] = self._redact(detail)
        return self._details.get(cid, {})

    @staticmethod
    def _redact(detail: dict[str, Any]) -> dict[str, Any]:
        """description 脱敏：真值 flag 不得出现在任何下发给 worker 的字段里。"""
        flag = str(detail.get("flag") or "")
        if not flag:
            return detail
        out = dict(detail)
        desc = str(out.get("description") or "")
        for variant in {flag, flag.replace("{", "{{"), flag.replace("}", "}}")}:
            desc = desc.replace(variant, "flag{***REDACTED***}")
        out["description"] = desc
        return out

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
            # 服务题：box + internal_port → 远程连接信息（T-F1：此前丢失导致 worker 无路可走）
            box = str(detail.get("box") or "")
            port = detail.get("internal_port") or detail.get("port")
            try:
                port_i = int(port) if port is not None else None
            except (TypeError, ValueError):
                port_i = None
            out.append(NormalizedChallenge(
                platform=self.name,
                challenge_id=cid,
                name=detail.get("name") or meta["name"],
                category=cat,
                description=detail.get("description", ""),
                points=detail.get("points"),
                files=list(detail.get("files") or []),
                target_kind="remote" if box else "static",
                host=box or None,
                port=port_i,
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
        base = self._local(rel)
        paths: list[Path] = []
        dest_dir.mkdir(parents=True, exist_ok=True)
        for fname in detail.get("files") or []:
            raw = str(fname).lstrip("./").replace("\\", "/")
            if raw.startswith("/") or ".." in raw.split("/"):
                continue  # 防路径穿越
            src = base / Path(*raw.split("/"))
            if not src.is_file():
                # 兜底：仓库布局与 files 清单不一致时按 basename 找（如 DES2Bites 的
                # Challenge/ 实际在 dist/）；找不到就跳过
                candidates = [f for f in base.rglob(Path(raw).name) if f.is_file()]
                if not candidates:
                    continue
                src = candidates[0]
            data = src.read_bytes()
            if len(data) > self.max_files_mb * 1024 * 1024:
                print(f"[ctftiny] skip large file {raw} ({len(data)/1e6:.1f}MB)")
                continue
            out = dest_dir / Path(raw).name
            out.write_bytes(data)
            paths.append(out)
        return paths

    def submit_flag(self, challenge: NormalizedChallenge, flag: str) -> SubmitResult:
        expected = (self._detail(challenge.challenge_id).get("flag") or "").strip()
        accepted = bool(expected) and flag.strip() == expected
        return SubmitResult(accepted=accepted,
                            message="correct" if accepted else "wrong",
                            already_solved=False)
