"""kb_server.py —— 本地解题知识库检索服务（T13，Windows :8099 只读）。

数据源 = 本地 benchmark（330+ 题的 challenge.json/README/solver 与 DASCTF writeup），
启动时建轻量索引（题名/分类/描述/文件名），关键词查询返回前 5 条"题型→手法"参考。
评测模式默认关闭（完整性铁律：KB 对 worker 不可见，除非显式 kb_enabled）。

用法：
    python kb_server.py [--port 8099]   # 常驻；编排器/worker 只读调用
    GET /search?q=...                   # {"results": [{"name","category","desc","hint"}]}
    GET /ping
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOTS = [
    (r"D:\ctf-agent\benchmarks\ctftiny", "challenge.json"),
    (r"D:\ctf-agent\benchmarks\nyu-ctf-bench\test", "challenge.json"),
    (r"D:\ctf-agent\benchmarks\cybench\benchmark", "metadata.json"),
]
WRITEUPS = r"D:\ctf-agent\benchmarks\dasctf-writeups"

# 常见题型 → 手法提示（无真值，只给方法论）
METHOD_HINTS = {
    "crypto": "RSA 常规套路：小 e 开根/共模/维纳攻击（oWiener）/LLL 格攻击（fpylll）；先确认参数关系再动手。",
    "rsa": "RSA 常规套路：小 e 开根/共模/维纳攻击（oWiener）/LLL 格攻击（fpylll）；先确认参数关系再动手。",
    "misc": "先 file/binwalk/strings 三连；隐写常用 zsteg/steghide/stegseek/binwalk -e；流量包先找 flag 明文或导文件。",
    "steg": "隐写链：先看文件尾部追加数据、像素 LSB、盲水印（DFT）；工具 zsteg/stegseek/blind-watermark。",
    "pwn": "checksec/file 确认保护；栈题找溢出点+win 函数/ret2libc；格式字符串查可写地址；gdb+pwntools 联调。",
    "rev": "strings/反编译先行；找关键比较函数；静态主攻 angr 符号执行；Android 用 jadx/blutter（flutter 用 blutter）。",
    "web": "先抓路由与参数；注入类上 sqlmap/手注；模板注入测 {{7*7}}；文件包含/上传/SSTI 按提示词特征排查。",
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _load_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root, meta_name in ROOTS:
        base = Path(root)
        if not base.is_dir():
            continue
        for meta_path in base.rglob(meta_name):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(meta, dict):
                continue
            name = str(meta.get("name") or meta_path.parent.name)
            cats = meta.get("categories")
            if isinstance(cats, list) and cats:
                cat = str(cats[0])
            else:
                cat = str(meta.get("category", ""))
            desc = str(meta.get("description") or meta.get("easy_prompt") or "")[:300]
            key = _norm(name)
            if not key or key in seen:
                continue
            seen.add(key)
            files = [f.name for f in meta_path.parent.rglob("*") if f.is_file()][:20]
            entries.append({"name": name, "category": cat, "desc": desc,
                            "files": " ".join(files)[:400]})
    for wf in Path(WRITEUPS).rglob("*"):
        if wf.is_file() and wf.suffix in (".md", ".txt"):
            try:
                text = wf.read_text(encoding="utf-8", errors="replace")[:800]
            except OSError:
                continue
            entries.append({"name": wf.stem, "category": "writeup",
                            "desc": text, "files": ""})
    return entries


def search(entries: list[dict[str, Any]], query: str, limit: int = 5) -> list[dict[str, Any]]:
    tokens = [t for t in _norm(query).split() if len(t) >= 2]
    if not tokens:
        return []
    scored: list[tuple[int, dict[str, Any]]] = []
    for e in entries:
        cat = _norm(e["category"])
        hint = next((v for k, v in METHOD_HINTS.items() if k in cat), "")
        text = _norm(f"{e['name']} {e['category']} {e['desc']} {e['files']} {hint}")
        score = sum(text.count(t) for t in tokens) * 3
        if all(t in text for t in tokens):
            score += 10
        if score > 0:
            scored.append((score, e))
    scored.sort(key=lambda x: -x[0])
    out = []
    for _, e in scored[:limit]:
        cat = _norm(e["category"])
        hint = next((v for k, v in METHOD_HINTS.items() if k in cat), "")
        out.append({"name": e["name"], "category": e["category"],
                    "desc": e["desc"][:200], "hint": hint})
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8099)
    args = p.parse_args()
    print(f"[kb] indexing benchmark trees ...")
    entries = _load_entries()
    print(f"[kb] indexed {len(entries)} entries")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # 静默
            pass

        def _json(self, code, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/ping":
                self._json(200, {"ok": True, "entries": len(entries)})
            elif url.path == "/search":
                q = (parse_qs(url.query).get("q") or [""])[0]
                self._json(200, {"results": search(entries, q)})
            else:
                self._json(404, {"error": "not found"})

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[kb] listening on http://127.0.0.1:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
