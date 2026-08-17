# verialabs/ctf-agent 源码精读 与 西湖论剑 AI Agent 赛道适配分析

> 分析对象：`D:\ctf-agent\ctf-agent-ref`（Veria Labs，BSidesSF 2026 52/52 冠军作品，公开第三方代码）
> 我们的目标：自研"西湖论剑 AI Agent 解题夺旗"打靶系统（DASCTF，Jeopardy，3 小时限时，仅 API 平台，Windows 编排器 + Kali REST 命令通道）
> 文中 `文件:行号` 一律指 ctf-agent-ref 仓库内相对路径；我们已有系统引用 `src/` 下的文件（`D:\ctf-agent\src\`）。

---

## 0. 仓库全貌（37 个文件）

| 位置 | 文件 | 职责 |
|---|---|---|
| 入口 | `backend/cli.py` | Click CLI：单题模式 / 全量 coordinator 模式；`ctf-msg` 操作员发消息 |
| 配置 | `backend/config.py` | Pydantic Settings（.env：CTFd 凭据、各 API key、沙箱镜像名、并发上限） |
| 模型 | `backend/models.py` | 模型 spec 解析（`provider/model/effort`）、Provider→Pydantic AI Model 映射、thinking 档位、上下文窗口表 |
| 平台 | `backend/ctfd.py` | 异步 CTFd 客户端（token/密码登录、CSRF、拉题、交 flag、查 solved） |
| 轮询 | `backend/poller.py` | 5 秒轮询 CTFd，diff 出新题/已解题，发事件到队列 |
| 编排器 | `backend/agents/coordinator_loop.py` | 共享事件循环（poller + 操作员 HTTP 端点 + 自动 spawn/kill swarm） |
| 编排器 | `backend/agents/coordinator_core.py` | coordinator 工具的纯逻辑（spawn/read trace/bump/broadcast/submit/kill） |
| 编排器 LLM | `backend/agents/claude_coordinator.py` / `codex_coordinator.py` | 两个 coordinator 大脑后端（Claude SDK / codex app-server），复用同一事件循环 |
| 集群 | `backend/agents/swarm.py` | `ChallengeSwarm`：一题多模型并行竞速 + 提交纪律 + bump 循环 + quota 回退 |
| 解题器 | `backend/agents/solver.py` | Pydantic AI Solver（Bedrock/Azure/Zen/Google 走这里） |
| 解题器 | `backend/agents/claude_solver.py` | Claude Agent SDK Solver（hook 把 Bash 重写到 Docker 容器） |
| 解题器 | `backend/agents/codex_solver.py` | codex `app-server` JSON-RPC Solver（动态工具声明） |
| 沙箱 | `backend/sandbox.py` | aiodocker 容器生命周期（启动/exec/读写文件/清理孤儿容器） |
| 工具 | `backend/tools/core.py` | 平台无关的纯 async 工具逻辑（bash/read/write/web_fetch/webhook/submit/vision） |
| 工具 | `backend/tools/flag.py` / `sandbox.py` / `vision.py` | Pydantic AI 工具薄封装 |
| 横切 | `backend/message_bus.py` | 题内多模型共享 findings 的 append-only 总线 |
| 横切 | `backend/loop_detect.py` | 工具调用签名循环检测 |
| 横切 | `backend/tracing.py` | 每题每模型一个 JSONL 轨迹文件（coordinator 读它给提示） |
| 横切 | `backend/cost_tracker.py` | genai-prices 计费 + 缓存命中率 |
| 横切 | `backend/deps.py` / `solver_base.py` / `output_types.py` | 依赖注入类型、状态常量、结构化输出 schema |
| 离线 | `pull_challenges.py` | 一次性把 CTFd 拉成本地 `metadata.yml + distfiles/`（借用 Eruditus） |
| 镜像 | `sandbox/Dockerfile.sandbox` + `sandbox/sandbox-tools.txt` | 沙箱镜像构建 + 工具速查表 |

---

## 1. 架构拆解（代码级）

### 1.1 进程/数据流模型

```
ctf-solve (cli.py:main)
  └─ run_coordinator (cli.py:147)
       ├─ configure_semaphore + cleanup_orphan_containers (cli.py:160-162)
       ├─ build_deps (coordinator_loop.py:26) → CTFdClient + CostTracker + CoordinatorDeps
       ├─ run_event_loop (coordinator_loop.py:69)   ← 真正的调度核心
       │    ├─ CTFdPoller(interval=5s).start()      (coordinator_loop.py:85-86)
       │    ├─ _start_msg_server(operator_inbox)     (coordinator_loop.py:89)   ← 人工发提示的 HTTP 口
       │    ├─ turn_fn(initial_msg)                  ← coordinator LLM 第一轮
       │    ├─ _auto_spawn_unsolved()                (coordinator_loop.py:110, 226)
       │    └─ while True: poller事件 → 自动kill已解/自动spawn新题/转达coordinator LLM
       └─ 每个 challenge → ChallengeSwarm.run()
            └─ 每个 model_spec → Solver（pydantic-ai | claude-sdk | codex）各一个 DockerSandbox
```

关键设计：**coordinator LLM 与 swarm 是解耦的**。事件循环（`coordinator_loop.py`）不依赖 coordinator 用哪种模型——它把"发生的事"（新题/已解/求解器消息/操作员消息/周期状态）拼成文本，丢给 `turn_fn`；`turn_fn` 只是"把一个字符串发给某个 LLM 并等它用完工具"（`claude_coordinator.py:162-176` / `codex_coordinator.py:343-346`）。coordinator 的工具（`coordinator_core.py` 的 `do_*`）才是真正改状态的入口。

### 1.2 Coordinator：读解题轨迹、给提示

**轨迹先落盘**：每个 solver 一个 JSONL（`tracing.py:18-21` 生成 `logs/trace-<题>-<模型>-<ts>.jsonl`），每次工具调用/结果/模型回复/usage 都 append+flush（`tracing.py:42-58`），供 `tail -f` 实时看。

**coordinator 读轨迹**：`coordinator_core.py:133-171` `do_read_solver_trace` 读 JSONL 最后 `last_n` 行，按事件类型折叠成可读摘要：

```python
# coordinator_core.py:146-167
lines = Path(path).read_text().strip().split("\n")
recent = lines[-last_n:]
...
if t == "tool_call":
    summary.append(f"step {d.get('step','?')} CALL {d.get('tool','?')}: {args_str}")
