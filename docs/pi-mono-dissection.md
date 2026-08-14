# Pi（earendil-works/pi）TypeScript Monorepo 编码 Agent 框架深度拆解

> 目标读者：计划构建"自动打 CTF 比赛"agent（DASCTF，Jeopardy：web/pwn/re/crypto/misc）的工程师。
> 本报告基于 `D:\pi-mono` 源码逐文件精读，所有引用标注到 `文件路径:行号`。

---

## 一、架构总览

**一句话定位**：Pi 是一个"自带可扩展交互 CLI 的编码 Agent 运行时"，核心是一个与 LLM provider 完全解耦的 Agent 循环 + 一个统一多 provider LLM 抽象 + 一套基于 TypeScript 模块热加载的扩展/技能系统，并以"事件流 + 可插拔存储"贯穿始终。

**分层**（自底向上）：

| 包 | 职责 | 关键导出 |
|---|---|---|
| `packages/ai`（`pi-ai`） | 统一多 provider LLM API：`Model`/`Provider`/`Models` 抽象、消息类型、事件流协议、OAuth/凭据 | `createModels`、`createProvider`、`streamSimple` |
| `packages/agent`（`pi-agent-core`） | Agent 运行时：turn loop、tool calling、事件类型、状态管理、可插拔 `FileSystem`/`Shell`、Session 存储接口 | `Agent`、`runAgentLoop`、`Session`、`AgentHarness` |
| `packages/coding-agent`（`pi-coding-agent`） | 面向用户的 CLI + SDK：4 种运行模式、内置工具集、Extensions/Skills/Prompts/Themes 加载（Pi Packages） | `main()`、`createAgentSession()`、`createBashTool` 等 |
| `packages/tui`（`pi-tui`） | 终端 UI 库：差分渲染、布局引擎、组件树 | `TUI`、`Container`、`renderLayoutFrame` |
| `packages/session-backends/sqlite-node` | `SessionRepo` 的 SQLite 实现（含 writer lease、迁移） | `SqliteSessionRepository` |
| `packages/telemetry` / `protocol` / `client` / `server` / `evals` | 遥测契约、RPC 协议、客户端/服务端、评测 | — |

**核心数据流**：`CLI 解析 → createAgentSession(Runtime) → AgentSession 订阅 Agent 事件 → Agent.runPromptMessages → runAgentLoop → streamAssistantResponse(convertToLlm→streamFn)→ 执行工具 → 回填 toolResult → 循环`。事件沿 `AgentEvent` 单向流动，UI/扩展只订阅事件。

---

## 二、目录树（关键部分，省略测试）

```
D:\pi-mono
├── package.json                     # workspaces: packages/*, session-backends/*, 扩展示例
├── .pi/                             # 仓库自带的示例 skills/extensions/prompts
├── packages/
│   ├── ai/src/
│   │   ├── index.ts                 # 无副作用 core 导出（类型 + 少量工厂）
│   │   ├── types.ts                 # Message/Context/Tool/Model/事件协议/ProviderStreams
│   │   ├── models.ts                # Provider/Models/createProvider/createModels
│   │   ├── models.generated.ts      # 生成的内置模型目录（含 deepseek 等）
│   │   ├── api/                     # 每 API 一个适配器（openai-responses/anthropic-messages/...）
│   │   ├── providers/               # 每 provider 一个工厂（anthropic.ts/deepseek.ts/.../all.ts）
│   │   └── auth/                    # 凭据存储 + OAuth 流程
│   ├── agent/src/
│   │   ├── agent.ts                 # Agent 类（状态 + 队列 + 事件派发）
│   │   ├── agent-loop.ts            # runAgentLoop / runLoop / 工具执行
│   │   ├── types.ts                 # AgentEvent/AgentMessage/AgentTool/AgentLoopConfig
│   │   ├── stream-fn.ts             # 默认 streamFn
│   │   └── harness/
│   │       ├── agent-harness.ts     # 高级 AgentLane（会话树 + 持久化 + hooks）
│   │       ├── types.ts             # FileSystem/Shell/ExecutionEnv/Skill/PromptTemplate
│   │       ├── tools/{bash,read,write,edit,edit-diff}.ts   # 底层工具工厂
│   │       ├── system-prompt.ts / skills.ts / prompt-templates.ts
│   │       ├── compaction/          # 上下文压缩
│   │       └── session/
│   │           ├── types.ts         # Entry/Record/SessionStorage/SessionRepo/SessionTree
│   │           ├── session.ts       # Session 门面（校验 + lane 视图）
│   │           └── jsonl/           # 默认 JSONL 后端（repo/storage/codec）
│   ├── coding-agent/src/
│   │   ├── main.ts                  # CLI 入口（解析 + 模式分发）
│   │   ├── config.ts                # ~/.pi/agent 约定、APP_NAME/CONFIG_DIR_NAME
│   │   ├── cli.ts / bun/cli.ts      # 可执行入口
│   │   ├── modes/                   # interactive/print/json-event/rpc 四模式
│   │   └── core/
│   │       ├── sdk.ts               # createAgentSession 公开 API
│   │       ├── messages.ts          # convertToLlm（coding-agent 定制消息）
│   │       ├── tools/               # bash/read/write/edit/grep/find/ls
│   │       ├── extensions/{loader,runner,types,wrapper}.ts
│   │       ├── skills.ts / prompt-templates.ts / resource-loader.ts
│   │       ├── pi-manifest.ts       # package.json 的 "pi" 字段
│   │       ├── session-manager.ts / model-runtime.ts / model-registry.ts
│   │       └── compaction/
│   ├── tui/src/                     # tui.ts / layout.ts / tui-main-screen.ts / components/
│   └── session-backends/sqlite-node/src/sqlite/
│       ├── repo.ts                  # SqliteSessionRepository（writer lease）
│       ├── migrations.ts + migrations/001_initial.sql
│       └── storage/                 # 每表一个存储模块
```

