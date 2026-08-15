# verialabs/ctf-agent 源码研究报告

目标仓库：`D:\ctf-agent\ctf-agent-ref`（verialabs/ctf-agent，BSidesSF 2026 52/52 冠军，MIT）
架构一句话：一个 coordinator LLM + 每题的 solver swarm（多模型并行竞速）+ 每模型一个 Docker 沙箱。

---

## 1. 同题多模型竞速（asyncio FIRST_COMPLETED + "NEVER kill / Cost is not a concern"）

### FIRST_COMPLETED 机制

`backend/agents/swarm.py:295-328`：

```python
async def run(self) -> SolverResult | None:
    """Run all solvers in parallel. Returns the winner's result or None."""
    tasks = [
        asyncio.create_task(self._run_solver(spec), name=f"solver-{spec}")
        for spec in self.model_specs
    ]

    try:
        while tasks:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

            for task in done:
                try:
                    result = task.result()
                except Exception:
                    continue
                if result and result.status == FLAG_FOUND:
                    self.cancel_event.set()
                    for p in pending:
                        p.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    return result

            tasks = list(pending)
```

关键点：
- `swarm.py:297-300` 按 `self.model_specs`（`DEFAULT_MODELS`，见 `models.py:21-27`：claude-opus-4-6/medium、/max、codex/gpt-5.4、gpt-5.4-mini、gpt-5.3-codex 共 5 个）创建独立 task。
- `swarm.py:304` 用 `asyncio.wait(..., return_when=asyncio.FIRST_COMPLETED)` 只要有一个 solver 结束就返回；`swarm.py:311` 只有 `status == FLAG_FOUND` 才算赢家，否则把未完成任务放回 `pending` 继续等（`swarm.py:318`）。
- 赢家出现后：`swarm.py:312` 置 `cancel_event`，`swarm.py:313-315` 取消所有 pending 并 `gather(return_exceptions=True)` 收尾。
- 另一层：solver 内部一旦自己找到 flag 也会先置 `cancel_event`（`swarm.py:229-231`）：

```python
if result.status == FLAG_FOUND:
    self.cancel_event.set()
    self.winner = result
```

`cancel_event` 在 `swarm.py:59` 定义，被传给所有 solver 构造（`swarm.py:91,107`），用于跨模型广播"停"。

### "NEVER kill swarm / Cost is not a concern" 哲学

体现在 coordinator 的 system prompt，两个后端重复相同文字：

`backend/agents/codex_coordinator.py:40-47`：

```python
CRITICAL RULES:
- NEVER kill a swarm. Solvers will keep trying indefinitely with different approaches.
  Even when stuck, they often unstick themselves after several bumps. Your job is to
  HELP them, not give up on them. The only time a swarm should die is when the flag
  is confirmed correct.
- When a solver seems stuck, bump it with very specific technical guidance based on
  its trace. Tell it exactly what to try next — specific tools, techniques, approaches.
- Cost is not a concern. Keep all swarms running.
```

`backend/agents/claude_coordinator.py:44-51` 同文。README 也呼应：`README.md:55` "Solvers never give up — they keep trying different approaches until the flag is found."

**代码层的落实**：kill 不是被禁止的硬约束（coordinator 仍有 `kill_swarm` 工具，`coordinator_core.py:114-119`），而是靠 prompt 软约束 + solver 无限循环（见问题 8）。真正让 swarm 死掉只有两条路径：flag 确认（`swarm.py:229-231/311-316`）和外部"该题已被解"自动杀（`coordinator_loop.py:121-127`，`swarm.kill()` 只置 cancel_event，见 `swarm.py:330-332`）。

**一句话机制总结**：swarm 用 `asyncio.wait(FIRST_COMPLETED)` 让所有模型并发跑、谁先出 `FLAG_FOUND` 谁赢，赢者触发 `cancel_event` 取消其余任务；"不杀 swarm/不在乎成本"是 coordinator prompt 的明文规则，代码上对应 solver 的无限重试循环 + 只有"已确认 flag"或"题被解"才真正取消。

---

## 2. 提交纪律（swarm.py 153-192：递增冷却 + 精确去重 + flag 锁）