elif t == "tool_result":
    summary.append(f"step {d.get('step','?')} RESULT {d.get('tool','?')}: {result_str}")
```

**给提示的三条路径**：
1. `do_bump_agent`（`coordinator_core.py:122-130`）→ 直接调 `solver.bump(insights)`，把提示注入**单个模型**的消息历史；
2. `do_broadcast`（`coordinator_core.py:174-180`）→ 走 message bus 广播给**该题所有 solver**；
3. 操作员 HTTP 端点（`coordinator_loop.py:233-280`）→ 人 POST `/msg`，消息进 `operator_inbox`，事件循环里以 `OPERATOR MESSAGE:` 前缀转给 coordinator LLM（`coordinator_loop.py:152-159`），由 LLM 决定怎么 bump。`cli.py:192-215` 的 `ctf-msg` 就是客户端。

**"永不言弃"的编排哲学**写死在 prompt 里（`claude_coordinator.py:44-51`，codex 版同）：

```
- NEVER kill a swarm. Solvers will keep trying indefinitely ...
  The only time a swarm should die is when the flag is confirmed correct.
- When a solver seems stuck, bump it with very specific technical guidance ...
- Cost is not a concern. Keep all swarms running.
```

### 1.3 Message Bus：跨 solver 共享

`backend/message_bus.py` 全文是核心，一个题内多模型共享发现：

```python
# message_bus.py:20-43
@dataclass
class ChallengeMessageBus:
    findings: list[Finding] = field(default_factory=list)
    cursors: dict[str, int] = field(default_factory=dict)   # 每个模型一个已读游标
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def post(self, model, content): ...   # append，超 200 条裁头并平移游标
    async def check(self, model):               # 返回「别人」未读的 findings，游标推进
        unread = [f for f in self.findings[cursor:] if f.model != model]
        self.cursors[model] = len(self.findings)
        return unread
```

- **写入**：`swarm.py:221-227`——一轮结束后，只要 findings 非空且不是 error/空跑，就 `message_bus.post(model_spec, findings[:500])`。
- **读取注入**（三个 solver 后端各实现一次，都是"每 5 步查一次"）：
  - Pydantic AI：`solver.py:87-92` `TracingToolset.call_tool` 里 `step % 5 == 0` 时把 `do_check_findings` 结果拼到工具返回值尾部；
  - Claude SDK：`claude_solver.py:241-250` PostToolUse hook 里同样 `step % 5`；
  - codex：`codex_solver.py:409-413`。
- **注入格式**：`message_bus.py:49-54` `format_unread` 输出 `"**Findings from other agents:**\n\n[model] content"`。

注意一个细节：`check()` 返回的是"非本模型"的未读，即**兄弟之间共享、自己不看自己的回声**（`message_bus.py:41` `if f.model != model`）。这是防"自我强化循环"的关键。

### 1.4 Solver 循环与"永不言弃"

**（a）结构化输出强制不许放弃**：`output_types.py:11-26` 的 JSON schema 只允许一种输出 `flag_found`，注释明说：

```python
# output_types.py:14-15
# Only flag_found is allowed — solvers must keep working until they find a flag.
# No gave_up option forces persistent solving behavior.
```

**（b）swarm 级 bump 循环**（`swarm.py:208-293` `_run_solver_loop`），这是"永不言弃"的实现主体：

```
while not cancel_event.is_set():
    result = await solver.run_until_done_or_gave_up()
    if FLAG_FOUND: cancel_event.set(); return
    if QUOTA_ERROR: 换 API 回退模型（共用同一沙箱）→ continue
    if GAVE_UP/ERROR:
        broken 判定（0 步且 $0）→ break        # swarm.py:259-263
        连续 3 次 ERROR → break                 # swarm.py:266-272
        否则 bump_count += 1
        冷却 min(bump_count*30, 300) 秒        # swarm.py:277-285
        solver.bump(兄弟 insights)             # swarm.py:286-291
        continue
```

`bump()` 把兄弟发现塞进模型消息历史，并要求"换思路、别重复"（`solver.py:267-285`）：

```python
# solver.py:269-278
bump_msg = ModelRequest(parts=[UserPromptPart(content=(
    "Your previous attempt did not find the flag. Here are insights "
    "from other agents working on the same challenge:\n\n" f"{insights}\n\n"
    "Use these insights to try a different approach. Do NOT repeat ..."))])