---

## 三、核心机制逐项

### 3.1 Agent 循环（packages/agent）

循环实现在 `agent-loop.ts`，状态/队列在 `agent.ts`。核心结构是 **双层循环**：

- **外层**：处理 `followUp` 队列（agent 本要停止时才继续）
- **内层**：处理 `steering` 队列 + 工具调用（一次 assistant 响应 + 若干工具结果为一"turn"）

```ts
// packages/agent/src/agent-loop.ts:155-175
async function runLoop(initialContext, newMessages, initialConfig, signal, emit, streamFunction) {
  let currentContext = initialContext;
  let firstTurn = true;
  let pendingMessages = (await config.getSteeringMessages?.()) || [];
  while (true) {                       // 外层：follow-up 续跑
    let hasMoreToolCalls = true;
    while (hasMoreToolCalls || pendingMessages.length > 0) {  // 内层：工具/steering
      if (!firstTurn) await emit({ type: "turn_start" }); else firstTurn = false;
      if (pendingMessages.length > 0) { /* 注入并 emit message_start/end */ }
      const message = await streamAssistantResponse(currentContext, config, signal, emit, streamFunction);
      const toolCalls = message.content.filter((c) => c.type === "toolCall");
      // stopReason === "length" 时全部工具调用按失败处理（参数可能被截断）
      const executedToolBatch = message.stopReason === "length"
        ? await failToolCallsFromTruncatedMessage(toolCalls, emit)
        : await executeToolCalls(currentContext, message, config, signal, emit);
      hasMoreToolCalls = !executedToolBatch.terminate;
      await emit({ type: "turn_end", message, toolResults });
      if (await config.shouldStopAfterTurn?.(...)) { await emit({ type: "agent_end", ... }); return; }
      pendingMessages = (await config.getSteeringMessages?.()) || [];
    }
    const followUpMessages = (await config.getFollowUpMessages?.()) || [];
    if (followUpMessages.length > 0) { pendingMessages = followUpMessages; continue; }
    break;
  }
  await emit({ type: "agent_end", messages: newMessages });
}
```

**LLM 调用边界**：`AgentMessage[]` 只在 `streamAssistantResponse` 里被 `convertToLlm` 转换成 `Message[]`，再通过 `streamFn`（通常是 `Models.streamSimple`）发往 provider。

```ts
// packages/agent/src/agent-loop.ts:288-312
let messages = context.messages;
if (config.transformContext) messages = await config.transformContext(messages, signal); // AgentMessage 级裁剪
const llmMessages = await config.convertToLlm(messages);  // AgentMessage[] -> Message[]
const llmContext = { systemPrompt: context.systemPrompt, messages: llmMessages, tools: context.tools };
const resolvedApiKey = (config.getApiKey ? await config.getApiKey(config.model.provider) : undefined) || config.apiKey;
const response = await streamFunction(config.model, llmContext, { ...config, apiKey: resolvedApiKey, signal });
```

流式响应被逐事件转发为 `message_start` / `message_update` / `message_end`（`agent-loop.ts:317-371`），并把 partial 写回 `context.messages`。

