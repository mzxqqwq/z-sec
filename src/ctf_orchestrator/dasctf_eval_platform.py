"""DASCTF 2025 真题评测适配器（BasePlatform 实现）。

数据源：本地解压目录 benchmarks/dasctf-2025-extracted + 真值清单 dasctf-2025-manifest.json
（清单含 7 题真值；unknown 题在评测中跳过或人工判题）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from platform import BasePlatform, NormalizedChallenge, SubmitResult

MANIFEST_PATH = Path(r"D:\ctf-agent\benchmarks\dasctf-2025-manifest.json")
EXTRACTED_ROOT = Path(r"D:\ctf-agent\benchmarks\dasctf-2025-extracted")

# 题目名 → 解压目录关键词（处理乱码目录名）
DIR_HINTS = {
    "lost LFSR key": "lost LFSR key",
    "Serration": "Serration",
    "two_examples": "two_examples",
    "DigitalSignature": "DigitalSignature",
    "stegh": "stegh",
    "Steganography": "Steganography",
    "rcms": "rcms",
    "CV_Manager": "CV_Manager",
    "mvmp": "mvmp",
    "ezmac": "ezmac",
    "androidfile": "androidfile",
    "login": "login",
    "androidfff": "androidfff",
}


class DasctfEvalPlatform(BasePlatform):
    name = "dasctf2025"

    def __init__(self, manifest: Optional[Path] = None,
                 extracted: Optional[Path] = None,
                 skip_unknown_flags: bool = True) -> None:
        self.manifest = manifest or MANIFEST_PATH
        self.extracted = extracted or EXTRACTED_ROOT
        self.skip_unknown = skip_unknown_flags
        self._entries = json.loads(self.manifest.read_text(encoding="utf-8"))

    def _entry(self, cid: str) -> dict[str, Any]:
        for e in self._entries:
            if e.get("name") == cid or e.get("name") == cid.replace("-", " "):
                return e
        return {}

    def _find_dir(self, hint: str) -> Optional[Path]:
        """在解压树里按关键词找题目目录（处理乱码/嵌套）。"""
        target = hint.lower().replace(" ", "")
        for d in self.extracted.rglob("*"):
            if d.is_dir() and target in d.name.lower():
                # 优先最浅目录
                return d
        return None

    def list_challenges(self) -> list[NormalizedChallenge]:
        out = []
        for e in self._entries:
            if self.skip_unknown and e.get("flag") == "unknown":
                continue
            out.append(NormalizedChallenge(
                platform=self.name,
                challenge_id=e["name"],
                name=e["name"],
                category=(e.get("category") or "misc").lower(),
                description=e.get("solve_notes", ""),
                points=e.get("difficulty", ""),
                files=[str(f) for f in e.get("files", [])],
                raw=e,
            ))
        return out

    def download_attachments(self, challenge: NormalizedChallenge,
                             dest_dir: Path) -> list[Path]:
        hint = DIR_HINTS.get(challenge.name, challenge.name)
        d = self._find_dir(hint)
        if d is None:
            return []
        paths: list[Path] = []
        dest_dir.mkdir(parents=True, exist_ok=True)
        for f in d.rglob("*"):
            if f.is_file() and f.suffix.lower() in (".zip", ".py", ".txt", ".enc", ".png",
                                                    ".jpg", ".tar.gz", ".matrix", ".sage"):
                out = dest_dir / f.name
                if not out.exists():
                    out.write_bytes(f.read_bytes())
                paths.append(out)
        # 也把解压后的关键文件放进去
        return paths

    def submit_flag(self, challenge: NormalizedChallenge, flag: str) -> SubmitResult:
        expected = (challenge.raw.get("flag") or "").strip()
        if not expected:
            return SubmitResult(accepted=False, message="no ground truth")
        return SubmitResult(accepted=flag.strip() == expected,
                            message="correct" if flag.strip() == expected else "wrong")
