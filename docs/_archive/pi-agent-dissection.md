# pi-agent 深度拆解报告

> 目标仓库：`Ashutosh0428/pi-agent`（PyPI 包名 `pi-coding-agent`，版本 0.6.0，见 `src/pi_agent/__init__.py:3`）
> 本地克隆：`D:\pi-agent`
> 拆解目的：为"构建自动打 CTF（DASCTF / Jeopardy：web/pwn/re/crypto/misc）的 agent"做学习性参考。

---

## 1. 架构总览

**一句话定位**：pi-agent 是一个约 300 行核心、provider 无关、UI 无关的 ReAct 工具调用循环 agent，用"中立 transcript + 每 provider 翻译层"这一条接缝同时支持 Claude/GPT/Groq/OpenRouter/Gemini/EURI/GLM/Ollama，并用一个 `Sandbox` 路径边界类 + 确定性正则 guardrails 兜住安全。

**模块职责**（全部在 `src/pi_agent/` 下）：

| 文件 | 职责 |
|---|---|
| `config.py` | `AgentConfig` 数据类 + 系统提示词（`SYSTEM_PROMPT`） |
| `sandbox.py` | 路径安全边界（所有文件操作的唯一 choke-point） |
| `llm.py` | provider 注册表 + 中立 transcript ↔ 各家 wire format 的翻译；用量/成本估算 |
| `agent.py` | 工具调用循环（ReAct、重试、delegate、自审），provider/UI 无关 |
| `skills.py` | 加载 `SKILL.md` 并内联进 system prompt，含相关性路由 |
| `guardrails.py` | 确定性安全护栏（secret 外泄拦截、破坏性命令确认、输出脱敏/高亮） |
| `kb.py` | 本地 BM25 知识库（stdlib sqlite3） |
| `mcp_client.py` | 纯 stdlib 的 MCP stdio 客户端 |
| `upload.py` | zip-slip 安全的项目解压 |
| `repl.py` / `cli.py` | 终端前端（rich）与 `pi` 命令入口 |
| `tools/*.py` | 工具定义与实现（见目录树） |
| `streamlit_app.py` | 公开 Web demo（无 shell、临时沙箱） |

---

## 2. 目录树

```
D:\pi-agent\
├── pyproject.toml            # 包元数据、依赖、[project.scripts] pi = pi_agent.cli:main
├── requirements.txt          # 仅供 Streamlit demo 使用
├── README.md  CHANGELOG.md  ROADMAP.md  Dockerfile  .env.example
├── docs\                     # USAGE.md / RELEASING.md / superpowers\specs\（设计文档）
├── skills\                   # 18 个 SKILL.md（planning/orchestrate/write-tests/code-review/
│   ├── <skill-name>\SKILL.md #   security-review/debug/refactor/... 每技能一个文件夹）
│   └── ...
├── src\pi_agent\
│   ├── __init__.py           # __version__ = "0.6.0"
│   ├── agent.py              # ★ 核心 loop（289 行）
│   ├── config.py             # AgentConfig + SYSTEM_PROMPT
│   ├── sandbox.py            # ★ Sandbox（路径边界，37 行）
│   ├── llm.py                # ★ provider 抽象（641 行）
│   ├── skills.py             # SKILL.md 加载/路由
│   ├── guardrails.py         # 确定性安全护栏
│   ├── kb.py                 # 本地 BM25 知识库
│   ├── mcp_client.py         # MCP stdio 客户端
│   ├── upload.py             # zip 安全解压
│   ├── repl.py  cli.py       # 前端与入口
│   └── tools\
│       ├── base.py           # Tool 数据类
│       ├── registry.py       # ToolRegistry + build_default_tools
│       ├── planning.py       # update_plan（实时 todo）
│       ├── filesystem.py     # read_file/write_file/edit_file/list_dir
│       ├── search.py         # grep
│       ├── patch.py          # apply_patch（原子多文件编辑）
│       ├── memory.py         # remember（持久记忆 .pi/memory.md）
│       ├── shell.py          # run_bash（本地全量 shell）
│       ├── safe_exec.py      # run_command（公开安全只读命令）
│       ├── _subprocess.py    # 共享的受限子进程执行器
│       ├── vcs.py            # git（只读）
│       ├── web.py            # web_fetch（SSRF 防护）
│       ├── subagent.py       # delegate（子代理）
│       └── datasci.py        # analyze_data / make_slides
├── streamlit_app.py          # 公开 Web demo
└── tests\                    # 174 个测试（含假 provider/MCP server）
```

