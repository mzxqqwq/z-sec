# Koshary / LLM-CTF-Solver 源码研究报告

> 目标仓库：`D:\ctf-agent\koshary-ref`（Koshary）、`D:\ctf-agent\llmctf-ref`（gehewu/LLM-CTF-Solver）。
> 所有行号均对应上述仓库当前文件内容，代码片段为原文摘录。

---

## 1. BasePlatform 平台抽象（Koshary `platforms/base.py` + CTFd 适配器）

### 1.1 NormalizedChallenge 字段

`platforms/base.py:17-58`：

```python
@dataclass
class NormalizedChallenge:
    platform: str
    event_id: str
    challenge_id: str
    name: str
    category: str
    points: Optional[int]
    description: str
    solved: bool = False
    files: list[str] = field(default_factory=list)
    # Generic / HTB target info
    target_kind: str = "static"  # static, docker, fullpwn, unknown
    host: Optional[str] = None
    port: Optional[int] = None
    url: Optional[str] = None
    vpn_required: bool = False
    # Raw platform payload ...
    raw: dict[str, Any] = field(default_factory=dict)
```

另有只读属性 `slug`（`base.py:43-47`，名字小写去非字母数字转 `-`）与 `connection_info`（`base.py:49-58`，优先 `url`，其次 `host:port`，再 `host`）。

### 1.2 BasePlatform 接口

`platforms/base.py:71-126`：

- **抽象方法（必须实现）**：`list_challenges()`（80）、`get_challenge(challenge_id)`（83-84）、`download_files(challenge, dest_dir)`（90-91）、`submit_flag(challenge, flag) -> SubmitResult`（115-116）。
- **实例管理（默认 no-op）**：`needs_instance()`（100-101，`target_kind in ("docker","fullpwn")` 返回 True）、`start_instance()`（103）、`stop_instance()`（106）、`instance_status()`（109）。
- **可选能力**：`get_scoreboard()`（122-123）、`close()`（125-126）。
- **SubmitResult 数据类** `base.py:61-68`：`accepted: bool`、`message: str`、`raw: dict`、`already_solved: bool = False`。

### 1.3 CTFd 适配器：登录 / CSRF / 提交

登录（cookie 会话）与 CSRF nonce 抓取在 `platforms/ctfd.py:22-52`：

```python
self.s.headers["Cookie"] = f"session={session_cookie}"   # ctfd.py:30
self.nonce = self._fetch_nonce()                          # ctfd.py:32

def _fetch_nonce(self) -> str:
    r = self.s.get(f"{self.base_url}/challenges", timeout=20)
    m = re.search(r"['\"]?csrf_?[Nn]once['\"]?:\s*['\"]([a-f0-9]+)['\"]", r.text)
    ...
    m = re.search(r'<meta name="csrf-token" content="([a-f0-9]+)">', r.text)
    ...
    self.s.headers["CSRF-Token"] = nonce                  # ctfd.py:42/47
```

提交在 `platforms/ctfd.py:81-95`：

```python
def submit_flag(self, challenge_id: int, flag: str) -> Dict[str, Any]:
    if "CSRF-Token" not in self.s.headers or not self.s.headers["CSRF-Token"]:
        self._fetch_nonce()
    r = self.s.post(f"{self.base_url}/api/v1/challenges/attempt",
        json={"challenge_id": challenge_id, "submission": flag}, ...)
    r.raise_for_status()
    return r.json()
```

适配器层 `CTFdPlatform.submit_flag`（`ctfd.py:188-199`）把 `success is True` 且响应文本不含 `incorrect`/`wrong` 判定为 accepted。

**一句话机制总结**：Koshary 用 `BasePlatform` + `NormalizedChallenge`/`SubmitResult` 把 CTFd/HTB 归一化，CTFd 适配器靠 `session=` Cookie 登录、从 `/challenges` 页面正则抓 `CSRF-Token`，提交走 `/api/v1/challenges/attempt` 并在无 nonce 时重抓。

---

## 2. 提交与限流（429 / 冷却 / 重复提交 / 错误上限 / 报错分类）

### 2.1 无 429 / 限流 / 冷却 / 重试处理（关键结论）

全仓库 grep `429|cooldown|rate.?limit|backoff|retry` 只在文档/注释/`run_gemini.sh` 中出现，**提交路径没有 429/限流/冷却/重试逻辑**：