**工具执行**：支持 `parallel` / `sequential` 两种模式（`agent-loop.ts:411-426`），每个工具调用经历 `prepareToolCall`（找工具→`prepareArguments`→`validateToolArguments`→`beforeToolCall` 钩子）→ 执行（`executePreparedToolCall` 捕获 `onUpdate` 增量）→ `finalizeExecutedToolCall`（`afterToolCall` 钩子合并覆盖）→ `createToolResultMessage`。`prepareToolCall` 找不到工具/校验失败/被 `beforeToolCall` block 时，会"立即返回一个错误 toolResult"而不是抛异常中断循环：

```ts
// packages/agent/src/agent-loop.ts:607-647（节选）
const tool = currentContext.tools?.find((t) => t.name === toolCall.name);
if (!tool) return { kind: "immediate", result: createErrorToolResult(`Tool ${toolCall.name} not found`), isError: true };
const validatedArgs = validateToolArguments(tool, preparedToolCall);
if (config.beforeToolCall) {
  const beforeResult = await config.beforeToolCall({ assistantMessage, toolCall, args: validatedArgs, context }, signal);
  if (beforeResult?.block) {
    const result = createErrorToolResult(beforeResult.reason || "Tool execution was blocked");
    if (beforeResult.terminate === true) result.terminate = true;
    return { kind: "immediate", result, isError: true };
  }
}
```

**事件类型**（`packages/agent/src/types.ts:428-443`）：

```ts
export type AgentEvent =
  | { type: "agent_start" } | { type: "agent_end"; messages: AgentMessage[] }
  | { type: "turn_start" } | { type: "turn_end"; message: AgentMessage; toolResults: ToolResultMessage[] }
  | { type: "message_start"; message: AgentMessage }
  | { type: "message_update"; message: AgentMessage; assistantMessageEvent: AssistantMessageEvent }
  | { type: "message_end"; message: AgentMessage }
  | { type: "tool_execution_start"; toolCallId; toolName; args }
  | { type: "tool_execution_update"; toolCallId; toolName; args; partialResult }
  | { type: "tool_execution_end"; toolCallId; toolName; result; isError };
```

**AgentMessage 与 convertToLlm**：`AgentMessage = Message | CustomAgentMessages[keyof ...]`（`types.ts:325`），通过 **declaration merging** 让上层扩展自定义消息类型；`convertToLlm` 负责把自定义消息翻译成 LLM 认识的 `user/assistant/toolResult` 或过滤掉。核心库提供默认实现（`agent.ts:33-37`），只保留 `user/assistant/toolResult`；coding-agent 提供增强版（见 3.3）。

**状态管理**（`packages/agent/src/agent.ts`）：`Agent` 持有 `MutableAgentState`（`messages`/`tools` 用 setter 做浅拷贝，`isStreaming`/`streamingMessage`/`pendingToolCalls`/`errorMessage` 为运行时态，`agent.ts:61-95`）。`processEvents`（`agent.ts:544-591`）在派发给订阅者前先 reduce 自身状态。两个队列 `steeringQueue`/`followUpQueue` 支持 `QueueMode = "all" | "one-at-a-time"`（`agent.ts:125-159`）。

**关键 hook 契约**（`types.ts:149-293`）：`AgentLoopConfig` 上的 `convertToLlm`、`transformContext`、`getApiKey`、`beforeToolCall`、`afterToolCall`、`shouldStopAfterTurn`、`prepareNextTurn`、`getSteeringMessages`、`getFollowUpMessages` 都注明"不得 throw/reject，必须返回安全回退值"——这是把失败都编码进事件流的健壮性约定。

**高级抽象 `AgentHarness`**（`harness/agent-harness.ts`）：在裸 `Agent` 之上加"会话树 + 持久化 + 多 lane + 可恢复运行"。`AgentLane` 接口（`agent-harness.ts:271-303`）暴露 `prompt/skill/promptFromTemplate/compact/navigateTree/resume/abort/steer/followUp/nextRun/runToCompletion` 等，底层以 `Entry`/`Record` 记录所有动作以便崩溃恢复（`RunOutcome` 有 `completed/aborted/failed/suspended`，`suspended` 用于 deferred 响应）。

### 3.2 Provider 抽象（packages/ai）

**两级抽象**：`Provider`（具体运行时单元）与 `Models`（provider 集合 + 鉴权 + 分发）。

```ts
// packages/ai/src/models.ts:97-149（节选）
export interface Provider<TApi extends Api = Api> {
  readonly id: string;
  readonly name: string;
  readonly baseUrl?: string;
  readonly headers?: ProviderHeaders;
  readonly auth: ProviderAuth;            // 每个 provider 都有鉴权语义（apiKey/oauth）
  getModels(): readonly Model<TApi>[];    // 同步目录
  refreshModels?(context): Promise<void>; // 动态刷新 + 持久化
  stream(model, context, options?): AssistantMessageEventStream;
  streamSimple(model, context, options?): AssistantMessageEventStream;
  fetchDeferred?(...); cancelDeferred?(...);
}
```