`backend/agents/swarm.py:152-192`：

```python
# Escalating cooldowns after incorrect submissions (per model)
SUBMISSION_COOLDOWNS = [0, 30, 120, 300, 600]  # 0s, 30s, 2min, 5min, 10min

async def try_submit_flag(self, flag: str, model_spec: str) -> tuple[str, bool]:
    """Cooldown-gated, deduplicated flag submission. Returns (display, is_confirmed)."""
    async with self._flag_lock:
        if self.confirmed_flag:
            return f"ALREADY SOLVED — flag already confirmed: {self.confirmed_flag}", True

        normalized = flag.strip()

        # Dedup exact flags across all models
        if normalized in self._submitted_flags:
            return "INCORRECT — already tried this exact flag.", False

        # Escalating cooldown after incorrect submissions
        wrong_count = self._submit_count.get(model_spec, 0)
        cooldown_idx = min(wrong_count, len(self.SUBMISSION_COOLDOWNS) - 1)
        cooldown = self.SUBMISSION_COOLDOWNS[cooldown_idx]
        if cooldown > 0:
            last_time = self._last_submit_time.get(model_spec, 0)
            elapsed = time.monotonic() - last_time
            if elapsed < cooldown:
                remaining = int(cooldown - elapsed)
                return (
                    f"COOLDOWN — wait {remaining}s before submitting again. "
                    f"You have {wrong_count} incorrect submissions. "
                    "Use this time to do deeper analysis and verify your flag.",
                    False,
                )

        self._submitted_flags.add(normalized)

        from backend.tools.core import do_submit_flag
        display, is_confirmed = await do_submit_flag(self.ctfd, self.meta.name, flag)
        if is_confirmed:
            self.confirmed_flag = normalized
        else:
            self._submit_count[model_spec] = wrong_count + 1
            self._last_submit_time[model_spec] = time.monotonic()
        return display, is_confirmed
```

配套数据结构（`swarm.py:63-67`）：

```python
confirmed_flag: str | None = None
_flag_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
_submit_count: dict[str, int] = field(default_factory=dict)  # per-model wrong submission count
_submitted_flags: set[str] = field(default_factory=set)  # dedup exact flags
_last_submit_time: dict[str, float] = field(default_factory=dict)  # per-model last submit timestamp
```

逐条回答：
- **冷却表**：`[0, 30, 120, 300, 600]` 秒（第 153 行），即第 1 次错后 30s、第 2 次后 2min、第 3 次后 5min、第 4 次及以后 10min。
- **计数维度**：`_submit_count` 与 `_last_submit_time` 都以 `model_spec`（模型）为 key（第 65、67 行），是**按模型**计数，不是按题。swarm 本身就是单题对象，所以"按模型"即"同一题内按模型"。
- **去重维度**：`_submitted_flags` 是全局（跨所有模型）的精确 flag 集合，`normalized = flag.strip()` 后精确匹配（第 161-165 行）。
- **错误提交达到上限后做什么**：**没有硬上限/封禁**。`cooldown_idx = min(wrong_count, len(...)-1)`（第 169 行）把冷却封顶在最后一项 600s。超过 4 次错后每次仍可提交，只是每次都强制等满 10 分钟，模型永远不会被"禁赛"，只会被拉长等待并收到"去深入分析"的提示（第 176-181 行）。
- **锁**：`_flag_lock`（`asyncio.Lock`，第 64 行）串行化整个"检查-去重-冷却-提交-记账"临界区，防并发竞态。
- 所有提交都经此路径：solver 的 `submit_flag` 工具走 `deps.submit_fn`（`tools/flag.py:19-24`），`submit_fn` 就是 swarm 里绑定的 `try_submit_flag`（`swarm.py:79,141`）。

**一句话机制总结**：每模型一条 5 档递增冷却（0/30s/2m/5m/10m，封顶 10m 且永不禁赛），跨模型精确去重 flag，`asyncio.Lock` 串行化；错误提交只延后、不封杀。

---

## 3. message bus（message_bus.py 全 54 行）

`backend/message_bus.py`（完整 54 行）：