- CTFd：`ctfd.py:94` 直接 `r.raise_for_status()`，429 会抛 `HTTPError`。
- HTB cookie：`core/htb_cookie_client.py:191-198` 只对 401/403 抛 `HTBAuthError`，其余非 2xx 在 `_json`（`212-213`）统一抛 `RuntimeError`，无退避。
- HTB MCP：`platforms/htb_ctf_mcp.py` 通过 `_call_role`（`150-158`）→ `_raise_on_error`（`160-167`）只识别 403/forbidden。

### 2.2 重复提交防护

`orchestrator.py:955` 与 `orchestrator.py:549-553`：

```python
if db.attempted_flag(cid, flag):
    continue
...
def attempted_flag(self, chall_id, flag: str) -> bool:
    return any(str(x["challenge_id"]) == str(chall_id) and x["flag"] == flag
               for x in self.data.get("submitted", []))
```

### 2.3 错误提交上限（max_wrong）

`orchestrator.py:968-970`：

```python
if max_wrong > 0 and db.count_attempts(cid) >= max_wrong:
    log_warn(f"Wrong-submission limit ({max_wrong}) reached for #{cid}; not submitting '{flag}'.")
    return False
```

`count_attempts`（`555-559`）统计 `submitted` 里该 challenge 的全部记录。默认值在 `orchestrator.py:1344-1349`：HTB 取配置 `max_wrong_submissions_per_challenge`（默认 3），**CTFd `max_wrong = 0` 表示不限**。注意：提交时发生异常也会 `db.mark_attempt(cid, flag, {"error": str(e)})`（`973-978`），因此**报错提交同样计入 max_wrong**。

### 2.4 平台报错分类

- CTFd `ctfd.py:191-198`：`accepted = resp.get("success") is True and not any(bad in msg_text for bad in ("incorrect","wrong"))`。
- HTB MCP `htb_ctf_mcp.py:498-516` `_interpret_submission`：先看 `correct/accepted/success/solved/is_correct` 布尔字段，否则用 `correct/accepted/solved` 与 `incorrect/wrong/invalid/not correct` 关键词组合判断，`already` 单独标记 `already_solved`。
- HTB cookie `htb_cookie.py:214-223`：`accepted = bool(res.get("accepted")) or "already" in message`。

**一句话机制总结**：提交链路**完全没有 429/限流/冷却/重试**，只有 `attempted_flag` 去重 + `max_wrong` 硬上限（CTFd 默认 0=不限，HTB 默认 3，异常提交也计数），平台报错靠 `SubmitResult.accepted/message` 关键词或布尔字段分类。

---

## 3. plan 阶段（Koshary）

`orchestrator.py:677-708` `generate_challenge_plan`：

```python
plan_prompt = (
    f"You are a CTF Planning Agent. Analyze the following challenge and files to create a high-level strategy.\n\n"
    f"Challenge: {chall.name}\nCategory: {chall.category}\nDescription: {chall.description}\n"
    f"Connection: {chall.connection_info}\n"
    f"Files: {chall.files}\n\n"
    "Your output should be a structured markdown plan. Do not write code yet. "
    "Focus on: Potential vulnerabilities, Required tools, and Execution steps."
)
...
rc, output = run_model(runner_cmd, prompt_file, chdir, timeout=timeout)
cleaned_plan = clean_model_output(output)
if rc != 0 or not cleaned_plan:
    log_warn(f"Strategy generation failed ... Proceeding without plan.")
    return "No plan generated."
(chdir / "plan.md").write_text(cleaned_plan, encoding="utf-8")
```

- **模型**：plan 直接复用当前题型路由解出的 `runner_cmd`（`orchestrator.py:821` 传入 `generate_challenge_plan(chall, runner_cmd, ...)`），即与求解同一模型 CLI，没有独立的 planning 模型。
- **prompt 结构**：单段自然语言，注入 name/category/description/connection/files，要求输出 markdown 计划、不写代码，聚焦"潜在漏洞/所需工具/执行步骤"。
- **注入后续执行**：plan 文本写入 `plan.md`（`707`），并通过 `render_prompt` 的 `plan=` 模板变量（`orchestrator.py:458`）在每轮 prompt 中注入（`orchestrator.py:871` `plan=plan_txt`）。
- **质量校验/降级**：**无质量校验**；唯一降级是 `rc != 0 or not cleaned_plan` 时返回 `"No plan generated."`（`703-705`）。仅当 `--plan`（`enable_planning=True`）启用（`820-822`、argparser `1013`）。

**一句话机制总结**：plan 复用题型路由的同一 runner CLI，用一段 "You are a CTF Planning Agent" 自然语言 prompt 产 markdown 计划，写入 plan.md 后作为 `{plan}` 变量注入每轮求解 prompt，无质量校验、失败仅降级为 `"No plan generated."`。