`Models`（`models.ts:156-223`）的关键方法是 `stream`/`streamSimple`/`complete`/`completeSimple`/`refresh`/`getAvailable`/`login`/`logout`。`streamSimple` 通过 `lazyStream` 延迟到实际消费时才 `applyAuth`（解析凭据 + 合并 header + 覆盖 baseUrl）再委托给 provider：

```ts
// packages/ai/src/models.ts:690-696
streamSimple(model, context, options?) {
  return lazyStream(model, async () => {
    const provider = this.requireProvider(model);
    const { requestModel, requestOptions } = await this.applyAuth(model, options);
    return provider.streamSimple(requestModel, context, requestOptions);
  });
}
```

**provider 注册**：`createProvider`（`models.ts:762-862`）从"id/name/auth/models/api"零件组装 `Provider`；`api` 可以是单个 `ProviderStreams`，也可以是 `Record<Api, ProviderStreams>` 按 `model.api` 分发——一个 provider 可同时服务多种 API 方言。内置 provider 集中在 `providers/all.ts` 的 `builtinProviders()`（`all.ts:89-132`，含 deepseek/openrouter/github-copilot 等 40+ 家），`builtinModels()` 把它们注册进一个 `Models`（`all.ts:135-141`）。

**统一流协议**：`ProviderStreams`（`types.ts:268-277`）要求每个 `src/api/*` 模块导出 `stream` 与 `streamSimple`；`streamSimple` 收 `SimpleStreamOptions`（`types.ts:304-310`，含 `reasoning`/`deferred`/`thinkingBudgets`）。流事件协议 `AssistantMessageEvent`（`types.ts:523-539`）统一为 `start`/`text_delta`/`thinking_delta`/`toolcall_delta`/`done`/`error`，**错误不进 throw，而是以 `done`/`error` 事件 + `stopReason`/`errorMessage` 收尾**：

```ts
// packages/ai/src/types.ts:523-539（节选）
export type AssistantMessageEvent =
  | { type: "start"; partial: AssistantMessage }
  | { type: "text_delta"; contentIndex: number; delta: string; partial }
  | { type: "thinking_delta"; ... }
  | { type: "toolcall_end"; contentIndex; toolCall: ToolCall; partial }
  | { type: "done"; reason: "stop"|"length"|"toolUse"|"deferred"; message }
  | { type: "error"; reason: "aborted"|"error"; error: AssistantMessage };
```

**核心消息/工具类型**（`types.ts`）：`Message = UserMessage | AssistantMessage | ToolResultMessage`（`455`）；`Tool` 用 **TypeBox schema** 描述参数（`502-507`），并支持 `constrainedSampling`（JSON schema strict 或 Lark/regex grammar）。`AssistantMessage.stopReason`（`393`）有 `pending/stop/length/toolUse/error/aborted/deferred`。成本计算 `calculateCost`（`models.ts:878-898`）按 `cacheRead/cacheWrite` 与阶梯价计算。

> 关键设计点：**LLM 层"永抛异常"被显式禁止**——`StreamFunction` 契约（`types.ts:314-324`）要求失败编码进流。这使得上层 agent loop 无需 try/catch 每个 provider 错误。

### 3.3 工具系统（packages/agent 底层 + packages/coding-agent 内置）

工具分两层：

1. **底层（harness）**：`AgentHarnessTool` 依赖注入 `FileSystem`/`Shell`/`ExecutionEnv` 抽象（`harness/types.ts:231-315`），全部操作返回 `Result<T,E>` 而非 throw，并带稳定错误码（`FileErrorCode`/`ExecutionErrorCode`）。这让 agent 可跑在任意后端（本地/沙箱/容器），是 Pi 容器化方案（Gondolin/Docker）的地基。

2. **内置工具（coding-agent）**：`read / bash / edit / write / grep / find / ls` 七种，见 `core/tools/index.ts:83-84`。工厂模式 `createXxxTool` 与 `createXxxToolDefinition` 分离（Definition 仅描述 + `promptSnippet`/`promptGuidelines`，用于系统提示词）。

bash 工具的 `BashOperations` 是可替换执行后端：

```ts
// packages/coding-agent/src/core/tools/bash.ts:62-80
export interface BashOperations {
  exec(command, cwd, options: { onData; signal?; timeout?; env? }): Promise<{ exitCode: number | null }>;
}
// 本地实现 createLocalBashOperations：spawn shell，流式 onData，signal -> killProcessTree
```

