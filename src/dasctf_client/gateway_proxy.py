# -*- coding: utf-8 -*-
"""DASCTF 大模型网关本地代理（比赛模式用）。

为什么需要：平台网关 URL 是"完整端点"（原始 URL 含 /chat/completions），
必须 POST 网关 URL 根；而 pi(openai SDK) / planner / digest 都会在 base_url 后
拼 /chat/completions。本代理监听 127.0.0.1:8787，把收到的 /chat/completions
请求转发到网关 URL 根（剥掉多余路径），保留 Authorization/Content-Type/body，
流式响应原样回传。所有 LLM 流量的最终出口仍是平台网关 → 合规（流量审计）。

用法：python gateway_proxy.py [--port 8787]
配置：config/secrets.json dasctf.gateway_url
"""
import argparse
import http.server
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SECRETS = ROOT / "config" / "secrets.json"


def load_gateway_url() -> str:
    try:
        data = json.loads(SECRETS.read_text(encoding="utf-8"))
        d = data.get("dasctf") or {}
        return str(d.get("gateway_url") or "").strip()
    except Exception:
        return ""


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    gateway: str = ""
    protocol_version = "HTTP/1.1"

    def _forward(self):
        if not self.gateway:
            self._send_text(500, "gateway_url 未配置（config/secrets.json dasctf.gateway_url）")
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        req = urllib.request.Request(self.gateway, data=body or None, method="POST")
        for h in ("Content-Type", "Authorization", "Accept", "User-Agent", "X-Session-Id"):
            if h in self.headers:
                req.add_header(h, self.headers[h])
        try:
            resp = urllib.request.urlopen(req, timeout=600)
        except urllib.error.HTTPError as e:
            # 错误分支同样要封包：Content-Length + Connection: close，
            # 否则 HTTP/1.1 keep-alive 下客户端等 EOF 挂死（2026-08-19 审查）
            try:
                body = e.read()
            except Exception:
                body = b""
            self.send_response(e.code)
            self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(body)
            except Exception:
                pass
            self.close_connection = True
            return
        except Exception as e:
            self._send_text(502, f"gateway forward failed: {e}")
            return
        self.send_response(resp.status)
        # 只转发 Content-Type；不转发 Content-Length/Transfer-Encoding（代理层重新封包），
        # 强制 Connection: close，客户端靠 EOF 收尾（流式 SSE 也兼容）。
        ct = resp.headers.get("Content-Type", "application/json")
        self.send_header("Content-Type", ct)
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            while True:
                chunk = resp.read(16384)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except Exception:
            pass
        finally:
            resp.close()
            self.close_connection = True

    def do_POST(self):
        self._forward()

    def do_GET(self):
        self._forward()

    def _send_text(self, code: int, text: str):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(text.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(text.encode("utf-8"))

    def log_message(self, fmt, *args):
        sys.stderr.write("[gateway-proxy] %s\n" % (fmt % args))


def main() -> int:
    ap = argparse.ArgumentParser(prog="gateway_proxy")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    gw = load_gateway_url()
    if not gw:
        print("error: 未配置网关 URL（config/secrets.json dasctf.gateway_url）")
        return 2
    ProxyHandler.gateway = gw
    srv = http.server.ThreadingHTTPServer((args.host, args.port), ProxyHandler)
    print(f"gateway proxy 已启动: http://{args.host}:{args.port} -> {gw}")
    print("（Ctrl+C 停止）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