---

## 3. 核心机制逐项拆解

### 3.1 核心 agent loop（`src/pi_agent/agent.py`）

README 声称"核心 loop 约 150 行"，实际实现是 `Agent._loop`（`agent.py:251-289`），加上 `_ask`、`_dispatch`、`_with_retry`、`_history_for_request` 等辅助方法构成完整机制。循环结构如下：

```python
# src/pi_agent/agent.py:251-289
def _loop(self, user_input: str) -> str:
    self.messages.append({"role": "user", "content": user_input})
    tools = self.registry.schemas()
    for _ in range(self.config.max_iterations):
        response, streamed = self._ask(tools)
        self.total_usage += response.usage
        self._emit("usage", {"turn": response.usage, "total": self.total_usage})
        self.messages.append({
            "role": "assistant",
            "content": response.text,
            "tool_calls": response.tool_calls,
        })
        if response.text and not streamed:
            self._emit("assistant_text", response.text)
        if not response.tool_calls:      # ← 退出条件：模型不再请求工具
            return response.text
        results: list[ToolResult] = []
        for call in response.tool_calls:
            self._emit("tool_call", call)
            if call.name == "update_plan":
                self._emit("plan", call.args.get("steps", []))
            output = self._dispatch(call)   # ← 结果回填前经统一 guardrail
            self._emit("tool_result", {"call": call, "output": output})
            results.append(ToolResult(id=call.id, name=call.name, output=output))
        self.messages.append({"role": "tool", "results": results})
    self._emit("info", "Reached max iterations.")
    return "Stopped: reached the maximum number of tool iterations."
```

要点：

- **提示构造**：系统提示词来自 `config.py:16-27` 的 `SYSTEM_PROMPT`，工具 schema 来自 `self.registry.schemas()`（`agent.py:254`），skills 在 CLI/Web 层拼进 system prompt（见 3.5）。
- **单次模型调用 `_ask`**（`agent.py:144-166`）：优先走 `provider.stream(...)`（若支持），否则 `_with_retry(lambda: provider.complete(...))`。流式不包重试，避免重复输出已显示的 delta。
- **结果回填**：每次 assistant 消息后跟一条 `{"role":"tool","results":[...]}`（`agent.py:286`），这是"中立 transcript"的关键结构。
- **退出条件**：`if not response.tool_calls: return response.text`（`agent.py:273-274`）+ 达到 `max_iterations` 返回 "Stopped:" 前缀（`agent.py:288-289`）。
- **瞬时错误重试 `_with_retry`**（`agent.py:104-121`）：`_PERMANENT_CODES = {400,401,403,404,422}`（`agent.py:47`），只对 408/409/429/5xx 或名称含 `ratelimit/timeout/...` 的异常重试，指数退避 + full jitter：`time.sleep(random.uniform(0, min(2 ** (attempt - 1), 16)))`（`agent.py:121`）。
- **历史裁剪 `_history_for_request`**（`agent.py:123-142`）：`max_history_messages`（默认 80，`config.py:49`）限制发送给模型的消息数，并把起点对齐到 `user` 边界，保证不会发出"没有对应 tool 结果的 assistant tool_call"（provider 会拒绝）。完整 transcript 仍留在 `self.messages`。

### 3.2 工具系统与"沙箱"（`tools/base.py`、`tools/registry.py`、`sandbox.py`、`guardrails.py`）