`createLocalBashOperations`（`bash.ts:88-140`）用 `detached` + `killProcessTree` 处理超时/中断，`waitForChildProcess` 避免被子进程继承的 stdio 句柄挂起。

`ToolDefinition`（`extensions/types.ts:449-498`）在 `AgentTool` 之上加了 UI 渲染与提示词字段：`promptSnippet`/`promptGuidelines`、`renderCall`/`renderResult`、`renderShell`。`defineTool` 保留参数推断。

### 3.4 扩展机制（Pi Packages）

**入口与 API**：扩展是"默认导出 `(api) => void` 工厂函数"的 TS/JS 模块，由 `jiti` 即时编译加载（`extensions/loader.ts:436-464`）。`ExtensionAPI`（`extensions/types.ts:1198-1437`）提供：`on(event)` 订阅、`registerTool/registerCommand/registerShortcut/registerFlag`、`registerMessageRenderer/registerMarkdownTransformer/registerEntryRenderer`、动作方法（`sendMessage/sendUserMessage/appendEntry/exec/setModel/registerProvider`）、以及共享 `events` 事件总线。

**加载器**（`extensions/loader.ts`）：
- `VIRTUAL_MODULES`（`loader.ts:50-74`）：把 `@earendil-works/pi-*`/typebox 等映射为内存模块，Bun 编译成单二进制后扩展仍能 import 这些包。
- `loadExtensionModule`（`436-464`）：`createJiti(...).import(extensionPath)`，三种模式（Bun 二进制用 virtualModules；tsx 源码用 tsconfigPaths；dist 用 alias）。
- 目录发现 `resolveExtensionEntries`（`610-640`）：目录里优先 `package.json` 的 `"pi.extensions"` 字段，否则 `index.ts/js`。
- `discoverAndLoadExtensions`（`689-737`）：加载顺序为 **项目本地 `.pi/extensions/` → 全局 `~/.pi/agent/extensions/` → 显式路径**。

**Pi Packages manifest**（`pi-manifest.ts:3-33`）：任何 npm 包可在 `package.json` 里声明：

```json
{ "pi": { "extensions": [...], "skills": [...], "prompts": [...], "themes": [...] } }
```

`readPiManifest` 只读取这四个数组字段。

**资源聚合**（`resource-loader.ts`）：`DefaultResourceLoader.reload()`（`resource-loader.ts:387-546`）统一编排：先 `packageManager.resolve()` 得到启用的资源路径（支持 enable/disable + trust 门控），再分别加载 extensions/skills/prompts/themes，最后 `discoverSystemPromptFile`（`1022-1034`）找 `SYSTEM.md`、`discoverAppendSystemPromptFile` 找 `APPEND_SYSTEM.md`。还带 **project trust** 逻辑：不可信项目会先以"不信任"模式只加载 user/global 扩展，再由 `resolveProjectTrust` 回调决定是否放行项目级资源（`resource-loader.ts:379-385, 394-399`）。

**扩展事件系统**（`extensions/types.ts:1034-1059`）：`ExtensionEvent` 覆盖 `session_start/before_compact/context/before_provider_request/agent_start/turn_start/message_update/tool_call/tool_result/input` 等；`tool_call`/`tool_result` 事件允许扩展 **原地改参数（mutable input）或 block**，`user_bash` 可完全接管执行（`BashOperations`）。这是做"权限门禁/危险操作拦截"的挂点（参考 `examples/extensions/permission-gate.ts`、`confirm-destructive.ts`、`protected-paths.ts`）。

**convertToLlm（coding-agent 版）**（`core/messages.ts:148-195`）：通过 declaration merging 注册 `bashExecution/custom/branchSummary/compactionSummary` 四种自定义消息，再把它们转成 user 消息或过滤（`excludeFromContext` 的 `!!` 命令不回传给 LLM）。这是"非 LLM 内容入会话、但不污染 LLM 上下文"的典型做法。

### 3.5 Skills / Prompt Templates（技能扩展机制）

**Skill**：目录里的 `SKILL.md`（含 frontmatter `name/description/disable-model-invocation`）。加载规则见 `skills.ts:160-275`：若目录含 `SKILL.md` 则视为 skill 根、不再下钻；否则递归找 `SKILL.md`，根目录直接 `.md` 文件也当 skill。`loadSkills`（`skills.ts:387-487`）合并 `~/.pi/agent/skills`（user）与 `.pi/skills`（project）+ 显式路径，按名字去重并报告冲突。技能以 **XML 块**注入系统提示词（符合 agentskills.io 规范）：