self._messages.append(bump_msg); self.loop_detector.reset()
```

**（c）循环检测**（`loop_detect.py`）：对工具名+参数序列化签名做滑窗计数，窗口 12，>=3 警告、>=5 强制 break（`loop_detect.py:14-16`）。警告/强制时注入 `LOOP_WARNING_MESSAGE`（`loop_detect.py:52-59`），强制 break 时直接把警告当工具返回值（`solver.py:68-72`）。

**（d）quota 回退**：订阅制模型用完配额，自动降级到 API 计价等价模型并**复用同一沙箱**（`swarm.py:34-39` 映射 + `swarm.py:240-256`，`solver.sandbox=None` 先"摘除"沙箱避免 stop 时销毁）。

**（e）胜者通知 + 取消**：`swarm.py:295-328` `run()` 用 `asyncio.wait(..., FIRST_COMPLETED)`，任一 solver 报 `FLAG_FOUND` 就 `cancel_event.set()` 并取消其余 pending task。

### 1.5 三类 Solver 后端（同一 `SolverProtocol`）

`solver_base.py:29-39` 定义统一协议：`start() / run_until_done_or_gave_up() / bump() / stop()`。三类实现的差异只在"如何驱动 LLM、如何把工具重定向进沙箱"：

| 后端 | 驱动方式 | 沙箱接入手段 |
|---|---|---|
| `solver.py`（Pydantic AI） | `pydantic_ai.Agent` + `FunctionToolset`，工具直接调 `deps.sandbox` | 工具函数本身操作 `DockerSandbox` |
| `claude_solver.py`（Claude SDK） | `ClaudeSDKClient`，`allowed_tools=["Bash","WebFetch","WebSearch"]` | **PreToolUse hook 把每条 Bash 重写为 `docker exec -i <cid> bash -c ...`** |
| `codex_solver.py`（codex） | `codex app-server` JSON-RPC over stdio，`dynamicTools` 声明工具 | 服务端回调用 `_exec_tool` 操作 `DockerSandbox` |

最有移植价值的是 `claude_solver.py` 的重写思路（见 §2.2），因为它展示了"**驱动一个功能完备的 CLI agent，但把它的 shell 拦截后重定向到远端**"——这与我们 `pi + kali.ts` 完全同构：

```python
# claude_solver.py:189-205
escaped = shlex.quote(command)
rewritten = f"docker exec -i {self._container_id} bash -c {escaped}"
return { "hookSpecificOutput": { "hookEventName": "PreToolUse",
    "permissionDecision": "allow", "updatedInput": {**tool_input, "command": rewritten} } }
# 同时把 Read/Write/Edit/Glob/Grep 全部 deny，逼模型只走 bash（claude_solver.py:207-226）
```

`submit_flag`/`notify_coordinator` 也在这里被正则拦截（`claude_solver.py:148-187`），转成 echo 回结果给模型，而不是真在容器里执行。

### 1.6 沙箱：Docker 容器 + 工具清单

**容器参数**（`sandbox.py:109-149`）——这是理解"无 Docker 后我们失去什么"的关键：

```python
# sandbox.py:120-141（节选）
binds = [f"{workspace_dir}:/challenge/workspace:rw"]          # 每 solver 独立临时工作目录
binds += [f"{distfiles}:/challenge/distfiles:ro"]              # 题目附件只读挂载
binds += [f"{meta_yml}:/challenge/metadata.yml:ro"]
"HostConfig": {
    "CapAdd": ["SYS_ADMIN", "SYS_PTRACE"],                     # pwn/gdb 需要
    "SecurityOpt": ["seccomp=unconfined"],
    "Devices": [{"PathOnHost": "/dev/loop-control", ...}],     # 挂载磁盘镜像取证需要
    "Memory": ..., "NanoCpus": int(2 * 1e9),                    # 每容器 2 CPU + 内存上限
}
"ExtraHosts": ["host.docker.internal:host-gateway"],           # 容器内访问宿主机服务
```

**exec 的超时/硬杀**（`sandbox.py:162-208`）：命令包一层 `timeout --signal=KILL --kill-after=5 {timeout_s} bash -c ...`，再用 `asyncio.wait_for(..., timeout_s+30)` 兜底。

**文件读写走 tar**：读 `get_archive` 返回 tar 再解（`sandbox.py:210-234`），写 `put_archive`（`sandbox.py:243-264`）——这是为了**二进制安全**（把容器文件当字节流搬）。

**孤儿清理**（`sandbox.py:49-68`）：启动时按 label `ctf-agent` 删掉上次残留容器。

**工具清单**（`Dockerfile.sandbox` + `sandbox-tools.txt`，分类）：
- 二进制/RE：binutils(objdump/readelf/nm/strings)、file、xxd、binwalk、gdb、ltrace、strace、radare2、pyghidra
- pwn：pwntools、ROPgadget、angr、capstone、unicorn
- crypto：SageMath、RsaCtfTool、cado-nfs、flatter、z3、gmpy2、pycryptodome、sympy、fpylll
- 取证：volatility3、sleuthkit(mmls/fls/icat/fsstat/tsk_recover)、foremost、testdisk、dcfldd、xfsprogs
- stego/媒体：steghide、stegseek、zsteg、exiftool、pngcheck、ImageMagick、ffmpeg、sox、tesseract
- web/网络：curl、wget、nmap、nc、requests、flask
- 其他：PyTorch(CPU)、keras、PyJWT、jq、podman（嵌套容器，`Dockerfile.sandbox:47-57`）

> 镜像在 ARM64 上构建（`sandbox-tools.txt:104` "Sandbox runs ARM64. Do NOT execute x86/x86-64 binaries locally"）——这是 BSidesSF 云环境决定的，与我们无关，但提示了一个重要点：**执行环境架构必须与题目二进制架构匹配**，我们 Kali 是 x86_64，无需处理跨架构。

### 1.7 CTFd 交互（Eruditus、token、轮询、自动发现新题）

**两套认证**（`ctfd.py` + `pull_challenges.py`）：
- **Token 优先**：`Authorization: Token <token>`（`ctfd.py:89-93`），命中则跳过登录。
- **密码回退**：GET `/login` 抓 `nonce`（`ctfd.py:55-61`），POST 登录（`ctfd.py:63-73`，200=失败、302=成功）。
- **CSRF**：token 无则从 `/challenges` 页面抓 `csrfNonce`（`ctfd.py:78-87`），POST 时带 `CSRF-Token` 头，403 时清缓存重试一次（`ctfd.py:113-121`）。

**拉题 → 本地目录**（`ctfd.py:195-271` `pull_challenge` 与 `pull_challenges.py` 同源，README 声明借用 [es3n1n/Eruditus](https://github.com/es3n1n/Eruditus)）：
- slugify 题名 → `<challenges>/<slug>/`，下载 distfiles，HTML 描述转 markdown（`markdownify`），写 `metadata.yml`（含 name/category/value/description/connection_info/tags/solves/hints）。
- **hints 解锁**（`pull_challenges.py:166-220`）：免费 hint 用 `POST /api/v1/unlocks` 解锁再 `GET /api/v1/hints/{id}` 取内容；付费 hint 只拿标题不给内容。

**轮询自动发现新题/已解题**（`poller.py`）——平台交互层最值得复用的部分：
- 每 5 秒 `_poll_once`（`poller.py:85-120`），对 `known_challenges` / `known_solved` 做集合 diff，新题发 `new_challenge`、新解发 `challenge_solved` 事件到队列。
- **两个防抖 sanity check**（`poller.py:91-98`）：题目数骤降过半、已解数归零，都判定为"API 抖动"跳过本次，避免误报。
- 事件消费在 `coordinator_loop.py:114-159`：`new_challenge` → `_auto_spawn_one` 自动开 swarm（`coordinator_loop.py:211-223`）；`challenge_solved` → 自动 kill 对应 swarm（`coordinator_loop.py:122-127`）。

**交 flag**（`ctfd.py:142-161`）：`POST /api/v1/challenges/attempt` `{challenge_id, submission}`，返回 `status` 归一化为 `correct / already_solved / incorrect / unknown` 四种（`ctfd.py:17-21` `SubmitResult`）。

### 1.8 多模型配置

**默认阵容**（`models.py:21-27`）：

```python
DEFAULT_MODELS = ["claude-sdk/claude-opus-4-6/medium", "claude-sdk/claude-opus-4-6/max",
                  "codex/gpt-5.4", "codex/gpt-5.4-mini", "codex/gpt-5.3-codex"]