**工具定义**是一个纯数据 dataclass（`tools/base.py:21-27`）：

```python
@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler      # Callable[[dict[str,Any], Sandbox], str]
    mutating: bool = False    # True -> 可能需确认
```

`handler` 统一签名是 `(args, Sandbox) -> str`（`tools/base.py:18`），输出永远是字符串，错误也被转成字符串回给模型（`registry.py:52-64`），模型能读错误并自我纠正而不是崩溃。

**注册与装配**在 `tools/registry.py:67-105` 的 `build_default_tools`，按开关增量组合：

```python
# src/pi_agent/tools/registry.py:90-104
tools = [*planning_tools(), *filesystem_tools(), *patch_tools(), *search_tools()]
if enable_shell:        tools += shell_tools()      # run_bash（本地）
if enable_safe_command: tools += safe_command_tools()  # run_command（公开安全）
if enable_subagents:    tools += subagent_tools()   # delegate
if enable_data:         tools += data_tools()       # analyze_data/make_slides
if enable_vcs:          tools += git_tools()        # git（只读）
if enable_web:          tools += web_tools()        # web_fetch（SSRF 防护）
if enable_memory:       tools += memory_tools()     # remember
```

CLI 用 `enable_shell=True, enable_vcs=True, enable_web=True, enable_memory=True`（`cli.py:213-215`）；公开 Web demo 用 `enable_shell=False, enable_safe_command=True, enable_subagents=True, enable_data=True`（`streamlit_app.py:359-364`）。同一个 registry 被两种前端复用，安全边界靠"开关 + 同一 choke-point"保证一致。

**"沙箱"具体实现是路径边界，不是容器**——`Sandbox.resolve`（`sandbox.py:22-30`）：

```python
def resolve(self, relative: str) -> Path:
    candidate = (self.root / relative).resolve()
    if candidate != self.root and self.root not in candidate.parents:
        raise SandboxError(f"Path '{relative}' escapes the sandbox root '{self.root}'.")
    return candidate
```

即：把 root `resolve()` 成绝对路径，再对每个工具传入的路径 `(root/relative).resolve()`，检查结果是否仍在 root 之内。它挡住的是 `../` 目录穿越（读 `/etc/passwd`），**不是** OS 级隔离（网络、进程、CPU 都不隔离）。

分三层安全：

1. **文件层**：所有文件工具（`filesystem.py` 的 `read_file/write_file/edit_file/list_dir`、`search.py` 的 `grep`、`patch.py`、`datasci.py`、`memory.py`）第一步都 `sb.resolve(...)`，逃逸抛 `SandboxError`，被 registry 捕获转字符串（`registry.py:63-64`）。
2. **Shell 层**：分两档
   - `run_bash`（本地全量，`shell.py:15-32`）：`subprocess.run(command, shell=True, cwd=str(sb.root), timeout=60)`，仅用 cwd 把工作目录限制在沙箱，但 `shell=True` 本身可逃逸（README 也标注"local only"）。
   - `run_command`（公开安全，`safe_exec.py`）：`shell=False` + 白名单 `ALLOWED = {"ls","cat","head","tail","wc","grep"}`（`safe_exec.py:33`），并拒绝 `-exec/-delete/-fprint` 等危险 flag（`safe_exec.py:36-45`）和绝对/父目录路径（`safe_exec.py:68-72`）。`find` 被刻意排除（`safe_exec.py:16-21` 注释解释了 `find -exec` 会绕过白名单）。
   - 共享受限执行器 `_subprocess.py`：`SAFE_ENV = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", ...}`（`_subprocess.py:21`），`run_confined` 用 `shell=False, cwd=sb.root, timeout, max_output`（`_subprocess.py:45-51`）；本地可信工具传 `env=None` 继承真实环境（如 git）。