---

## 4. 状态与锁（StateDB + STATE_LOCK + 每题 .lock）

### 4.1 StateDB 数据结构

`orchestrator.py:467-481`（默认 schema）：

```python
self.data = load_json(path, default={
    "solved_ids": [],
    "submitted": [],
    "challenges": {},
    "errors": [],
    "stats": {"runs": 0, "model_rounds": 0, "artifacts_executed": 0, "commands_executed": 0}
})
```

方法（`467-569`）：`save`（484-486）、`is_solved`（488-489）、`inc_stat`（491-495）、`mark_seen`（497-512，写 challenges 条目的 id/platform/event_id/name/category/value/target_kind/route/status/workdir/last_update）、`mark_status`（514-521）、`mark_solved`（523-537，追加 solved_ids + submitted + 置 status=solved/solved_flag）、`mark_attempt`（539-547，追加 submitted）、`attempted_flag`（549-553）、`count_attempts`（555-559）、`add_error`（561-569，追加 errors 条目 ts/stage/challenge_id/message）。

### 4.2 STATE_LOCK 并发写保护

全局锁 `orchestrator.py:73`：`STATE_LOCK = threading.Lock()`。每个写方法都用 `with STATE_LOCK:` 包住"内存变更 + `save_json`"：

```python
def save(self) -> None:
    with STATE_LOCK:
        save_json(self.path, self.data)          # orchestrator.py:484-486
def inc_stat(self, key, delta=1) -> None:
    with STATE_LOCK:
        ...
        save_json(self.path, self.data)          # orchestrator.py:491-495
def mark_solved(...):
    with STATE_LOCK: ... save_json(...)          # orchestrator.py:523-537
```

`mark_seen/mark_status/mark_attempt/add_error` 均同模式（`497-512`/`514-521`/`539-547`/`561-569`）。

### 4.3 每题工作区 .lock

原子创建 `orchestrator.py:661-671`：

```python
def acquire_lock(lock_path: Path) -> bool:
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)  # 原子独占
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError: return False

def release_lock(lock_path: Path) -> None:
    with contextlib.suppress(FileNotFoundError): lock_path.unlink()
```

用法 `orchestrator.py:790-792` 与 `926-927`：`process_challenge` 开头 `lock_path = chdir / ".lock"`，`if not acquire_lock(lock_path): return {"status": "locked", ...}`；`finally: release_lock(lock_path)`。另有 `instance.lock`（`807-814`）保护 HTB 实例准备。

**一句话机制总结**：StateDB 是单 JSON 文件（solved_ids/submitted/challenges/errors/stats 五块），所有写操作都包在全局 `STATE_LOCK` 内"改内存+落盘"；每题用 `chdir/.lock` 以 `O_CREAT|O_EXCL` 原子抢占，抢不到直接返回 `locked`，`finally` 释放。

---

## 5. 路由表（config.json 约 68-89 行）

`config.json:68-89` 完整内容：

```json
"routing": {
  "web": "gemini", "web exploitation": "gemini",
  "crypto": "claude", "cryptography": "claude",
  "pwn": "codex", "binary exploitation": "codex",
  "reverse": "claude", "rev": "claude", "reverse engineering": "claude",
  "mobile": "gemini", "android": "gemini",
  "misc": "claude", "miscellaneous": "claude",
  "forensics": "gemini", "dfir": "gemini",
  "fullpwn": "codex", "full-pwn": "codex", "machine": "codex",
  "hardware": "codex", "blockchain": "claude"
}
```

静态映射逻辑 `orchestrator.py:214-219` `choose_route`（子串包含匹配）：

```python
def choose_route(category, routing):
    c = category.lower().strip()
    for key, route in routing.items():
        if key in c:
            return route
    return None
```

随后 `resolve_model_key` / `choose_model_key`（`orchestrator.py:247-284`）把 route+category 细化为模型 key（如 `codex`+`pwn`→`codex_pwn`、`codex`+`rev`→`codex_rev`、`gemini`+`misc`→`gemini_misc`、`gemini`+`forensics`→`gemini_forensics`），再到 `config.json:90-127` 的 `models` 表取 `runner`（`bash runners/run_*.sh`）与 `prompt_template`（`prompts/*.txt`）。

**一句话机制总结**：路由是 config.json 的静态"类别关键字→模型名"表（web/forensics/mobile→gemini，crypto/reverse/misc/blockchain→claude，pwn/fullpwn/hardware→codex），`choose_route` 用子串匹配命中，再经 `choose_model_key` 细化为具体 runner+prompt 模板。