```

spec 格式 `provider/model_id/effort`，`effort_from_spec` 解析 `low|medium|high|max`（`models.py:142-147`）。

**thinking 档位三处体现**：
1. Claude：`effort` 传给 `ClaudeAgentOptions(effort=effort)`（`claude_solver.py:256-259`）；
2. Google：`resolve_model_settings` 里 `google_thinking_config={"thinking_level":"high","include_thoughts":True}`（`models.py:120-126`）；
3. codex：`REASONING_EFFORT = {"gpt-5.3-codex": "xhigh"}`（`codex_solver.py:48-50`）→ `thread_params["reasoningEffort"]`（`codex_solver.py:222-224`）。

**选择逻辑**：`swarm._create_solver` 按 `provider_from_spec` 分流（`swarm.py:70-114`）：`claude-sdk/*`→ClaudeSolver、`codex/*`→CodexSolver、`bedrock|azure|zen|google/*`→Pydantic AI Solver。所有模型**对同一道题同时跑**，先出 flag 者胜。

**上下文窗口**（`models.py:30-38`）与**vision 能力表**（`models.py:41-47`）：`supports_vision` 决定是否挂 `view_image` 工具（`solver.py:101-102`）。小上下文模型（spark 128k）触发 70% 自动压缩（`codex_solver.py:347-358`）。

### 1.9 flag 抽取与提交、错误提交处理

**抽取**：不靠正则猜，靠模型**主动调 `submit_flag` 工具验证**（`prompts.py:176-177` "Verify every candidate with submit_flag ... Once CORRECT: output `FLAG: <value>`"）。Claude 后端甚至从 bash 命令里正则拦 `submit_flag '<flag>'`（`claude_solver.py:148-174`）。

**提交纪律**（`swarm.py:153-192` `try_submit_flag`）——本仓库最精良的一段工程，值得逐行移植：

```python
# swarm.py:153
SUBMISSION_COOLDOWNS = [0, 30, 120, 300, 600]   # 错误后逐级递增冷却（0s/30s/2min/5min/10min）
# swarm.py:157-192（节选）
async with self._flag_lock:                       # 全题一把锁，串行化提交
    if self.confirmed_flag: return "ALREADY SOLVED ...", True
    normalized = flag.strip()
    if normalized in self._submitted_flags:       # 全模型去重：同一 flag 只交一次
        return "INCORRECT — already tried this exact flag.", False
    wrong_count = self._submit_count.get(model_spec, 0)
    cooldown = self.SUBMISSION_COOLDOWNS[min(wrong_count, len(...)-1)]
    if elapsed < cooldown: return "COOLDOWN ... do deeper analysis", False
    self._submitted_flags.add(normalized)
    display, is_confirmed = await do_submit_flag(self.ctfd, self.meta.name, flag)
    if is_confirmed: self.confirmed_flag = normalized
    else: self._submit_count[model_spec] = wrong_count + 1; self._last_submit_time[model_spec] = now
```

确认标记 `CORRECT_MARKERS = ("CORRECT", "ALREADY SOLVED")`（`solver_base.py:16`），`already_solved` 也算"已确认"（避免同队重复提交）。

---

## 2. 适配分析（逐条：拿什么 / 改什么 / 怎么改 / 为什么）

> 我们的底子（已确认存在）：`src/ctf_orchestrator/ctf_orchestrator.py`（黑板 state.json + hints 注入 + 多题并行 + 同题多 worker 竞速）、`src/dasctf_client/dasctf_client.py`（API 客户端骨架 + EP 端点集中 + flag 正则 + 提交预算）、`src/pi-ext/kali.ts`（pi 工具转发到 Kali `/api/command`）、`src/kali-setup/bootstrap-kali.sh`、`src/mock_platform/mock_platform.py`（本地演练平台）。

### 2.1 可直接移植到我们编排器的机制

**① Message Bus（`message_bus.py` 全文）→ 最高优先级移植**

拿什么：`ChallengeMessageBus` 的 append-only findings + per-model 游标 + `check()` 只看别人（`message_bus.py:20-54`）。

改什么：我们是**多进程**（`subprocess.Popen` 起 pi worker，`orchestrator.py:235-243`），不是 asyncio 单进程。asyncio.Lock + 内存 list 跨进程失效。

怎么改：把 findings 落进黑板 `state.json`（或独立 `workspace/findings/<cid>.jsonl`），每 worker 一轮结束写"发现摘要"，每 worker 起跑/中途读"别人的发现"注入 prompt。游标语义照搬：`findings[自己的游标:]` 里过滤掉 `model == 自己`（`message_bus.py:41` 的防回声是精髓，必须保留）。

为什么：我们目前只有"hints 人工注入"（`orchestrator.py:162-166`）和"同题竞速"（各自独立跑），**缺少同题多 worker 之间实时共享发现**——这正是 ctf-agent 52/52 的核心杠杆之一（`README.md:120` "Cross-solver insights — findings shared between models via message bus"）。

**② 提交纪律（`swarm.py:153-192`）→ 移植到 `Orchestrator.submit_flag`**

拿什么：全题去重 `_submitted_flags` + 逐级递增冷却 `SUBMISSION_COOLDOWNS` + per-model 错误计数。

改什么：我们 `submit_flag`（`orchestrator.py:177-195`）只有 `max_wrong_submits=3` 硬上限 + `min_submit_interval=5s`（`dasctf_client.py:88-91`）。缺"全 worker 共享去重"和"错误越多冷却越久"。

怎么改：在 `Orchestrator` 加 `self._submitted_flags: set` 和 `self._submit_times: dict`，`submit_flag` 加锁段内先查去重再查冷却。冷却梯度可按 3 小时压缩为 `[0, 20, 60, 180]`（比原版 600s 短，见 §2.3）。

为什么：DASCTF 有封号/限频风险，且多个 worker 会对同一题各交一次重复 flag 浪费提交额度；去重 + 冷却能显著降低误提交与平台风控概率。`dasctf_client.py:87` 注释"防封号"已经是同一意图，但粒度不够。

**③ Loop 检测（`loop_detect.py`）→ 移植到 pi 侧**

拿什么：工具签名滑窗计数，warn=3/break=5（`loop_detect.py:14-16`）+ `LOOP_WARNING_MESSAGE`。

改什么：我们没有 agent 内工具 hook（pi 是黑盒 CLI）。但可以在 **kali.ts 的 `bashOps.exec` 层**对命令字符串做哈希计数，同一命令重复 N 次时，在 stdout 尾部追加循环警告文本（等价 `solver.py:79-81` 的 warn 注入）。

为什么：pi worker 在限时赛里最常见的浪费就是"反复跑同一个失败命令"，这是纯本地、零模型成本的止损手段。

**④ "只允许 flag_found"的结构化约束（`output_types.py:11-26`）**

拿什么：输出 schema 只留 `{type:"flag_found", flag, method}`，没有 gave_up。

改什么：我们 `SOLVE_PROMPT_TEMPLATE`（`orchestrator.py:68-82`）是自由文本，靠 `extract_flags` 正则兜底（`dasctf_client.py:206-219`）。pi 是否支持结构化输出待确认。

怎么改：至少做 prompt 层约束——把 `prompts.py:176-177` 的 "Verify every candidate ... Once CORRECT output `FLAG: <flag>` on its own line" 直接并入我们的模板；`extract_flags` 保留作为兜底，但**只信真实执行输出**（`dasctf_client.py:204` 注释已写 EnIGMA 教训，方向一致）。

为什么：限时赛里"模型宣布放弃/输出一堆候选"会拖慢抢分；强制单一 `flag_found` 输出 + 先交验证，能压掉无效回合。

**⑤ JSONL 轨迹（`tracing.py`）→ 供 coordinator/人工读**

拿什么：每题每模型一个 append-only JSONL，事件类型 `tool_call/tool_result/model_response/usage/finish`（`tracing.py:42-58`）。

改什么：我们现在只存 worker 日志 tail 4000 字（`orchestrator.py:261`），且 pi 是黑盒，拿不到内部工具事件。

怎么改：保留 orchestrator 层的 worker 日志，但加一层"回合级"事件记录（worker 起/停/flag 候选/提交结果）到 `workspace/traces/<cid>.jsonl`，让 `coordinator_core.py:133-171` 的"读轨迹给提示"逻辑可以直接落地（读最后 N 条拼摘要）。pi 内部轨迹若 pi 有 trace 输出可顺带解析。

为什么：我们已有 `hints/<cid>.md` 人工注入，但**没有"读轨迹→自动生成提示"的闭环**；这是 coordinator LLM 存在的全部意义（`claude_coordinator.py:34-54`）。

**⑥ broken-solver 检测（`swarm.py:259-263`）**

拿什么：`step_count==0 and cost==0` 判 broken，不 bump 直接放弃该 worker。

改什么：我们 `run_one` 没有识别"空跑 worker"（进程起了但模型没干活）。

怎么改：worker 结束判定里，若日志几乎为空/无任何工具执行痕迹，标记该 worker 配置为 broken，下一轮同题不再用它，换档位。

为什么：DeepSeek/Qwen 偶发"空输出/直接结束"，限时赛里空跑一次就是浪费 WORKER_TIMEOUT 内的等待。

**⑦ poller 防抖（`poller.py:91-98`）→ 移植到 `Orchestrator.sync`**

拿什么：题目数骤降过半、已解数归零 → 判定 API 抖动跳过。

改什么：我们 `sync()`（`orchestrator.py:150-160`）直接信任 `client.challenges()`，平台抖一次会误判"新题/丢题"。

怎么改：`sync` 里记录上次题目数，若本次 < 上次一半则跳过并告警；`submit` 结果异常（非 bool/缺字段）不当作"错误提交"计数。

为什么：测试赛才见真 API，稳定性未知；这是零成本鲁棒性。

**⑧ 工具纯函数分离（`backend/tools/core.py`）**

拿什么：`do_*` 纯 async 函数与 Pydantic AI 薄封装（`tools/sandbox.py`）分层。

改什么：我们 pi 的工具在 `kali.ts` 里直接实现，逻辑和 transport 混在一起。

怎么改：抽一个 `kali_ops` 层（`exec/read_file/write_file/list_files` 纯函数，返回 `{stdout,stderr,code}`），`kali.ts` 只做 pi 接口适配。这层同时被 orchestrator 的 `kali_upload/kali_exec` 复用，避免 `orchestrator.py:85-101` 与 `kali.ts` 两套重复实现。

为什么：决赛要代码审查，transport/逻辑分层是可读性的硬要求；也便于以后换 SSH pty 通道（`kali.ts:12-13` 已注明限制）。

**⑨ 首动作强制连接服务（`prompts.py:77-83`）**

拿什么：`connection_info` 非空时在 prompt 顶部写 "FIRST ACTION REQUIRED: connect to the service"，并区分 web(`https://`)/TCP(`nc `) 给不同示例（`prompts.py:96-110`）。

改什么：我们模板（`orchestrator.py:68-82`）只笼统说"连网络服务"，没有按连接类型给 heredoc/pwntools 示例。

怎么改：把 `prompts.py:99-107` 那段 nc-heredoc 提示并入模板。

为什么：pwn/web 题模型常先去翻沙箱文件系统而不连服务，白浪费回合；这是零成本提升首步命中率。

**⑩ 计费（`cost_tracker.py`）→ 用自建价目替代**

拿什么：按模型累计 input/cached/output token 与金额、缓存命中率（`cost_tracker.py:125-232`）。

改什么：`genai-prices` 不覆盖 DeepSeek/Qwen 国内价，`PROVIDER_MAP`（`cost_tracker.py:15-22`）不适用。我们预算有限，**必须**计费。

怎么改：建一个 `pricing.json`（deepseek-v4-pro/flash、qwen-max/plus 的 input/output 单价），在 pi 输出或 orchestrator 层读 token 用量累计；把 `_fmt_tokens/_cache_rate` 直接搬。pi 是否吐出 usage 需确认，否则退化为"按 worker 时长 × 模型单价估算"。

为什么：3 小时 + 预算有限，没有实时成本就无法做"时间预算内的竞速"（§2.3）的降档决策。

### 2.2 Docker 沙箱 → Kali REST 命令通道（最大差异点）

**失效的设计**（逐条对应 `sandbox.py`）：

| ctf-agent 依赖 | 失效原因 | 我们的等价替代 |
|---|---|---|
| 每 solver 一个容器 = 天然隔离（`sandbox.py:79` "a single solver agent"） | 无容器，所有 worker 共享一台 Kali 的文件系统/进程/端口空间 | **目录命名空间隔离**（见下 ①） |
| `binds` 只读挂载 distfiles / 可写挂载 workspace（`sandbox.py:120-124`） | 无挂载语义 | 每 worker 一个远程目录，附件用 `base64 -d` 落盘（`orchestrator.py:91-101` `kali_upload` 已实现） |
| `CapAdd SYS_ADMIN/SYS_PTRACE` + `seccomp=unconfined`（`sandbox.py:135-136`） | Kali 原生 root，无此限制 | 无需处理（反而更省） |
| `Memory`/`NanoCpus` 资源上限（`sandbox.py:138-139`） | 无 cgroup 限制 | Kali API 服务端用 `ulimit`/`timeout` 近似（见下 ③） |
| `ExtraHosts host.docker.internal` 桥接（`sandbox.py:134`、`prompts.py:49-55` 把 localhost 改写） | 无桥接网络 | **不要改写** localhost：题目服务地址用真实 host:port（见下 ④） |
| exec 包 `timeout --signal=KILL --kill-after=5`（`sandbox.py:165`） | Kali API 目前只是一次性 REST（`kali.ts:12-13`） | Kali 服务端命令处理层加同样的 timeout + 进程组杀（见下 ③） |
| `read_file/write_file` 走 tar `get_archive/put_archive`（`sandbox.py:210-264`） | 无 Docker API | `kali.ts` 已用 `cat`/`echo b64 \| base64 -d` 等价（`kali.ts:70,85-88`）；**二进制读需补 base64 回传**（见下 ②） |
| 流式 exec 输出采集（`sandbox.py:177-199`） | Kali REST 一次性返回，无流式 | 接受一次性返回；长任务由超时截断（`kali.ts:29` 已设 5min） |
| `cleanup_orphan_containers`（`sandbox.py:49-68`） | 无容器可删 | Kali 侧残留进程/目录清理（见下 ⑤） |
| 嵌套 podman 跑题镜像（`Dockerfile.sandbox:47-57`） | Kali 无 Docker（约束明说） | 放弃，见 §2.5 |

**① 无容器隔离时的并发安全 + 文件隔离（核心设计）**

问题：`orchestrator.py:220-222` 现在所有同题 worker 共用 `remote_root=/root/ctf/{cid}`，多 worker 竞速会互相覆盖 `exploit.py`、`output`、`flag.txt`，且 `kali.ts:65-66` 的 `remoteCwd` 是**每个 pi 进程一个**、但都映射到同一远程根。

改法（命名空间三级）：
```
/root/ctf/<cid>/<worker_idx>/   # 每 worker 独立目录
  ├─ attachments/               # 只读约定（脚本里不写这里）
  └─ work/                      # 可写
```
- orchestrator 起 worker 时传 `--kali /root/ctf/<cid>/w<idx>`（`orchestrator.py:240-241` 已传 `--kali remote_root`，改成带 idx）。
- 附件每 worker 一份 or 软链共享只读目录；建议**共享只读 + 独立可写**：`/root/ctf/<cid>/shared/attachments`（一次上传）+ `/root/ctf/<cid>/w<idx>/`（可写）。省去 N 份附件重复上传（`kali_upload` 有 base64 开销）。

为什么：ctf-agent 靠"容器 = 边界"隐式解决；我们没有边界，只能靠**约定 + 目录前缀**显式解决。命名空间不隔离的话，同题竞速会互相踩踏，竞速反而变负资产。

**② 二进制文件安全的等价物（tar → base64）**

`kali.ts` 的 `readFile` 用 `cat` 直接返回 stdout（`kali.ts:70`），遇二进制（pwn 附件、加密后文件）会因编码损坏。改法：`readOps.readFile` 对疑似二进制先用 `file --mime-type` 判定，二进制走 `base64 -w0 <file>` 回传后在 TS 侧 `Buffer.from(b64,'base64')` 还原（`detectImageMimeType` 已示范了 `file --mime-type`，`kali.ts:74-81`）。

为什么：ctf-agent 用 tar 正是为了二进制安全（`sandbox.py:231-233` 有 UnicodeDecodeError 分支）；我们失去 tar 通道，base64 是最小等价物。

**③ 进程清理 + 超时（等价 `sandbox.py:165`）**

Kali 端目前没有服务代码（只有 `bootstrap-kali.sh` 装工具）。需要在 Kali 上部署一个**命令执行 handler**（现假设的 `/api/command` 服务），并让它对每条命令：
```bash
# 等价 sandbox.py:165
setsid timeout --signal=KILL --kill-after=5 ${timeout_s} bash -c "$cmd" &
wait $!   # 超时后 timeout 发 KILL，setsid 让整组子进程（pwn 题 spawn 的）一起死
```
加 per-worker 的"上一次命令残留进程"记录，下次执行前 `pkill -g <pgid>` 或按 cwd 前缀 `pkill -f "/root/ctf/<cid>/w<idx>"` 清理。

为什么：pwn 题常留下挂着 nc 的孤儿进程；无容器 `delete(force=True)`（`sandbox.py:275`）兜底，只能显式杀进程组。这是从"容器生命周期"迁移到"进程组生命周期"的关键。

**④ localhost 改写策略反转**

ctf-agent 把 `connection_info` 里的 localhost 改成 `host.docker.internal`（`prompts.py:49-55`）是因为容器桥接网络。我们 Kali 上跑的命令**就在 Kali 本机**，若题目服务在 Kali 本机（演练 mock_platform 的 8300，`mock_platform.py:45`），应保持 localhost；若在远程，直接用题目给的 host:port。**不要照搬改写逻辑**——这是最容易踩的坑。

**⑤ 状态残留清理（等价容器快照回滚）**

ctf-agent 每次 `start()` 新建 `tempfile.mkdtemp`（`sandbox.py:114`），天然干净。我们 Kali 目录是持久的，`run_one` 起跑前要 `kali_exec("rm -rf /root/ctf/<cid>/w<idx>/* && mkdir -p ...")`，或 worker 结束统一清。

### 2.3 3 小时限时：把"无限 swarm 竞速"改成"时间预算内的竞速"

ctf-agent 的前提是 **"Cost is not a concern / NEVER kill a swarm"**（`claude_coordinator.py:44-51`）——它对 BSidesSF 是 48 小时制 + 富预算，对我们 3 小时 + 预算有限**是直接冲突**。改造点：

**① 全局倒计时 + 分级预算**（替换"无限 bump"）

- 编排器启动即读入 3 小时 deadline，每道题按 `value/solves` 排序（ctf-agent coordinator 策略 "prioritize by solve count" 即先易后难，`claude_coordinator.py:39`），**先扫 easy**。
- 给每题分配"时间片"而非"无限 bump"：easy 题 10–15min 硬上限、hard 题 20–25min 上限；到点强制 kill worker、标记 dead。我们 `WORKER_TIMEOUT=1500`(25min)（`orchestrator.py:47`）要按题分档而非一刀切。

**② 先易后难的"降档竞速"**（替换"5 模型齐上"）

ctf-agent 每题 5 个高价模型齐上；我们预算有限，用 **DeepSeek/Qwen 的 pro/flash × thinking 档位组合做伪多模型**（我们 `DEFAULT_MODEL_CONFIG`，`orchestrator.py:54-66`，已是这个方向）。改造：easy 题用 `flash+low` 1 个 worker 抢分，hard 题才 `pro+high` + `flash+low` 2 worker 竞速（`drill-config.json` 已接近此态）。**省下的钱花在 hard 题的 pro 档**。

**③ bump 冷却压缩**

ctf-agent 的 bump 冷却 `min(bump_count*30, 300)`（`swarm.py:279-282`）在 3 小时里太慢。压缩为 `min(bump_count*10, 60)`，且 bump 次数上限按时间片封顶（如 easy 题最多 2 次 bump）。

**④ 提交纪律冷却压缩**

`SUBMISSION_COOLDOWNS = [0,30,120,300,600]`（`swarm.py:153`）压缩为 `[0,15,60,180]`——3 小时抢分容不得 10 分钟提交冷却，但保留递增趋势防封号。

**⑤ 人机交互（人写提示纠偏）**

ctf-agent 的操作员 HTTP 端点（`coordinator_loop.py:233-280` + `cli.py:192-215`）与我们的 `hints/<cid>.md` 人工注入（`orchestrator.py:162-166`）本质同构。3 小时赛里"人看板发现某 worker 卡死 → 写 hint → 下一轮自动注入"是**强制要求**的纠偏手段，保留并强化：hint 文件支持"仅注入特定 worker 档位"标记，而非全题广播。

### 2.4 平台从 CTFd → 未知 API 的抽象设计

**ctf-agent 的问题**：`CTFdClient` 是具体类直接注入 `SolverDeps/CoordinatorDeps`（`deps.py:21-42`），`poller` 直接依赖 `CTFdClient`（`poller.py:7,23`）。换平台 = 重写 client + poller + deps。

**我们的机会**：`dasctf_client.py` 已把端点集中到 `EP` dataclass（`dasctf_client.py:41-51`）且 `challenges()` 兼容多种返回形态（`dasctf_client.py:156-164`），这是好起点。建议补一个**平台接口 + 归一化层**：

```python
# 仿 SolverDeps 的依赖注入（deps.py:21-35）
class Platform(Protocol):
    def challenges(self) -> list[dict]: ...        # 统一返回 {id, name, category, value, solves, status}
    def detail(self, cid) -> dict: ...
    def submit(self, cid, flag) -> SubmitResult: ...  # 统一 correct/incorrect/rate_limited
    def solved_ids(self) -> set[str]: ...          # 已解集合（poller diff 用）
    def attachment(self, cid, dest) -> Path | None: ...
```

- `SubmitResult` 归一化照搬 `ctfd.py:17-21`（`correct/already_solved/incorrect/unknown`），`submit()` 内部把 DASCTF 的真实响应（`mock_platform.py:105` 是 `{"correct":bool}`，真实平台未知）归一化成一个枚举。我们 `dasctf_client.submit`（`dasctf_client.py:186-197`）已做了部分（`data.get("correct")`）。
- **poller 抽象**：把 `poller.py` 的 diff + 防抖逻辑（`poller.py:85-120`）原样保留，把 `self.ctfd.fetch_challenge_stubs / fetch_solved_names` 换成接口方法。这套"new/solved 集合 diff + sanity check"平台无关，直接可用。
- **自动发现新题**：`coordinator_loop.py` 的 `_auto_spawn_one/_auto_spawn_unsolved`（`coordinator_loop.py:211-230`）平台无关，保留。
- **hints 解锁**（`pull_challenges.py:166-220`）逻辑保留为可选：若真实平台有 hint API 且 cost<=0，才自动解锁，否则靠人工 hints。

为什么这样设计：真实端点测试赛才知道，所以**把"未知"收敛进一个 `EP` + 一个 `Platform` 适配器**，其余（编排、竞速、提交纪律、防抖、轨迹）全部平台无关。这正好是 `dasctf_client.py:9-10` 注释写的目标，只是再往前推一层：把"解析差异"与"归一化语义"分离。

### 2.5 放弃清单（及理由）

| 放弃项 | 理由 |
|---|---|
| **Claude Agent SDK 后端**（`claude_solver.py` 全部） | 我们不用 Claude；但其"hook 重写 bash 到远端"的思想已由 `kali.ts` 的 `BashOperations.exec` 等价实现，思想保留、代码放弃 |
| **codex `app-server` JSON-RPC 直接驱动**（`codex_solver.py` 全部） | 我们不用 codex；但 `dynamicTools` 声明 + `outputSchema` 结构化输出思想可借鉴到 pi 的工具声明 |
| **Bedrock/Azure/Zen/Google provider**（`models.py:50-98` 的 `resolve_model`） | 我们只有 DeepSeek（OpenAI 兼容）+ 可能 Qwen；只保留 openai 兼容分支，删掉 boto3/bedrock/google 依赖（`pyproject.toml:7,10` 的 `pydantic-ai[bedrock,google]`、`boto3` 都不需要） |
| **Docker 嵌套 podman**（`Dockerfile.sandbox:47-57`） | Kali 无 Docker（硬约束），web 题"跑题镜像"能力放弃；本地 web 题改用 Kali 原生起服务 |
| **genai-prices 计费**（`cost_tracker.py:9,15-22`） | 不覆盖 DeepSeek/Qwen；换自建 `pricing.json`（§2.1⑩） |
| **webhook.site 外带通道**（`tools/core.py:128-152`） | 依赖境外服务；西湖论剑 web 题是否需要外带待定，需要时换 dnslog/自建回调，否则放弃 |
| **Eruditus 的 HTML 登录抓取**（`pull_challenges.py:46-72` 的 nonce 抓取） | 平台 API-only（约束明说），无 HTML 登录页；只保留其 hint 解锁与 md 转换思路 |
| **ARM64 镜像 / 跨架构提示**（`Dockerfile.sandbox` 构建、`sandbox-tools.txt:104`） | 我们 Kali 是 x86_64，题目二进制同架构，无需处理 |
| **"Cost is not a concern / NEVER kill swarm"**（`claude_coordinator.py:44-51`、`codex_coordinator.py:41-47`） | 与 3 小时 + 预算有限直接冲突，改为 §2.3 的时间预算制 |
| **"5 个高价模型同题齐上"**（`models.py:21-27` 默认阵容） | 预算有限，改为 DeepSeek/Qwen pro/flash × thinking 档位伪多模型 |

---

## 3. 落地优先级建议（一句话）

1. **P0**：`Platform` 抽象 + `SubmitResult` 归一化（§2.4）→ `message_bus` 落盘版（§2.1①）→ 提交纪律去重+冷却（§2.1②）。
2. **P1**：Kali 目录命名空间 + 二进制 base64 + 进程组超时杀（§2.2①②③）→ 时间预算 + 降档竞速（§2.3）。
3. **P2**：loop 检测 + 首动作连接服务 + broken 检测 + 计费（§2.1③⑨⑥⑩）。
4. **决赛代码审查约束**：上述所有改动落在 transport/逻辑分离 + `EP` 集中 + 归一化层，天然满足"可审查"；避免在 orchestrator 里散落平台特殊分支。

---

## 附：关键文件:行号索引（速查）

| 机制 | 位置 |
|---|---|
| 提交纪律（去重+冷却） | `backend/agents/swarm.py:153-192` |
| bump 循环（永不言弃） | `backend/agents/swarm.py:208-293` |
| quota 回退复用沙箱 | `backend/agents/swarm.py:240-256` |
| 兄弟发现共享 | `backend/message_bus.py:20-54`、`backend/agents/swarm.py:221-227`、`backend/agents/solver.py:87-92` |
| 读轨迹给提示 | `backend/agents/coordinator_core.py:133-171` |
| 自动发现新题/自动 kill | `backend/agents/coordinator_loop.py:211-230`、`backend/poller.py:85-120` |
| 提交防抖 sanity check | `backend/poller.py:91-98` |
| 结构化输出不许放弃 | `backend/output_types.py:11-26` |
| 循环检测 | `backend/loop_detect.py:14-16,52-59` |
| Claude hook 重写 bash 进容器 | `backend/agents/claude_solver.py:189-226` |
| 容器隔离参数 | `backend/sandbox.py:120-141` |
| exec 超时硬杀 | `backend/sandbox.py:162-208` |
| 二进制安全 tar 读写 | `backend/sandbox.py:210-264` |
| CTFd token/CSRF/交卷 | `backend/ctfd.py:89-161` |
| 多模型/thinking 档位 | `backend/models.py:21-27,142-147`、`backend/agents/codex_solver.py:48-50` |
| 首动作连接服务 | `backend/prompts.py:77-110` |