```python
@dataclass
class Finding:
    model: str
    content: str
    timestamp: float = field(default_factory=time.time)

MAX_FINDINGS = 200

@dataclass
class ChallengeMessageBus:
    """Append-only shared findings list with per-model cursors."""

    findings: list[Finding] = field(default_factory=list)
    cursors: dict[str, int] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def post(self, model: str, content: str) -> None:
        """Post a finding from a solver."""
        async with self._lock:
            self.findings.append(Finding(model=model, content=content))
            if len(self.findings) > MAX_FINDINGS:
                trim = len(self.findings) - MAX_FINDINGS
                self.findings = self.findings[trim:]
                self.cursors = {k: max(0, v - trim) for k, v in self.cursors.items()}

    async def check(self, model: str) -> list[Finding]:
        """Get unread findings from other models. Advances the cursor."""
        async with self._lock:
            cursor = self.cursors.get(model, 0)
            unread = [f for f in self.findings[cursor:] if f.model != model]
            self.cursors[model] = len(self.findings)
            return unread

    async def broadcast(self, content: str, source: str = "coordinator") -> None:
        """Coordinator broadcasts a message to all solvers."""
        await self.post(source, content)

    def format_unread(self, findings: list[Finding]) -> str:
        """Format findings for injection into a solver prompt."""
        if not findings:
            return ""
        parts = [f"[{f.model}] {f.content}" for f in findings]
        return "**Findings from other agents:**\n\n" + "\n\n".join(parts)
```

机制逐条：
- **findings 怎么 append**：`post()`（第 28-35 行）加锁追加 `Finding(model, content, timestamp)`；超过 `MAX_FINDINGS=200` 时裁掉头部最旧记录，并把所有游标同步左移 `trim`（第 32-35 行），保证游标不越界。
- **每模型游标**：`cursors: dict[str, int]`（第 25 行），每个模型一个整数偏移。`check(model)`（第 37-43 行）读 `self.cursors.get(model, 0)` 作为起点。
- **只回传"别人"的未读**：第 41 行 `if f.model != model` 过滤掉自己发的；只取 `findings[cursor:]` 即游标之后的新条目。
- **防回声**：靠两层——① 第 41 行按 `f.model != model` 排除自己；② 第 42 行读完立刻把游标推进到 `len(self.findings)`，下次只读新增，已读的不再重复注入。
- **注入时机**：每个 solver 每 5 步调用一次（`solver.py:87-92`、`claude_solver.py:241-250`、`codex_solver.py:409-413`），经 `tools/core.py:155-162` 的 `do_check_findings` 调 `check()` + `format_unread()`；`broadcast()`（第 45-47 行）是 coordinator 广播（`coordinator_core.py:174-180`）。
- swarm 侧在 solver 一轮结束时也会 `post` 其 findings summary（`swarm.py:226-227`，`message_bus.post(model_spec, ...[:500])`）。

**一句话机制总结**：单题内一个 append-only 的共享 findings 列表 + 每模型一个整数游标，`check()` 只返回"游标之后且非本人"的条目并推进游标，实现只读别人新发现、绝不回声。

---

## 4. LoopDetector（loop_detect.py 完整检测签名）

`backend/loop_detect.py:10-49`：

```python
@dataclass
class LoopDetector:
    """Track recent tool call signatures to detect repetitive loops."""

    window: int = 12
    warn_threshold: int = 3
    break_threshold: int = 5
    _recent: deque[str] = field(init=False)

    def __post_init__(self) -> None:
        self._recent = deque(maxlen=self.window)

    def check(self, tool_name: str, args: dict | str | None = None) -> str | None:
        if args:
            raw = json.dumps(args, sort_keys=True) if isinstance(args, dict) else str(args)
            sig = f"{tool_name}:{raw[:500]}"
        else:
            sig = tool_name
        self._recent.append(sig)

        count = sum(1 for s in self._recent if s == sig)
        if count >= self.break_threshold:
            return "break"
        if count >= self.warn_threshold:
            return "warn"
        return None
```