---

## 6. LLM-CTF-Solver 僵局检测（6 维 D1-D6）

`solve_agent.py:1393-1479` `_detect_stuck_step`。各维精确条件：

- **D2 连续相同工具**（`1407-1418`）：`self._recent_tools` 维护最近 8 个工具名；取 `recent = self._recent_tools[-4:]`，`Counter(recent).most_common(1)[0][1] >= 3` 触发（最近 4 次里同一工具 ≥3 次）。
- **D3 输出语义相似度**（`1420-1436`）：取 `output[:300]` 维护最近 5 个样本；触发条件为**最近 3 个样本完全相同**（`last3[0] == last3[1] == last3[2]`）**或** `SequenceMatcher(None, prev, latest).ratio() > 0.85`（与最近 3 个前序样本任一相似度 >85%）。
- **D4 思考意图循环**（`1438-1447`）：`_recent_thoughts` 由 `_extract_thought_intent`（`1383-1391`，提取 curl/http/jwt/flag/爆破/枚举 等关键词）生成，长度 ≥5 时 `Counter(self._recent_thoughts).most_common(1)[0][1] >= 3` 触发（同一意图 ≥3 次）。注入点 `605`，上限 8。
- **D6 工具错误率**（`1449-1457`）：`_recent_tool_errors`（bool 列表，注入点 `451`，上限 10）长度 ≥5 时 `error_rate = sum/len >= 0.6` 触发（最近 ≥5 步中 ≥60% 报错；报错判定关键词见 `444-450`）。
- **D1 LLM 标记无进展**（`1463-1473`）：`progress == "none" and step_count >= 5`，且需规则弱信号配合 `self._recent_tools[-4:].count(tool_name or "none") >= 2`（最近 4 次里当前工具 ≥2 次）。
- **D5 步数多但无进展**（`1475-1477`）：`step_count >= 15 and not self._has_progress(analysis)`。

触发汇总 `1459-1461`：D2/D3/D4/D6 任一独立触发即 `return True`；D1、D5 走各自分支。`_has_progress`（`1481-1496`）：`progress_level in ("significant","moderate")` 为真；pentest 模式另看 `vulnerability_found`/`attack_surface_covered`，ctf 模式另看 `flag_found` 或分析文本含 `flag{`/`flag`。

**一句话机制总结**：6 维中 D2（近4次同工具≥3）、D3（输出 3 连同或相似度>0.85）、D4（意图≥3次）、D6（近5步错误率≥60%）任一独立命中即判僵局；D1（progress=none 且 step≥5 且当前工具近4次≥2）、D5（step≥15 且无 progress）各自单独触发。

---

## 7. LLM-CTF-Solver 解析回退（原生 → JSON → XML → general_next）

`solve_agent.py:863-1013` `next_instruction` 三层解析：

1. **原生 tool_calls**（`954-972`）：`response.choices[0].message.tool_calls` 存在时 `ToolUtils.parse_tool_calls(response)`，校验 `tname in self.tools or tname in self.tool._dynamic_tool_map`，未知工具名触发动态发现 `inject_dynamic_tools`。
2. **文本提取（堆栈法 JSON → XML）**（`975-992`）：`self._extract_tool_calls_from_content(think_content)`（`821-842`）先用 `_extract_json_blocks`（`844-861`，栈法匹配平衡 `{}` 并按长度降序）逐个 `json.loads`，支持 `{"tool_calls":[...]}` 数组和单工具 `{"name"/"tool_name", "arguments"}`；失败则回退 `ToolUtils._parse_xml_tool_calls(content)`（`839`）。
3. **general_next 回退**（`994-1013`）：上两层都无有效工具时，`extract_tool_mentions` + `recommend_tools` 后用 `self.tool_general(history_summary, think_content, tools)` 再生成（`tool_general` 定义在 `1343-1364`，渲染 `general_next` 模板、`json_check=True`、再 `parse_tool_calls`）。

底层 `ToolUtils.parse_tool_calls`（`utils/tools.py:505-559`）同样按"原生 tool_calls → `json.loads`（坏 JSON 用 `fix_json_with_llm` 修）→ `_parse_xml_tool_calls`"回退。XML 解析 `utils/tools.py:477-502`：

```python
m = re.search(r'<tool_calls>(.*?)</tool_calls>', content, re.DOTALL)
root = ET.fromstring("<tool_calls>" + m.group(1) + "</tool_calls>")
for tc in root.findall("tool_call"):
    name = tc.get("name", ""); args = {arg.get("key",""): (arg.text or "").strip() for arg in tc.findall("arg")}
```

