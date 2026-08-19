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
            # benchmark 适配器会给出靶机存活探测结果（比赛平台不探测，无此字段）
            liveness = (self.raw or {}).get("liveness")
            if liveness:
                out["service_status"] = liveness
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

    def get_hint(self, challenge: NormalizedChallenge) -> str:
        """官方提示（平台提供时）。默认无提示。"""
        return ""

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

    def get_hint(self, challenge: NormalizedChallenge) -> str:
        r = self.s.get(f"{self.base_url}/api/challenges/{challenge.challenge_id}/hint",
                       timeout=15)
        if r.status_code != 200:
            return "该题无官方提示"
        try:
            data = r.json()
            return str(data.get("hint") or data.get("msg") or "该题无官方提示")
        except Exception:
            return "该题无官方提示"


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "challenge"


def _to_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _strip_flag(flag: str) -> str:
    """flag 提交仅需 {} 内内容（官方手册）；worker 给完整 DASCTF{...} 时剥壳。

    2026-08-19 审查：加词边界锚（^ 或非字母数字下划线前缀），避免误剥
    `xxx_flag{...}` 这类本身就是答案内容的合法 flag。
    """
    s = (flag or "").strip()
    m = re.search(r"(?:^|[^A-Za-z0-9_])(?:DASCTF|flag|CTF)\s*\{([^{}]+)\}\s*$", s, re.I)
    return m.group(1).strip() if m else s


def _parse_port(p: str) -> str:
    """端口字段可能是 '80' 或 'http/80'（协议/端口）→ 归一化为纯端口。"""
    return str(p).rsplit("/", 1)[-1] if "/" in str(p) else str(p)


def _attachment_files(attachment: Any) -> list[dict]:
    """兼容实际返回的多种附件结构：
    {files:[{name,url,ext}]}（文档示例） / 单对象 {url,name,extension}（实测） / [] / 数组。
    审查补丁：files 为空列表但顶层带 url 时（组合形态）也要取顶层，否则附件漏下载。
    """
    if isinstance(attachment, dict):
        files = attachment.get("files")
        if isinstance(files, list):
            out = [f for f in files if isinstance(f, dict)]
            if out or not attachment.get("url"):
                return out
            return [attachment]
        if attachment.get("url"):
            return [attachment]
        return []
    if isinstance(attachment, list):
        return [f for f in attachment if isinstance(f, dict) and f.get("url")]
    return []