- **重复什么**：`tool_name + ":" + json.dumps(args, sort_keys=True)[:500]`（第 30-34 行）——即"工具名 + 参数（字典按 key 排序后的 JSON，截 500 字符）"构成的签名；重复**完全相同的签名**才计数。
- **阈值**：滑动窗口 `window=12`（最近 12 次调用），`warn_threshold=3`、`break_threshold=5`（第 14-16 行）；窗口内同一签名出现 ≥3 次返回 `"warn"`，≥5 次返回 `"break"`（第 37-42 行）。
- **触发后干什么——是警告注入，不是终止**：
  - `"break"`：Pydantic solver 直接把 `LOOP_WARNING_MESSAGE` 当工具结果返回（`solver.py:68-72`，模型根本不会真正执行该工具）；Claude SDK 用 PreToolUse hook 返回 `permissionDecision: "deny"` 拒绝执行（`claude_solver.py:130-138`）；Codex 直接替换结果为提示文本（`codex_solver.py:390-392`）。
  - `"warn"`：把 `LOOP_WARNING_MESSAGE` 追加到正常结果后面（`solver.py:80-81`、`codex_solver.py:395-397`、`claude_solver.py:139-142`）。
  - 警告文案在 `loop_detect.py:52-59`（"你卡在循环里……停止重复，换一个完全不同的技术/工具"）。
- **何时重置**：`bump()` 时 `loop_detector.reset()`（`solver.py:283`、`claude_solver.py:360`、`codex_solver.py:525`）。

**一句话机制总结**：跟踪最近 12 次"工具名+排序参数"签名，窗口内同一签名 ≥3 次注入警告、≥5 次阻止本次工具执行并注入换思路警告——只软性打断，从不终止 agent。

---

## 5. 结构化输出约束（output_types.py + prompts 强制）

`backend/output_types.py`（全 26 行）：

```python
class FlagFound(BaseModel):
    flag: str
    method: str  # brief description of how


def solver_output_json_schema() -> dict:
    """JSON schema for solver structured output — shared by Claude SDK and Codex.

    Only flag_found is allowed — solvers must keep working until they find a flag.
    No gave_up option forces persistent solving behavior.
    """
    return {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["flag_found"]},
            "flag": {"type": "string"},
            "method": {"type": "string"},
        },
        "required": ["type", "flag", "method"],
        "additionalProperties": False,
    }
```

**强制方式**（三后端都绑定同一 schema）：
- Pydantic AI：`Agent(..., output_type=FlagFound)`（`solver.py:182-189`）。
- Claude SDK：`output_format={"type": "json_schema", "schema": solver_output_json_schema()}`（`claude_solver.py:264`）。
- Codex：`turn/start` 时传 `outputSchema: solver_output_json_schema()`（`codex_solver.py:481-485`）。

**解析与失败处理**：
- Pydantic：`result.output` 判 `isinstance(output, FlagFound)`（`solver.py:243-246`）；若模型输出不符合 schema，pydantic-ai 会抛异常，被 `run_until_done_or_gave_up` 的 `except Exception` 捕获并返回 `ERROR`（`solver.py:261-265`），该 ERROR 会被 swarm 视为"连错 3 次则放弃该模型"或 bump（见问题 8）。
- Claude：`output.get("type") == "flag_found"` 才取 flag（`claude_solver.py:329-335`）；没有匹配的 structured_output 时走 `GAVE_UP`（`claude_solver.py:340-345`）。
- Codex：从 `item/completed` 里抓 JSON 字典塞进 `self._structured_output`（`codex_solver.py:305-311`），turn 结束后 `if self._structured_output: if type == "flag_found"`（`codex_solver.py:501-506`）；没有则 `GAVE_UP`（第 510 行）。
- prompt 侧辅助：`prompts.py:177` "6. Once CORRECT: output `FLAG: <value>` on its own line."，但真正的硬约束是 schema（`enum: ["flag_found"]` + `additionalProperties: False` 只允许 flag_found，**没有 gave_up 选项**，迫使模型持续解题——`output_types.py:14-15` 注释明说）。