```ts
// packages/coding-agent/src/core/skills.ts:342-359（节选）
const lines = [
  "\n\nThe following skills provide specialized instructions for specific tasks.",
  "Use the read tool to load a skill's file when the task matches its description.",
  "<available_skills>", ...
];
// 每个 skill 输出 <skill><name>…</name><description>…</description><location>…</location></skill>
```

**Prompt Template**：`prompt-templates.ts` 提供 `/模板名 参数…` 显式调用与占位符格式化（`formatPromptTemplateInvocation`）。Harness 侧统一类型在 `harness/types.ts:46-67`（`Skill`/`PromptTemplate`）与 `harness/prompt-templates.ts`。

> 对 CTF 的启示：SKILL.md 是天然的"打靶技能包"载体——pwn 打法、web 常见漏洞利用清单、crypto 工具链调用模板各写一个 skill，靠 description 让模型自路由，靠 location 绝对路径让模型自己 read 详情。

### 3.6 事件与 TUI（packages/tui）

**差分渲染要点**（`tui.ts` / `layout.ts` / `tui-main-screen.ts`）：
- 组件是 `render(width): string[]` + 可选 `handleInput`/`invalidate` 的极简接口（`tui.ts:23-47`）。
- 渲染被合并 + 节流：`requestRender` 用 `process.nextTick` 合并，`MIN_RENDER_INTERVAL_MS=16` 限频（`tui.ts:343, 772-824`），键盘输入走 `requestImmediateRender` 低延迟通道。
- 每帧 `renderLayoutFrame`（`layout.ts:353-382`）按布局树（vstack/hstack/scroll）计算每组件 rect/clip，`renderCached` 按 `(component, width)` 缓存渲染结果，再 `paintBox` 逐行合成到屏幕 buffer。
- 真正输出时对"上一帧行"与"新行"做 **逐行 diff**，只重绘变化区间（`tui-main-screen.ts:180-` 的 `doRender`，全量重绘只发生在首帧/宽度变化/高度变化/超出滚动等少数情况）；支持 Kitty 图像协议、synchronized output（`\x1b[?2026h`）、cursor 定位标记。

**事件→TUI**：交互模式（`modes/interactive/interactive-mode.ts`）订阅 `AgentSessionEvent`，把 `message_update` 的流式增量映射到组件重绘；工具执行组件（`tool-execution.ts`）展示 `tool_execution_start/update/end`。对 CTF 的意义主要是"若需要给人看的实时面板可复用其 diff 思路"，但 headless 打靶可完全绕过 TUI 用 RPC/JSON 模式。

### 3.7 配置/目录约定

`config.ts` 定义（`config.ts:487-561`）：

- `APP_NAME = "pi"`，`CONFIG_DIR_NAME = ".pi"`（可通过 `piConfig.configDir` 覆盖）。
- **全局配置目录** `getAgentDir()` = `~/.pi/agent`（可被 `PI_CODING_AGENT_DIR` 覆盖），其下：
  - `extensions/`、`skills/`、`prompts/`、`themes/`、`tools/`、`bin/`、`sessions/`
  - `models.json`（自定义 provider/model）、`auth.json`（凭据）、`settings.json`
  - `SYSTEM.md`、`APPEND_SYSTEM.md`（系统提示词覆盖）
- **项目本地**：`<cwd>/.pi/` 下的 `extensions/skills/prompts/themes` 与 `SYSTEM.md`（受 project trust 门控）。
- 会话目录 `getSessionsDir()`，默认 JSONL 后端按 `cwd` 哈希命名目录：`jsonlSessionDirectoryName(cwd) = "--<cwd-归一化>--"`（`session/jsonl/repo.ts:27-29`）。

**发现顺序**（extensions/skills 均是"项目本地优先、全局其次、CLI 显式路径"）：见 `extensions/loader.ts:710-733` 与 `skills.ts:430-433`。

### 3.8 会话持久化：SQLite Session Backend

`packages/agent` 定义**存储无关**的三层接口（`session/types.ts`）：

- `SessionStorage`（`types.ts:290-326`）：Lanes / Entries / Records / Log / facts / stats 的读写接口。
- `SessionRepo`（`types.ts:361-373`）：`create/open/list/delete/fork`。
- `SessionTree`（`types.ts:328-352`）：面向会话树的读 + `appendMessage/appendCustomEntry`。

**数据模型**：`Entry`（消息/模型变更/thinking 变更/active_tools 变更/compaction/branch_summary/custom，`types.ts:67-74`）构成**带 parent_id 的树**；`Record`（operation_started/step_attempt/tool_started/queue_enqueued/usage/…，`types.ts:203-212`）是**运行日志**，用于崩溃恢复。二者共享递增 `seq` 生成 `LogItem`（`getLog`）。