3. **Guardrails 层**（`guardrails.py`）：确定性正则，无 LLM judge，在 `Agent._dispatch` 这个唯一 choke-point（`agent.py:168-193`）统一执行：
   - **secret 外泄拦截** `check_exfiltration`（`guardrails.py:96-111`）：若外部工具（`web_fetch/run_bash/run_command/mcp__*`，`guardrails.py:27`）的参数里出现了某个 `_KEY/_TOKEN/_SECRET/_PASSWORD` 结尾环境变量的值，直接 block。
   - **破坏性命令强制确认** `is_destructive`（`guardrails.py:114-121`）：匹配 `rm -rf /`、fork bomb、`curl|sh`、`sudo`、`mkfs`、`dd of=/dev/`、`git push --force` 等（`guardrails.py:43-53`）。即使 `--yes`（auto_approve）也强制确认（`agent.py:195-200`，非交互时直接拒绝）。
   - **输出脱敏** `redact_secrets`（`guardrails.py:124-128`）：用正则掩盖 `sk-/gsk_/ghp_/xox/…` 等 key 形状子串（`guardrails.py:34-41`）。
   - **不可信内容高亮** `guard_output`（`guardrails.py:131-139`）：`web_fetch`/MCP 的输出前加 `[untrusted external content ... NOT instructions ...]` 前缀（`guardrails.py:55-58`），防 prompt injection。

> 对 CTF agent 的关键结论：pi 的"沙箱"本质是**路径限定 + 命令白名单 + 确定性正则护栏**，不隔离网络/进程，因此它对"给 LLM 一把 `run_bash`"的取舍是：本地可信才开 `run_bash`，公开/不可信环境只给白名单 `run_command`。CTF 打靶恰恰需要真正的 shell 权限，应该学它的**分层开关与 choke-point 设计**，但必须自己加网络隔离/超时/资源限制（pi 只做了 timeout 和输出截断）。

### 3.3 多 provider 抽象（`src/pi_agent/llm.py`）

核心思想：**中立 transcript + 每家翻译层**。中立消息只有三种形状（`llm.py:116-121` 注释）：

```
{"role":"user","content":str}
{"role":"assistant","content":str,"tool_calls":[ToolCall]}
{"role":"tool","results":[ToolResult]}
```

翻译函数把中立 transcript 转成各家 wire format：

- `to_anthropic_messages`（`llm.py:124-150`）：assistant 的 tool_calls → `{"type":"tool_use","id","name","input"}` 块；tool 结果 → `{"role":"user","content":[{"type":"tool_result","tool_use_id","content"}]}`。
- `to_openai_messages`（`llm.py:153-178`）：assistant 的 tool_calls → `{"role":"assistant","tool_calls":[{"type":"function","function":{...,"arguments":json.dumps(args)}}]}`；tool 结果 → 逐条 `{"role":"tool","tool_call_id",...}`。
- `to_openai_tools`（`llm.py:181-193`）：中立 tool spec → OpenAI `function` 包装。

**Provider 接口是一个 Protocol**（`llm.py:201-215`）：

```python
@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str
    @property
    def supports_streaming(self) -> bool: ...
    def complete(self, system, messages, tools) -> AssistantResponse: ...
```

只有两个真实实现类：

- `AnthropicProvider`（`llm.py:218-303`）：直接调用 Anthropic Messages API，`_parse` 把 content blocks 拆成 text + tool_calls（`llm.py:258-270`）。
- `OpenAIProvider`（`llm.py:316-436`）：一个类通吃所有 OpenAI 兼容端点，靠 `base_url` 切换；`_consume_stream` 负责把流式 chunk 的 tool_calls 按 `index` 累积、参数 JSON 分片拼接（`llm.py:377-425`）。

**7 个"provider"里 6 个只是 `OpenAIProvider + base_url`**，注册表 `PROVIDERS`（`llm.py:467-559`）用 `ProviderSpec` 描述静态事实（kind / default_model / key_env / base_url / free / models），例如：