**一句话机制总结**：统一 JSON schema 只允许 `{type:"flag_found", flag, method}`（无 gave_up），Pydantic 用 `output_type`、Claude 用 `output_format`、Codex 用 `outputSchema` 分别强制；解析失败→Pydantic 抛错走 ERROR，Claude/Codex 没拿到 flag_found 结构就走 GAVE_UP，由 swarm 决定 bump 重试。

---

## 6. 工作区/文件隔离（容器隔离 + tar 传输）

**不是** `/root/ctf/<cid>/w<idx>/` 命名空间，本仓库用的是**每模型一个独立 Docker 容器 + 每容器一个唯一 host 临时目录**：

- 每个 solver 各自 new 一个 `DockerSandbox`：`solver.py:131-135`、`claude_solver.py:69-73`、`codex_solver.py:149-153`。swarm 里 5 个模型 = 5 个容器（`swarm.py:82-114` 分别构造）。
- 唯一 workspace：`sandbox.py:114` `self.workspace_dir = tempfile.mkdtemp(prefix="ctf-workspace-")`，然后 `sandbox.py:120` 绑定进容器：

```python
workspace_dir = tempfile.mkdtemp(prefix="ctf-workspace-")
...
binds: list[str] = [f"{self.workspace_dir}:/challenge/workspace:rw"]
if Path(distfiles).exists():
    binds.append(f"{distfiles}:/challenge/distfiles:ro")
if Path(meta_yml).exists():
    binds.append(f"{meta_yml}:/challenge/metadata.yml:ro")
```

  - distfiles 只读（`:ro`）、workspace 读写（`:rw`），路径规整为 `/challenge/distfiles`、`/challenge/workspace`、`/challenge/metadata.yml`。
- 容器隔离参数（`sandbox.py:126-141`）：`CapAdd: SYS_ADMIN/SYS_PTRACE`、`seccomp=unconfined`、`Memory: 16g`、`NanoCpus: 2`、标签 `ctf-agent`（第 131 行）。
- 并发控制：`configure_semaphore(max_concurrent=50)`（`sandbox.py:29-32`）+ 启动计数告警（`sandbox.py:35-46`）。

**文件在 docker 与 agent 之间传（tar）**：
- 读：`read_file` 用 aiodocker `get_archive`（返回 tar），解包取文件，UTF-8 解码失败返回 bytes（`sandbox.py:210-234`）；`read_file_bytes` 包装（`236-241`）。
- 写：`write_file` 用 `tarfile` 打 tar 再 `put_archive` 进容器（`sandbox.py:243-264`）。
- 主机侧落盘：`copy_from` 读字节写 host 路径（`sandbox.py:266-270`）。

**防文件互踩**：
- 跨模型：容器彼此隔离，各写各的 `/challenge/workspace`（即各自的 host mkdtemp 目录），互不可见，天然无互踩。
- 容器内：`DockerSandbox._lock`（`asyncio.Lock`，`sandbox.py:88`）串行化 `exec`（`sandbox.py:155`），避免同一容器并发执行命令。
- 特例：quota 回退时复用同一沙箱——`swarm.py:247-253` 把旧 solver 的 `sandbox=None` 摘除（防 stop 销毁），再传给新的 Pydantic solver 并 `owns_sandbox=True`。

**一句话机制总结**：隔离靠"每模型一容器 + 每容器唯一 `tempfile.mkdtemp` host 目录绑定为 `/challenge/workspace`（distfiles 只读）"，文件靠 aiodocker 的 `get_archive`/`put_archive`（tar）传输；容器互相不可见，容器内用 asyncio 锁串行 exec，天然无跨模型文件互踩。

---

## 7. 轨迹与观测（tracing.py JSONL + poller diff/防抖）

### tracing.py 的 JSONL 结构

`backend/tracing.py`：

```python
def __init__(self, challenge_name, model_id, log_dir="logs"):
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    self.path = str(Path(log_dir) / f"trace-{_sanitize(challenge_name)}-{_sanitize(model_id)}-{ts}.jsonl")
    self._fh = open(self.path, "a")

def _write(self, event):
    self._fh.write(json.dumps({"ts": time.time(), **event}) + "\n")
    self._fh.flush()
```