**SQLite 实现**（`session-backends/sqlite-node/src/sqlite/repo.ts`）要点：
- `SqliteSessionRepository` 实现 `SessionRepo` + `AsyncDisposable`（`repo.ts:669-673`）。
- **Writer lease**（防多写者）：`claimWriterLease`（`repo.ts:132-137`）在事务里写 `writer_leases`（owner_id/fence/expires_at_ms）；每次写操作先 `renewWriterLease` 续租、失败即 `lostWriterError`（`repo.ts:377-415`）；心跳定时器（`scheduleHeartbeat`，默认 TTL 30s、心跳 10s）续租。
- 所有写操作经 `SerialOperationQueue` 串行化 + 事务包裹（`appendEntry` `repo.ts:456-484`），`appendEntry` 同时写 entries 表、更新 lane leaf、增量维护 `branch_entries` 缓存、`incrementMessageCount`、推进 seq。
- **迁移**：`applyMigrations`（`migrations.ts:35-49`）建 `migrations` 表并顺序应用 `001_initial.sql`。

**Schema**（`migrations/001_initial.sql`）：`sessions` / `entries`（`payload` 存 JSON，`UNIQUE(session_id,seq)`）/ `session_sequences` / `session_stats` / `branch_entries`（派生的分支读缓存，parent 链接仍以 entries 为准）/ `lanes`（含 `open_operation_id`）/ `records`（按 lane/type/run_id 建索引）/ `lane_moves` / `facts`（name/label 最新值）/ `branch_tips` / `writer_leases`（fence 防旧租约复活）。WAL + `synchronous=FULL` + `busy_timeout=5000`（`repo.ts:172-176`）。

> 两个后端（JSONL 与 SQLite）实现同一套 `SessionRepo` 接口——`Session` 门面（`session/session.ts`）在写入前用 `assertJsonSerializable` 做深度校验（`session.ts:42-100`），保证任何后端都能持久化。

---

## 四、CLI 入口与四种运行模式

`main.ts` 是唯一入口：解析参数 → 建 `SessionManager`/`SettingsManager`/`ModelRuntime`/`ResourceLoader` → `createAgentSessionRuntime` → 按模式分发。

模式判定（`main.ts:118-129`）：`--mode rpc` → RPC；`--mode json` 或 `--print` 或非 TTY → print；否则 interactive。分发在 `main.ts:927-976`：

- **interactive**：`InteractiveMode`（TUI 全屏，订阅事件流，支持 `/` 命令、`!` bash、模型/主题选择器）。
- **print**：`runPrintMode`，一次性执行，输出文本或 JSON。
- **json**：print 模式的 JSON 变体，事件经 `toJsonEvent`（`modes/json-event.ts:29-46`）剥离流式 `message_update` 里的累积 `partial`，只保留增量 delta + usage。
- **rpc**：`runRpcMode`（`modes/rpc/rpc-mode.ts`），JSON stdin/stdout 协议，command/response/event 三类消息，扩展 UI 请求由客户端应答——是 headless 嵌入（打靶 orchestrator）的最佳入口。
- **SDK**：`createAgentSession()`（`core/sdk.ts:38-87`），`CreateAgentSessionOptions` 可注入 `cwd/agentDir/modelRuntime/model/tools/customTools/resourceLoader/sessionManager` 等。

---

## 五、可复用到 CTF 自动打靶 agent 的要点

以下按"值得抄什么 → 在哪 → 为什么对 CTF 有用"列出：

1. **`Agent` 的纯事件流 + hook 化循环**（`packages/agent/src/agent.ts` + `agent-loop.ts` + `types.ts:149-293`）
   打靶 agent 需要"循环→调工具→看结果→再循环"且要能中途打断/注入。`AgentLoopConfig` 的 `beforeToolCall`（做命令白名单/危险操作拦截）、`afterToolCall`（归一化工具输出）、`shouldStopAfterTurn`（拿到 flag 或超时就停）、`getSteeringMessages/getFollowUpMessages`（人工/编排器中途塞指令）、`prepareNextTurn`（按上下文切换模型/思维强度）几乎就是打靶状态机需要的全部控制点。

2. **`convertToLlm` + declaration merging**（`packages/agent/src/types.ts:302-325`、`coding-agent/src/core/messages.ts:70-77,148-195`）
   把"工具原始输出/扫描结果/进度条"这类非 LLM 内容存进会话（自定义 `AgentMessage`），再由 `convertToLlm` 决定哪些进 LLM 上下文、哪些过滤。CTF 场景可自定义 `FlagFoundMessage`、`ScanResultMessage`，避免污染上下文又保留审计轨迹。