```python
# src/pi_agent/llm.py:489-498（Groq）、511-520（Gemini）、548-558（Ollama）
"groq":  ProviderSpec("groq","openai","llama-3.3-70b-versatile","GROQ_API_KEY",...,
                      base_url="https://api.groq.com/openai/v1", free=True, ...),
"gemini": ProviderSpec("gemini","openai","gemini-3.5-flash","GEMINI_API_KEY",...,
                      base_url="https://generativelanguage.googleapis.com/v1beta/openai/", ...),
"ollama": ProviderSpec("ollama","openai","qwen2.5-coder:7b","OLLAMA_API_KEY",...,
                      base_url="http://localhost:11434/v1", requires_key=False, ...),
```

工厂 `build_provider`（`llm.py:593-627`）据 `spec.kind` 决定构造 `OpenAIProvider` 还是 `AnthropicProvider`；keyless 的 Ollama 塞一个占位 key "ollama"（`llm.py:614-615`）。

**自动检测** `detect_provider`（`llm.py:577-590`）按 `DETECTION_ORDER = ("anthropic","openai","gemini","groq","glm","euri","openrouter")`（`llm.py:565`）扫描环境变量，最后尝试连 Ollama 端口。**`/model` 切换**在 `repl.py:91-107` 的 `_switch_model`：更新 `config.model/provider` 后 `build_provider(...)` 重建 provider 对象，**保留 `agent.messages`（对话不丢，可跨 provider 续聊）**——这正是中立 transcript 的价值。

**成本估算**：`MODEL_PRICING` 子串匹配 + `estimate_cost`（`llm.py:81-112`），仅显示用。**模型列表** `list_models`（`llm.py:630-641`）直接问 provider 的 `/models` 而非信任硬编码。

> 对 CTF agent 的启示：**把"对话状态"和"模型厂商"解耦**是这套设计最有价值的点——你可以用便宜模型（如 GLM/Groq free tier）跑侦察/枚举，用强推理模型（Claude/GPT）跑漏洞利用规划，甚至同一对话中途切模型。CTF 打靶（web/pwn/re/crypto/misc 五类题目差异极大）尤其需要这种"按题目难度/类型选模型"的能力。

### 3.4 持久记忆与自我审查

**持久记忆**（`tools/memory.py`）——一个 `remember` 工具 + 一个 markdown 文件，无黑魔法：

```python
# src/pi_agent/tools/memory.py:21-36
MEMORY_RELPATH = ".pi/memory.md"
MEMORY_RECALL_CAP = 4096   # 召回上限（字节）
MEMORY_FACT_CAP = 500      # 单条事实上限（字符）

def _remember(args, sandbox):
    fact = str(args.get("fact", "")).strip()
    ...
    path = sandbox.resolve(MEMORY_RELPATH)          # 也走沙箱
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"- [{date.today().isoformat()}] {fact}\n")
    return f"Remembered: {fact}"
```

- 写入：模型调 `remember` 追加一行 `- [日期] 事实`。
- 召回：`load_memory`（`memory.py:39-54`）读文件，取**尾部** 4096 字节并对齐行边界（旧事实先被挤掉）。
- 注入：CLI 启动时把 memory 拼进 system prompt（`cli.py:217-222`）：`config.system_prompt += "\n\n## Project memory (from earlier sessions)\n..." + memory`。

设计取舍很明确：用普通 markdown 是为了"用户能用任意编辑器读/改/删"（`memory.py:8-9` 注释：transparency over magic）。

**自我审查**（`agent.py`）——`--reflect` 触发一次有界复盘：

```python
# src/pi_agent/agent.py:221-249
def run(self, user_input: str) -> str:
    answer = self._loop(user_input)
    if self.config.reflect and answer and not answer.startswith("Stopped:"):
        answer = self._reflection_pass(answer)
    return answer

def _reflection_pass(self, answer: str) -> str:
    saved_reflect = self.config.reflect
    saved_iters = self.config.max_iterations
    self.config.reflect = False                    # 防递归
    self.config.max_iterations = min(saved_iters, 5)   # 有界（≤5 次迭代）
    try:
        reviewed = self._loop(REFLECTION_PROMPT)
    finally:
        self.config.reflect = saved_reflect
        self.config.max_iterations = saved_iters
    return reviewed or answer
```