**一句话机制总结**：先吃原生 `tool_calls`，失败后用"栈法平衡括号"提取 JSON（数组/单工具两格式）再回退 `<tool_calls>` XML，仍未命中则由 `general_next` 模板重新生成一次工具调用，且每层都对工具名做合法性/动态发现校验。

---

## 8. LLM-CTF-Solver 记忆与断点

### 8.1 三层记忆结构

`memory.py:36-40`：

```python
self.history: List[Dict] = []              # 最近详细步骤
self.journal_entries: List[str] = []        # 最近 N 条日志（保持原样）
self.consolidated_narrative: str = ""       # 早期日志的整合叙事
self.failed_attempts: Dict[str, int] = {}
self._external_facts: List[str] = []        # 外部注入事实（策略切换等）
```

- **整合触发** `add_journal_entry`（`83-90`）：`len(journal_entries) >= _CONSOLIDATION_INTERVAL * 2`（`12` 行 `_CONSOLIDATION_INTERVAL = 5`）即触发 `_consolidate_journal`，整合时保留最近 5 条原样，其余交给 LLM 合并为叙事。
- **整合原则** `_consolidate_journal`（`152-204`）：prompt 强调"宁可长不可丢、IP/端口/URL/payload/凭据/失败记录一个不能少"；成功路径叙事超 `_NARRATIVE_MAX_CHARS = 10000` 触发二次压缩（`188-190`、`234-255`），LLM 失败降级 `_fallback_consolidate`（`206-232`，只保留 `## 步N` 标题骨架，上限 6000）。
- **汇总读取** `get_summary`（`94-148`）：叙事 → 最近日志 → 外部事实（最近 5 条）→ 最近 6 条详细步骤（含 output_summary/analysis/历史失败次数）。

### 8.2 关键事实防丢

`memory.py:16-29` `_PROTECTED_PATTERNS` 覆盖 flag、password/passwd、Bearer token、JWT 三段式、mysql/postgres/mongodb/redis 连接串、AWS Access Key、PRIVATE KEY 头、/etc/shadow 行、SHA256/MD5 哈希。

`memory.py:257-290` `_ensure_protected_facts`：整合完成后（`204` 调用）扫描本次源文本，对每个受保护模式 `pattern.finditer(source_text)` 提取事实，若 `fact not in self.consolidated_narrative` 则强制追加：

```python
block = "\n\n## 关键事实（防丢）\n"
for f in missing:
    block += f"- `{f}`\n"
self.consolidated_narrative += block
```

### 8.3 Checkpoint 断点续跑

存档（`agent/checkpoint.py`）：键 `_problem_key`（`26-30`）= `f"{mode}_{md5(problem)[:12]}"`；`save`（`33-114`）写 `checkpoints/checkpoint_{key}_{timestamp}.json`，同 key 保留最近 3 个，内容分 `meta`（mode/problem_key/step_count/problem_text/phase/format v2）、`agent_state`（auto_mode、stuck_counter、recent_tools、recent_output_samples、tried_strategies、recent_thoughts、recent_tool_errors、llm_failure_count、current_phase 等）、`memory`（history/journal_entries/consolidated_narrative/external_facts/failed_attempts）。

读档（`checkpoint.py:116-141`）：`load` 按 `_problem_key` 找文件，按 mtime 取最新。恢复实现 `solve_agent.py:226-258` `_restore_from_checkpoint`：恢复 `_step_count`、各计数/滑动窗口、`memory.*`；入口 `main.py:130-137`（`--resume` 调 `CheckpointManager.load`，未命中则从头）。

存档时机：每 `checkpoint_interval`（默认 5）步存一次（`solve_agent.py:671-672`），成本上限/LLM 失败/异常等路径也会存（`345/351/360/369/375/405/641/662/667`），解出 flag 后 `_clear_checkpoint`（`273`、`514`）清掉该 key。

**一句话机制总结**：记忆分"最近详细步骤 history / 最近日志 journal_entries / 早期整合叙事 consolidated_narrative"三层，日志每积 10 条经 LLM 合并（失败降级为标题骨架）并在整合后扫描 12 类受保护模式强制补齐关键事实；checkpoint 以 `mode+md5(problem)[:12]` 为键，每 5 步序列化 agent_state+memory 到 `checkpoints/checkpoint_*.json`（保留 3 份），`--resume` 按键取最新恢复后继续。
