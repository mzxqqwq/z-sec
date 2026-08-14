#!/usr/bin/env python3
"""
probe_platform.py —— DASCTF 平台 API 探测脚本（8/18 测试赛专用）

测试赛开赛后第一时间运行，系统性发现真实 API 端点，为适配 dasctf_client
提供一手证据。所有结果落盘到 --out 目录，供快速适配。

用法：
    python probe_platform.py --base-url https://game.gcsis.cn --out D:/ctf-agent/workspace/probe

探测内容：
1. SPA 入口与 JS bundle 里的 /api 路径、baseURL、域名
2. 常见端点路径探测（GET，记录状态码与响应首部）
3. openapi/swagger 文档探测
4. 登录接口形状探测（config 端点：是否要验证码/加密）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests

COMMON_PATHS = [
    "/api/config", "/api/user", "/api/account/login", "/api/login",
    "/api/challenges", "/api/game", "/api/games", "/api/challenge",
    "/api/scoreboard", "/api/team", "/api/teams", "/api/contest",
    "/api/attachment", "/api/submit", "/api/flag",
    "/openapi.json", "/swagger.json", "/api-docs", "/docs",
    "/robots.txt",
]

JS_CACHE: dict[str, str] = {}


def fetch(url: str, session: requests.Session, timeout: float = 15.0) -> requests.Response | None:
    try:
        return session.get(url, timeout=timeout, allow_redirects=True)
    except requests.RequestException as e:
        return None


def extract_api_hints(text: str) -> dict[str, list[str]]:
    paths = sorted(set(re.findall(r"[\"'](/[a-zA-Z0-9_/{}.:\-]{2,80}(?:api)[a-zA-Z0-9_/{}.:\-]*)[\"']", text, re.I)))
    base_urls = sorted(set(re.findall(r"baseURL[\"']?\s*[:=]\s*[\"']([^\"']+)[\"']", text, re.I)))
    hosts = sorted(set(re.findall(r"[\"'](https?://[a-zA-Z0-9.\-]+(?:gcsis|dasctf|anheng|dbappsecurity)[a-zA-Z0-9./\-]*)[\"']", text, re.I)))
    return {"paths": paths[:100], "base_urls": base_urls[:20], "hosts": hosts[:20]}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="probe_platform")
    p.add_argument("--base-url", required=True)
    p.add_argument("--out", default="D:/ctf-agent/workspace/probe")
    args = p.parse_args(argv)

    base = args.base_url.rstrip("/")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    report: dict = {"at": stamp, "base": base, "spa": {}, "endpoints": [], "docs": []}

    s = requests.Session()
    s.headers.update({"User-Agent": "dasctf-agent-probe/0.1", "Accept": "*/*"})

    # 1) SPA 分析
    resp = fetch(base + "/", s)
    if resp is not None:
        report["spa"]["index_status"] = resp.status_code
        report["spa"]["index_title"] = re.search(r"<title>(.*?)</title>", resp.text).group(1) if re.search(r"<title>(.*?)</title>", resp.text) else None
        js_files = re.findall(r"<script[^>]+src=[\"']([^\"']+\.js)[\"']", resp.text)
        report["spa"]["js_files"] = js_files
        for js in js_files[:6]:
            js_url = urljoin(base + "/", js)
            if js_url in JS_CACHE:
                continue
            r = fetch(js_url, s, timeout=30)
            if r is not None and len(r.text) > 100:
                JS_CACHE[js_url] = r.text
                report["spa"][js] = extract_api_hints(r.text)
                print(f"[spa] {js}: {len(r.text)}B, paths={len(report['spa'][js]['paths'])}")

    # 2) 常见端点探测
    for path in COMMON_PATHS:
        url = base + path
        r = fetch(url, s, timeout=10)
        if r is None:
            continue
        body_head = r.text[:120].replace("\n", " ")
        entry = {"path": path, "status": r.status_code,
                 "ct": r.headers.get("Content-Type"), "body": body_head}
        report["endpoints"].append(entry)
        if r.status_code != 404 or "application/json" in (r.headers.get("Content-Type") or ""):
            print(f"[ep] {path} -> {r.status_code} {body_head[:80]}")

    # 3) docs
    for path in ("/openapi.json", "/swagger.json", "/v3/api-docs"):
        r = fetch(base + path, s, timeout=10)
        if r is not None and r.status_code == 200 and "json" in (r.headers.get("Content-Type") or ""):
            report["docs"].append({"path": path, "size": len(r.text)})
            print(f"[docs] FOUND {path} ({len(r.text)}B)")

    out_file = out_dir / f"probe-{stamp}.json"
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nreport saved: {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