`REFLECTION_PROMPT`（`agent.py:68-73`）让模型"重读改过的文件、查 bug/漏需求/坏 import，用工具修真实问题，再给最终答案"。关键：**review 走的是同一个 `_loop`（有工具），且强制关闭 reflect 防止递归、把 max_iterations 压到 5**。

> 对 CTF agent 的启示：记忆做成"agent 可写、下一会话可召回"的 markdown 很有用——CTF 场景可存"这道题的 flag 格式 / 已试过的 payload / 环境限制"；而"自审用同一个带工具的 loop、只做一次有界 pass"是一个便宜且通用的质量提升手法。

### 3.5 skills 与 sub-agents

**skills**（`skills.py`）：每个技能是一个 `SKILL.md`（frontmatter `name/description/trigger` + 正文），启动时加载并内联进 system prompt（省去一次工具调用读技能）：

```python
# src/pi_agent/skills.py:34-54
def _parse_skill(text, fallback_name) -> Skill:
    # 解析 --- name: / description: / trigger: --- frontmatter，正文为 content
```

- 加载：`load_skills` 遍历 `<skills_dir>/*/SKILL.md`（`skills.py:57-68`）。
- 相关性路由：`select_skills` 用 prompt 与 `name+description+trigger` 的 token 重叠打分，取 top-k（`skills.py:76-90`）；`build_system_prompt` 组装"索引（全部技能的一行简介）+ 内容（仅 top-k 全文）"（`skills.py:93-112`）。
- CLI 默认 `--skills-top-k 3`（`cli.py:156-162`），REPL 内联全部；Web demo 每次按当前 prompt 路由 top 3（`streamlit_app.py:438-441`）。
- 一个真实技能样例 `skills/security-review/SKILL.md`：frontmatter 声明 trigger="when the user asks for a security review"，正文给出 How/Avoid/Done well 的步骤（如 grep `eval(`/`shell=True`/`pickle.load` 等危险模式）。

**sub-agents**（`tools/subagent.py` + `agent.py`）：`delegate` 工具的 schema 定义在 `subagent.py:23-47`，但 handler 是 stub（`subagent.py:18-20`，永远不执行）；真正的执行被 `Agent._dispatch` 拦截：

```python
# src/pi_agent/agent.py:183-184, 202-219
if call.name == "delegate":
    return self._run_subagent(call.args)

def _run_subagent(self, args):
    task = (args.get("task") or "").strip()
    ...
    sub = Agent(
        provider=self.provider,
        registry=self.registry.without("delegate"),   # 去 delegate -> 禁止递归
        sandbox=self.sandbox,                          # 同一工作目录
        config=replace(self.config, max_iterations=min(self.config.max_iterations, 12)),
    )
    result = sub.run(task)
    self.total_usage += sub.total_usage
    return result or "(sub-agent returned no text)"
```

要点：子代理是**顺序**的（不并行）、同一 workspace、同 provider；通过 `registry.without("delegate")`（`registry.py:44-50`）从工具集里去掉 `delegate`，递归深度封顶为 1 层；子代理异常被吞掉转成字符串（`agent.py:216-217`）。

> 对 CTF agent 的启示：skills 机制是"零代码扩展"的典范——CTF 的每种题型（web/pwn/re/crypto/misc）天然就是一个 skill（内含常见手法、工具链、checklist），直接抄它的 `SKILL.md + 相关性路由` 就能做"按题目类型加载对应技能"。delegate 的"单层顺序子代理 + 禁用递归"是一个简单可控的任务分解范式。

---

## 4. CLI 入口、配置目录约定、依赖清单