3. **`FileSystem`/`Shell` 抽象 + `Result<T,E>` 不抛错契约**（`packages/agent/src/harness/types.ts:231-315`）
   这是把 agent 与"本地 shell / 容器 / 远程靶机（SSH）"解耦的关键。打靶需要：本地跑扫描器、连远程 web 靶、跑 pwn 的 nc/脚本，都要能换执行后端。`BashOperations`（`coding-agent/src/core/tools/bash.ts:62-80`）与扩展 `ssh.ts`、`gondolin`（微 VM）示例证明这套抽象可直接拿来接"靶机连接器"。

4. **Provider 抽象 + 自定义 provider 注册**（`packages/ai/src/models.ts:762-862`、`extensions/types.ts:1417-1459` 的 `registerProvider`）
   比赛可能要用本地部署模型 / 中转网关 / 免费额度轮换多个 key。`createProvider` 支持自定义 `baseUrl` + `apiKey: "$ENV_VAR"` + 任意 `api` 方言；`getApiKey`（`agent.ts`/`types.ts:202-210`）可每次调用动态换 key——适合做"多 API key 池 + 失败轮换"。

5. **Skill（SKILL.md）技能包机制**（`coding-agent/src/core/skills.ts:335-361`）
   直接复用为"CTF 技能库"：每个分类一个 skill（web 常见漏洞、pwn 套路、re 工具链、crypto 数学库、misc 编码），靠 frontmatter `description` 让模型按题面自路由，靠 XML `<location>` 让模型自己 read 详情，`disable-model-invocation` 控制是否主动触发。这是最省事的"知识注入"方案。

6. **Pi Packages（`pi` 字段 + 资源发现）**（`pi-manifest.ts:3-33`、`extensions/loader.ts:689-737`、`resource-loader.ts:387-546`）
   把"工具/技能/提示词模板/主题"打包成可分发 npm 包，`packageManager.resolve()` 支持 enable/disable 与 trust 门控。CTF 打靶框架可据此做"题目平台插件"：装一个包即获得该比赛平台的题目拉取/提交/判题工具链。

7. **SQLite Session 的 Entry/Record + writer lease**（`session/types.ts:67-74,203-212`、`sqlite/repo.ts:132-137,377-415`）
   打靶要长时间、可断点续跑、可审计。`Entry`（消息树，支持 fork 分支）天然支持"一个靶多解并行探索再回滚"；`Record`（运行日志）支持崩溃恢复与"本次尝试做了什么"复盘；writer lease 保证多进程（多个 agent 并发打多个题）不互相踩库。

8. **RPC 模式作为 headless 编排入口**（`modes/rpc/rpc-mode.ts`、`modes/json-event.ts`）
   自动打靶不要 TUI。RPC/JSON 模式把整个 agent 变成"stdin 下命令、stdout 收事件"的进程，`toJsonEvent` 剥离累积 `partial` 减少带宽——可直接被 Python 编排器（拉题→下发→监听事件→判 flag→提交）子进程化调用。

9. **工具输出截断与流式 `onUpdate`**（`agent/src/harness/tools/bash.ts:51-161`、`coding-agent/src/core/tools/truncate.ts`）
   打靶工具输出常巨大（目录爆破、网络扫描）。bash 工具"截断到尾部 N 行/N KB + 全量落临时文件 + 每 100ms 节流增量上报"的模式值得直接照抄；`AgentToolResult` 的 `details`（结构化）与 `content`（给 LLM 的文本）分离，让编排器拿到机器可读结果、模型拿到精炼文本。

10. **把失败编码进流、不抛异常**（`packages/ai/src/types.ts:314-324`、`agent-loop.ts:196-200`）
    CTF 环境网络抖动/超时/靶机重启很常见。Pi 从 provider 到工具执行都"失败即产生 error 事件或 error toolResult 而非 crash 循环"，`stopReason === "length"` 时对截断的工具调用一律按失败处理（`agent-loop.ts:207-214`），这些健壮性约定对长时间无人值守打靶至关重要。

**落地建议**：以 `pi-agent-core`（Agent + harness 工具 + Session 接口）为运行时底座，`pi-ai` 做模型接入，自写 coding-agent 之外的轻量 headless 编排层（复用其 RPC/JSON 事件协议思路），技能用 SKILL.md 承载，靶机连接用 `FileSystem/Shell` 抽象 + 扩展 `registerProvider/registerTool` 接入，持久化直接套 SQLite `SessionRepo`。

---

*报告结束。源码根：`D:\pi-mono`。*