- **一个 solver 一个文件**，命名 `trace-{challenge}-{model}-{timestamp}.jsonl`（第 21 行），append 模式，每次 `flush()` 以便 `tail -f`（第 35-40 行）。
- **记录字段**（每条都带 `ts` + `type`）：
  - `tool_call`：`tool`、`args`（截 2000）、`step`（第 42-44 行）
  - `tool_result`：`tool`、`result`（截 2000）、`step`（第 46-47 行）
  - `model_response`：`text`（截 1000）、`input_tokens`、`output_tokens`（第 49-51 行）
  - `usage`：`input_tokens`、`output_tokens`、`cache_read_tokens`、`cost_usd`（6 位小数，第 53-55 行）
  - 通用 `event(kind, **kwargs)`（第 57-58 行），实际用于 `start`、`stop`、`finish`（status/flag/confirmed/cost_usd）、`error`、`bump`、`flag_confirmed`、`loop_break`、`findings_injected`、`compact_requested`、`turn_complete`、`turn_failed` 等。
- **何时写**：工具调用前（`tool_call`）、工具返回后（`tool_result`，见 `solver.py:64,77`）；模型响应后（`solver.py:232-241`）；每轮结束写 `finish`（`solver.py:290`）；bump/stop/error 时写对应事件（`solver.py:264,284,290,301`）。
- **消费**：coordinator 的 `read_solver_trace` 读最后 N 行转摘要（`coordinator_core.py:133-171`）。

### poller.py 的 diff + 防抖

`backend/poller.py:85-120`（`_poll_once`）：

```python
async def _poll_once(self) -> None:
    try:
        stubs = await self.ctfd.fetch_challenge_stubs()
        current_names = {ch["name"] for ch in stubs}
        current_solved = await self.ctfd.fetch_solved_names()

        # Sanity check: if results look bogus compared to what we know, skip.
        if self._known_challenges and len(current_names) < len(self._known_challenges) // 2:
            logger.warning(f"Poll returned suspicious data ... — skipping")
            return
        # Don't let solved count regress (API might return empty on errors)
        if self._known_solved and not current_solved:
            logger.warning("Poll returned 0 solved (had %d) — skipping", len(self._known_solved))
            return

        # Detect new challenges
        new_challenges = current_names - self._known_challenges
        for name in new_challenges:
            self._event_queue.put_nowait(PollEvent("new_challenge", name))

        # Detect newly solved
        new_solves = current_solved - self._known_solved
        for name in new_solves:
            self._event_queue.put_nowait(PollEvent("challenge_solved", name))

        self._known_challenges = current_names
        self._known_solved = current_solved
```

- **diff**：维护 `_known_challenges` / `_known_solved` 两个 set，每次拉全量后做集合差 `current - known` 产生 `new_challenge` / `challenge_solved` 事件（第 100-114 行），然后整体覆盖 known（第 116-117 行）。
- **防抖**（两处 sanity check）：① 题目数疑似异常缩水（<已知一半）则整轮跳过（第 92-94 行）；② 已解数从非空回退到 0（API 报错返回空）则跳过（第 96-98 行）。轮询间隔 `interval_s=5.0`，循环 `await asyncio.sleep(self.interval_s)`（第 122-125 行）。
- 事件经 `asyncio.Queue` 传给 `get_event(timeout)` / `drain_events()`（第 60-75 行），coordinator 侧 `coordinator_loop.py:114-127` 消费并自动 spawn/自动 kill。

**一句话机制总结**：每个 solver 一个 append+flush 的 JSONL trace（tool_call/tool_result/model_response/usage/事件，均带 ts+step），coordinator 可读尾部做诊断；poller 每 5s 全量拉取后与已知集合做差产生增/解事件，并用"题目数异常缩水/已解数回退到 0"两道 sanity-check 跳过脏数据实现防抖。

---

## 8. 卡死处理（无限预算 + 仅逐调用超时 + 软 loop 打断）

**没有整体超时预算，真的一点不杀**（除非 flag 确认或题被外部解出）：
- `solver.py:204-209`：`usage_limits=UsageLimits(request_limit=None)`，无请求上限；swarm 外层 `swarm.py:218` `while not self.cancel_event.is_set():` 无限循环。
- Codex 等 turn 完成不设超时：`codex_solver.py:487` `await self._turn_done.wait()`（裸 wait，无 timeout）。
- Claude 收响应无限迭代直到 `cancel_event`：`claude_solver.py:304-306`。