- **入口**：`pyproject.toml:54-55` 注册 `pi = "pi_agent.cli:main"`；`cli.py:116-251` 的 `main` 处理 `pi`（REPL/one-shot）、`pi ingest`（`cli.py:65-78`）、`pi ask`（`cli.py:81-113`）。参数解析见 `cli.py:123-168`（`--provider/--model/--dir/--yes/--no-shell/--no-stream/--think/--reflect/--no-guardrails/--skills-dir/--skills-top-k/--mcp-config/--version`）。
- **provider/model 解析** `resolve_selection`（`cli.py:26-43`）：显式 flag > `PI_AGENT_MODEL` > 按 key 自动检测 > 该 provider 默认模型。
- **配置目录约定**（全部在工作目录下，前缀 `.pi/`）：
  - `.pi/memory.md` —— 持久记忆（`memory.py:21`）
  - `.pi/kb.sqlite3` —— 本地知识库（`kb.py:26`）
  - `.pi/mcp.json`（或 `~/.pi/mcp.json`）—— MCP 服务器配置（`mcp_client.py:241-248`）
  - 环境变量：`PI_AGENT_MODEL / PI_AGENT_MAX_TOKENS / PI_AGENT_MAX_ITERS`（`config.py:52-59`）
- **依赖清单**（`pyproject.toml:42-51`）：
  - 核心（必需）：`anthropic>=0.40`、`openai>=1.30`、`rich>=13.0` —— **极简**，因为 Groq/Gemini/GLM/EURI/OpenRouter/Ollama 都走 OpenAI 兼容路径，不需要各家 SDK。
  - 可选 extra：`data`（`pandas`、`python-pptx`、`openpyxl`）、`dev`（pytest/ruff/mypy/build/twine）。
  - `requirements.txt` 仅给 Streamlit demo：`streamlit`、`anthropic`、`openai`、`pandas`、`python-pptx`、`openpyxl`。
  - Python `>=3.10`；`setuptools>=77` 打包，src-layout（`pyproject.toml:64-65`）。

---

## 5. 可复用到 CTF 自动打靶 agent 的要点

### 5.1 值得抄的设计（按优先级）

1. **中立 transcript + provider 翻译层**（`llm.py:116-193`，`agent.py` 全程只碰 `NeutralMessage`）——这是最值得抄的架构决策。CTF 打靶中，题目类型与难度差异大：枚举/爆破用便宜模型，逆向/pwn 思路规划用强模型；"对话状态与厂商解耦、可 `/model` 中途切换（`repl.py:91-107`）"让你能按需混合免费 GLM/Groq 和付费 Claude/GPT，省大量 token 成本。**对应文件**：`llm.py` 的 `to_anthropic_messages`/`to_openai_messages`/`to_openai_tools` + `Agent._loop`。

2. **单一工具分发 choke-point + 确定性 guardrails**（`agent.py:168-193` 的 `_dispatch`，`guardrails.py` 全套）——CTF agent 会给 LLM 一把 shell，必须把"跑工具"收敛到一个函数里做：外泄拦截、破坏性命令确认、输出脱敏、不可信内容高亮。这套纯正则、零 LLM judge、可单测的护栏（`guardrails.py` 每条都有对应 test）成本极低但能挡住最蠢的事故。**对应文件**：`guardrails.py`、`agent.py:_dispatch`。

3. **`run_bash`（本地全量）与 `run_command`（白名单只读）双档 shell + 开关装配**（`shell.py`、`safe_exec.py`、`registry.py:67-105`）——CTF 环境本身是隔离靶机，agent 宿主往往可以放心给 shell；但这个"按信任等级选择工具集"的模式可复用于"本地调试 vs 提交平台/外网交互"两种上下文。**对应文件**：`safe_exec.py`（白名单、禁 `find -exec`、禁 `-c/--exec-path` 的思路可直接抄）。

4. **工具即 `Tool` dataclass + registry 装配**（`tools/base.py:21-27`，`registry.py:67-105`）——新增"跑 exp、调 nc、发 HTTP、读 flag 文件、跑 pwntools 脚本"这些 CTF 工具只需写一个 handler + 一个 `Tool` 注册进去，`mutating` 字段天然区分"是否需要确认"。**对应文件**：`tools/base.py`、`tools/registry.py`。