class DasctfPlatform(BasePlatform):
    """DASCTF 真实平台适配器（2026-08-19 依据官方《AI Agent API 文档》）。"""

    name = "dasctf"

    @staticmethod
    def _probe_port(host: str, port: int, timeout: float = 2.0) -> bool:
        """快速 TCP 探测（靶机/代理端口是否可达）。"""
        import socket
        try:
            with socket.create_connection((host, int(port)), timeout=timeout):
                return True
        except Exception:
            return False

    def __init__(self, base_url: str) -> None:
        import os
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dasctf_client"))
        from dasctf_client import DasctfClient, load_dasctf_credentials
        creds = load_dasctf_credentials()
        access_key = creds.get("access_key") or os.environ.get("DASCTF_ACCESS_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.client = DasctfClient(base_url, access_key)

    # ---- endpoints → 可读连接信息 ----
    @staticmethod
    def _conn_text(endpoints: list[dict]) -> tuple[Optional[str], str]:
        first: Optional[str] = None
        first_proxy: Optional[str] = None
        lines: list[str] = []
        for ep in endpoints or []:
            ips = [str(x) for x in (ep.get("exposeIps") or [])]
            ports = [_parse_port(x) for x in (ep.get("ports") or [])]
            users = ep.get("users") or []
            mappings = ep.get("portMappings") or []
            proxy_ips = [str(x) for x in (ep.get("proxyIps") or [])]
            is_proxy = bool(ep.get("isProxy"))
            expire = ep.get("expireTime")
            conns: list[str] = []
            if is_proxy and proxy_ips:
                host = proxy_ips[0]
                pm = {_parse_port(m.get("port")): str(m.get("proxy") or m.get("port"))
                      for m in mappings if m.get("port")}
                conns = [f"{host}:{pm.get(p, p)}" for p in ports] or [host]
                lines.append(f"- 代理连接: {', '.join(conns)}")
            elif ips:
                host = ips[0]
                conns = [f"{host}:{p}" for p in ports] or [host]
                lines.append(f"- 直连: {', '.join(conns)}")
            # 主连接点优先取平台代理（worker 从外部能连）；直连内网 IP 只作兜底
            if conns:
                if first is None:
                    first = conns[0]
                if is_proxy and proxy_ips and first_proxy is None:
                    first_proxy = conns[0]
            for u in users:
                lines.append(f"    账号: {u.get('username')} / 密码: {u.get('password')}")
            if is_proxy:
                lines.append("    (优先走平台代理)")
            if expire:
                lines.append(f"    过期时间戳: {expire}")
        return (first_proxy or first), "\n".join(lines)

    def list_challenges(self) -> list[NormalizedChallenge]:
        import time
        from dasctf_client import ApiError, ClientError
        out: list[NormalizedChallenge] = []
        try:
            groups = self.client.exercise_list()
        except (ApiError, ClientError) as e:
            print(f"[dasctf] exercise-list 失败: {e}")
            return out
        # 靶机配额：本队同时最多 3 台（实测 40409）。
        # 活跃占用 = 未 solved 且靶机端口可达的题；solved 题的环境应让出配额；
        # 端口不可达（Fate 类平台侧故障）→ 回收并标记重建。
        MAX_ENV = 3
        details: dict[int, dict] = {}
        solved_ids: set[int] = set()
        probe: list[tuple[int, dict, str]] = []  # (eid, detail, first_conn)
        built_count = 0
        for g in groups:
            for c in (g.get("corpus") or []):
                try:
                    eid = int(c["id"])
                except (KeyError, TypeError, ValueError):
                    continue
                d: dict = {}
                try:
                    d = self.client.exercise(eid) or {}
                except (ApiError, ClientError) as e:
                    d = {"_error": str(e)}
                details[eid] = d
                if bool(d.get("hasSolved") or c.get("hasSolved")):
                    solved_ids.add(eid)
                    continue
                if d.get("endpoints"):
                    first_conn, _ = self._conn_text(d["endpoints"])
                    # 只对"平台代理已就绪"的靶机探测连通性（isProxy=true 且 proxyIps 非空）；
                    # 内网 IP/创建中的环境（proxyIps 空）外部不可达不代表靶机死 → 只算占用不探测
                    proxy_ready = any(e.get("isProxy") and e.get("proxyIps")
                                      for e in d.get("endpoints") or [])
                    if proxy_ready:
                        probe.append((eid, d, first_conn or ""))
                    else:
                        built_count += 1
        if probe:
            from concurrent.futures import ThreadPoolExecutor
            def _check(t: tuple[int, dict, str]) -> bool:
                eid, d, conn = t
                if ":" in conn:
                    h, _, pp = conn.rpartition(":")
                    if pp.isdigit():
                        # 探测带 1 次重试（瞬时抖动会误回收可用靶机——审查建议）
                        if self._probe_port(h, int(pp)):
                            return True
                        return self._probe_port(h, int(pp))
                return False
            with ThreadPoolExecutor(max_workers=4) as pool:
                alive_map = {t[0]: ok for t, ok in zip(probe, pool.map(_check, probe))}
            for eid, d, conn in probe:
                if alive_map.get(eid):
                    built_count += 1
                else:
                    try:
                        self.client.recover_env(eid)
                        print(f"[dasctf] {eid} 靶机不可达({conn or '无连接点'})，已回收，下轮重建")
                    except (ApiError, ClientError):
                        # 回收失败（网络/平台抖动）：不清 endpoints、不标重建，
                        # 保持现状下轮再试——否则会超额建机触发 40409（2026-08-19 审查）
                        print(f"[dasctf] {eid} 靶机不可达但回收失败，保持现状下轮再试")
                        continue
                    _d = dict(details.get(eid) or {})
                    _d["endpoints"] = []
                    _d["_env_rebuild"] = True
                    details[eid] = _d
        for eid in solved_ids:
            d = details.get(eid) or {}
            if d.get("isNeedInit") and d.get("endpoints"):
                try:
                    self.client.recover_env(eid)
                    print(f"[dasctf] {eid} 已解出，回收环境释放配额")
                except (ApiError, ClientError):
                    pass
                try:
                    details[eid] = self.client.exercise(eid) or {}
                except (ApiError, ClientError):
                    pass
        for g in groups:
            cat = str(g.get("name") or "misc").lower()
            for c in (g.get("corpus") or []):
                try:
                    eid = int(c["id"])
                except (KeyError, TypeError, ValueError):
                    continue
                detail = dict(details.get(eid) or {})
                solved = bool(detail.get("hasSolved") or c.get("hasSolved"))
                # 需要环境且未就绪（仅未解出的题）→ 配额内启动并轮询
                if (not solved and not detail.get("endpoints")
                        and not detail.get("_error")
                        and (detail.get("isNeedInit") or detail.get("_env_rebuild"))):
                    if built_count < MAX_ENV:
                        try:
                            self.client.build_env(eid)
                            for _ in range(30):
                                time.sleep(2)
                                detail = self.client.exercise(eid) or {}
                                if not detail.get("isNeedCheck") and detail.get("endpoints"):
                                    built_count += 1
                                    break
                        except (ApiError, ClientError) as e:
                            detail["_env_error"] = str(e)
                    else:
                        detail["_env_wait"] = f"靶机配额已满({MAX_ENV}台)，等待回收后自动启动"
                endpoints = detail.get("endpoints") or []
                first_conn, conn_text = self._conn_text(endpoints)
                desc = str(detail.get("description") or "")
                if detail.get("_error"):
                    desc = (desc + f"\n[题目详情获取失败: {detail['_error']}]").strip()
                if detail.get("_env_error"):
                    desc = (desc + f"\n[靶机环境启动失败: {detail['_env_error']}]").strip()
                if detail.get("_env_rebuild"):
                    desc = (desc + "\n[原靶机不可达已回收，正在重建环境]").strip()
                if detail.get("_env_wait"):
                    desc = (desc + f"\n[{detail['_env_wait']}]").strip()
                if conn_text:
                    desc = (desc + "\n\n【靶机连接信息】\n" + conn_text).strip()
                files = [str(f.get("name", "")) for f in _attachment_files(detail.get("attachment"))
                         if f.get("name")]
                host = port = None
                if first_conn and ":" in first_conn:
                    h, _, pp = first_conn.rpartition(":")
                    host, port = h, (int(pp) if pp.isdigit() else None)
                out.append(NormalizedChallenge(
                    platform=self.name,
                    challenge_id=str(eid),
                    name=str(detail.get("name") or c.get("name") or ""),
                    category=cat,
                    description=desc,
                    points=_to_float(detail.get("score")),
                    solved=solved,
                    files=files,
                    target_kind="remote" if (endpoints or detail.get("isNeedInit")) else "static",
                    host=host,
                    port=port,
                    raw={**detail, "_category": cat},
                ))
        return out

    def download_attachments(self, challenge: NormalizedChallenge, dest_dir: Path) -> list[Path]:
        paths: list[Path] = []
        for f in _attachment_files((challenge.raw or {}).get("attachment")):
            url = str(f.get("url") or "")
            if not url:
                continue
            if url.startswith("/"):
                url = self.base_url + url
            p = self.client.download(url, dest_dir, str(f.get("name") or ""))
            if p:
                paths.append(p)
        return paths

    def submit_flag(self, challenge: NormalizedChallenge, flag: str) -> SubmitResult:
        from dasctf_client import ApiError, ClientError
        try:
            data = self.client.submit_answer(int(challenge.challenge_id), _strip_flag(flag))
            ok = bool(data.get("isCorrect"))
            return SubmitResult(accepted=ok, message="correct" if ok else "wrong", raw=data)
        except (ApiError, ClientError) as e:
            return SubmitResult(accepted=False, message=f"{e.code}: {e.message}", raw={})

    def scoreboard(self) -> list[dict[str, Any]]:
        try:
            ov = self.client.overview()
            return [{"rank": ov.get("stageRank"), "point": ov.get("stagePoint")}]
        except Exception:
            return []