**只有逐调用级超时**（防单条工具挂死，不杀 agent）：
- bash 默认 `timeout_seconds=60`（`tools/core.py:20`、`tools/sandbox.py:18`）；沙箱 `exec` 默认 `timeout_s=300`（`sandbox.py:151`），且容器内再包一层 `timeout --signal=KILL --kill-after=5 {timeout_s} bash -c ...`（`sandbox.py:162-171`）——进程级硬杀，超时返回 `exit_code=-1, stderr="Command timed out"`（第 195-199 行）。
- 读/写文件 `asyncio.wait_for(..., timeout=30)`（`sandbox.py:216,259`）；Codex RPC `timeout=300`（`codex_solver.py:245`）；Codex coordinator turn `timeout=120`（`codex_coordinator.py:191`）。
- 容器被删时 exec 返回 `"Container gone"`（`sandbox.py:158-160`）。

**陷入循环/反复失败的处理**：
- LoopDetector 只警告注入/阻止单次工具，不终止（见问题 4）。
- 一轮结束若没解出 → `GAVE_UP`，swarm 做 bump（注入兄弟模型见解后重跑），bump 间隔指数递增封顶 300s：`swarm.py:278-285` `timeout=min(bump_count * 30, 300)`，bump 会 `loop_detector.reset()`。
- 放弃该模型的少数例外（`swarm.py:258-274`）：`step_count==0 且 cost==0` 的"坏模型"直接 break；连续 3 次 `ERROR` 放弃（第 266-272 行）；`QUOTA_ERROR` 换 API 后端回退（`swarm.py:241-256`，映射表 `swarm.py:34-39`）。
- 唯一真正终止整题的：flag 确认置 `cancel_event`（`swarm.py:229-231/311-316`），以及 poller 检测到"题已被解"自动 `swarm.kill()`（`coordinator_loop.py:121-127`；`kill()` 只是 `cancel_event.set()`，`swarm.py:330-332`）。

**一句话机制总结**：solver 无整体时间/步数预算（`request_limit=None` + 无限 while + 无超时的 turn wait），真的一点不杀；只有 bash 60s/沙箱 exec 300s/文件 30s/RPC 300s 这类**逐调用**超时，loop 用警告注入软打断，反复失败靠 bump（间隔递增封顶 300s）重试，只在"flag 确认"或"题被外部解出"时取消整题。

---

## 附：关键文件清单

| 文件 | 作用 |
|------|------|
| `backend/agents/swarm.py` | ChallengeSwarm：多模型竞速、提交纪律、bump 循环 |
| `backend/agents/coordinator_core.py` | coordinator 工具实现（spawn/kill/bump/broadcast/read_trace） |
| `backend/agents/coordinator_loop.py` | 事件循环、poller 消费、自动 spawn/auto-kill |
| `backend/agents/{codex,claude}_coordinator.py` | coordinator 后端 + "NEVER kill / Cost is not a concern" prompt |
| `backend/message_bus.py` | 单题共享 findings + 每模型游标 |
| `backend/loop_detect.py` | 工具签名循环检测 |
| `backend/output_types.py` | FlagFound + 唯一 flag_found JSON schema |
| `backend/prompts.py` | system prompt 构建 |
| `backend/sandbox.py` | Docker 沙箱（每模型一容器、tar 文件传输、exec 超时） |
| `backend/tools/core.py` | 工具底层逻辑（bash/read/write/submit/check_findings） |
| `backend/tools/{sandbox,flag}.py` | Pydantic AI 工具封装 |
| `backend/tracing.py` | 每 solver JSONL 轨迹 |
| `backend/poller.py` | CTFd 轮询 + diff/防抖 |
| `backend/solver_base.py` | SolverResult / 状态常量 / 协议 |
| `backend/models.py` | DEFAULT_MODELS、provider 解析 |
| `backend/deps.py` | SolverDeps / CoordinatorDeps 共享依赖 |