5. **skills = `SKILL.md` + 相关性路由**（`skills.py:76-112`）——CTF 五类题型（web/pwn/re/crypto/misc）各自做成一个 skill（内含套路、工具链、checklist、flag 提交格式），按题目类型用 token 重叠路由只内联 top-k，既省 context 又提升遵循度。**对应文件**：`skills.py` 的 `select_skills`/`build_system_prompt`。

6. **持久记忆 `remember` + 自审 `reflect`**（`memory.py`，`agent.py:221-249`）——CTF 跨回合状态（"已尝试的 payload 列表、flag 正则、目标端口"）用 `remember` 存 `.pi/memory.md` 跨会话召回；每题解完用一次有界 `reflect` 复检（重跑 exp、核对 flag）。**对应文件**：`memory.py:26-54`、`agent.py:232-249`。

7. **工具错误转字符串回喂模型**（`registry.py:52-64`）+ **瞬时错误重试**（`agent.py:104-121`）+ **历史裁剪对齐 user 边界**（`agent.py:123-142`）——这些"韧性"细节让长跑 CTF 循环不崩、不因 context 超长报错。**对应文件**：`registry.py`、`agent.py`。

### 5.2 它的局限（对 CTF 场景必须注意）

- **沙箱只是路径限定，不是进程/网络隔离**（`sandbox.py:22-30`）：`run_bash` 用 `shell=True`，模型可用 `curl`、`ssh`、`exec` 直接逃出"沙箱"触达宿主机和网络。CTF 打靶若在真实环境，必须自己加容器/VM/网络隔离、资源限制（pi 只做了 `timeout` 和输出截断）。
- **sub-agent 仅顺序、单层、不并行**（`agent.py:202-219`，`registry.py:44-50`）：CTF 需要多目标/多方向并行侦察时不够用；且子代理复用同一 provider，不能"子代理用便宜模型、主代理用强模型"。
- **无浏览器自动化、无深度 research 模式**（README ROADMAP `README.md:277-278` 明说"Next up: browser automation, deep-research, parallel sub-agents"）：web 题若需真实浏览器渲染/JS 执行，得自己接 Playwright。
- **HTTP 工具只有 `web_fetch`（SSRF 防护下的 GET 文本）**（`web.py`）：CTF web 题需要自定义 header/cookie/POST/多步会话/原始响应，这个工具不够，得扩展。
- **记忆是单文件 append**（`memory.py:26-36`）：无检索/结构化/向量化，长跑多题会退化成"只召回最后 4096 字节"，跨题目标记复杂状态能力弱。
- **`run_command` 白名单太窄**（`safe_exec.py:33` 只有 `ls/cat/head/tail/wc/grep`），而 CTF 常用 `nc/curl/python/pwntools/gdb/file/strings/objdump` 都需自行加入白名单或改用 `run_bash`。
- **无"分数/状态"追踪**：pi 是通用 coding agent，没有题目状态机（如"已拿到 flag → 提交 → 记录分数"），也没有 flag 提交工具；这些是 CTF agent 的核心增量，需自行设计。

### 5.3 一句话总结

pi-agent 最值得抄的是**"中立 transcript 解耦模型厂商 + 工具 dataclass 统一注册 + 分层路径/命令/正则护栏 + skill 路由扩展 + 确定性有界自审"**这套轻量、透明、可单测的骨架；它刻意不做的（真隔离、并行、浏览器、HTTP 深度、题目状态机）恰恰是 CTF 打靶 agent 需要自己补上的部分。

---

*报告生成自对 `D:\pi-agent` 仓库的逐文件精读（主要引用 `src/pi_agent/` 下 agent.py / llm.py / tools/* / sandbox.py / guardrails.py / skills.py / config.py / cli.py / repl.py / kb.py / mcp_client.py / upload.py / streamlit_app.py，以及 pyproject.toml / requirements.txt / skills/*/SKILL.md）。*
