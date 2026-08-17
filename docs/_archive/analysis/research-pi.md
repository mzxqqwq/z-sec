# pi (earendil-works/pi) 运行时能力研究报告

> 目标仓库：`D:\ctf-agent\pi-mono`（TypeScript 源码，重点 `packages/coding-agent/src`，底层 runtime 在 `packages/agent/src` 与 `packages/ai/src`）。
> 用途：为把 pi 当作 worker 后端（`--mode json` 事件流）的队内架构设计提供证据。
> 每个结论都附 `文件路径:行号` + 关键代码片段 + 一句话机制总结。

---

## 1. 会话续跑 / 注入消息

### 1.1 CLI 三个 flag 的语义

参数解析（`packages/coding-agent/src/cli/args.ts`）：

```ts
86: } else if (arg === "--continue" || arg === "-c") {
87:     result.continue = true;
88: } else if (arg === "--resume" || arg === "-r") {
89:     result.resume = true;
...
113: } else if (arg === "--fork" && i + 1 < args.length) {
114:     result.fork = args[++i];
```

帮助文案（`args.ts:271-277`）：
```ts
271:   --continue, -c                 Continue previous session
272:   --resume, -r                   Select a session to resume
274:   --session-id <id>              Use exact project session ID, creating it if missing
275:   --fork <path|id>               Fork specific session file or partial UUID into a new session
276:   --session-dir <dir>            Directory for session storage and lookup
277:   --no-session                   Don't save session (ephemeral)
```

三者实际实现（`packages/coding-agent/src/main.ts:360-451`）：

```ts
370: 	if (parsed.fork) {
...
379: 		const resolved = await resolveSessionPath(parsed.fork, cwd, sessionDir);
381: 		switch (resolved.type) {
382: 			case "path": case "local": case "global":
385: 				return forkSessionOrExit(resolved.path, cwd, sessionDir, parsed.sessionId);
...
417: 	if (parsed.resume) {
419: 			const selectedPath = await selectSession(...);   // 交互式选择器
428: 			return SessionManager.open(selectedPath, sessionDir);
...
434: 	if (parsed.continue) {
435: 		return SessionManager.continueRecent(cwd, sessionDir);
436: 	}
...
450: 	return SessionManager.create(cwd, sessionDir, { id: parsed.sessionId });
```

- `--continue`：`SessionManager.continueRecent`（`session-manager.ts:1557-1565`）→ 用 `findMostRecentSession`（`session-manager.ts:635-656`，按 mtime 排序取最新 `.jsonl`）打开最近会话。
- `--resume`：交互式 `selectSession` 列表选择后 `SessionManager.open`。
- `--fork`：`SessionManager.forkFrom`（`session-manager.ts:1579-1630`）→ 新建一个带新 session id 的 `.jsonl`，header 里写 `parentSession: resolvedSourcePath`，并把源会话所有非 header entry 原样拷贝，历史不动（append-only 分支）。

三者互斥约束在 `main.ts:301-338`（`--fork` 不能与 `--session/--continue/--resume/--no-session` 组合；`--session-id` 不能与 `--session/--continue/--resume` 组合）。

### 1.2 session 文件位置与格式

默认目录（`packages/coding-agent/src/config.ts:514-521` + `session-manager.ts:476-489`）：

```ts
// config.ts
515: export function getAgentDir(): string {
516:     const envDir = process.env[ENV_AGENT_DIR];      // PI_CODING_AGENT_DIR
517:     if (envDir) return expandTildePath(envDir);
520:     return join(homedir(), CONFIG_DIR_NAME, "agent"); // ~/.pi/agent
521: }
558: /** Get path to sessions directory */
559: export function getSessionsDir(): string {
560:     return join(getAgentDir(), "sessions");
561: }

// session-manager.ts
476: function getDefaultSessionDirPath(cwd: string, agentDir: string = getDefaultAgentDir()): string {
477:     const resolvedCwd = resolvePath(cwd);
479:     const safePath = `--${resolvedCwd.replace(/^[/\\]/, "").replace(/[/\\:]/g, "-")}--`;
480:     return join(resolvedAgentDir, "sessions", safePath);
481: }
```

文件名与格式（`session-manager.ts`）：

```ts
30: export const CURRENT_SESSION_VERSION = 3;
32: export interface SessionHeader {
33:     type: "session";
34:     version?: number;
35:     id: string;
36:     timestamp: string;
37:     cwd: string;
38:     parentSession?: string;
39: }
...
951:         if (this.persist) {
952:             const fileTimestamp = timestamp.replace(/[:.]/g, "-");
953:             this.sessionFile = join(this.getSessionDir(), `${fileTimestamp}_${this.sessionId}.jsonl`);
954:         }
...
845:  * Manages conversation sessions as append-only trees stored in JSONL files.
```

- 位置：`~/.pi/agent/sessions/--<encoded-cwd>--/<ISO时间戳>_<uuidv7>.jsonl`（可用 `--session-dir` 或 `PI_CODING_AGENT_SESSION_DIR` 覆盖，`config.ts:496`）。
- 格式：**每行一个 JSON 对象（JSONL）**，首行是 `session` header（version/id/timestamp/cwd/parentSession），后面是带 `id`/`parentId`/`timestamp` 的 entry（`message` / `thinking_level_change` / `model_change` / `compaction` / `branch_summary` / `custom` / `custom_message` / `label` / `session_info`，见 `session-manager.ts:46-153`）。append-only 树结构，`buildSessionContext()`（`session-manager.ts:461-470`）从 leaf 沿 parentId 回到根、展开 compaction 摘要得到实际喂给 LLM 的消息。

### 1.3 worker 运行中注入消息

在 `AgentSession` 层有三条注入通道（`packages/coding-agent/src/core/agent-session.ts`）：

```ts
1343: async steer(text: string, images?: ImageContent[]): Promise<void> {   // 中断当前回合，下个 LLM 调用前注入
1379:     this._steeringMessages.push(text); ...
1386:     this.agent.steer({ role: "user", content, timestamp: Date.now() });
...
1363: async followUp(text: string, images?: ImageContent[]): Promise<void> { // agent 无工具调用后才注入
...
1481: async sendUserMessage(content, options?: { deliverAs?: "steer" | "followUp"; ... }): Promise<void>
...
1437: async sendCustomMessage(message, options?: { triggerTurn?: boolean; deliverAs?: "steer" | "followUp" | "nextTurn" }): Promise<void>
```

底层队列语义（`packages/agent/src/agent.ts:282-290` + `agent-loop.ts:259-268`）：

```ts
282:     /** Queue a message to be injected after the current assistant turn finishes. */
283:     steer(message: AgentMessage): void { this.steeringQueue.enqueue(message); }
287:     /** Queue a message to run only after the agent would otherwise stop. */
288:     followUp(message: AgentMessage): void { this.followUpQueue.enqueue(message); }
```

外部进程注入：`--mode rpc` 暴露 `prompt`/`steer`/`follow_up` 命令（`packages/coding-agent/src/modes/rpc/rpc-types.ts:22-25`），处理在 `rpc-mode.ts:394-430`：

```ts
418: 			case "steer": { await session.steer(command.message, command.images); return success(id, "steer"); }
423: 			case "follow_up": { await session.followUp(command.message, command.images); return success(id, "follow_up"); }
```

**机制总结**：`--continue` 打开最近会话、`--resume` 交互选择、`--fork` 以 parentSession 新建分支；session 是 `~/.pi/agent/sessions/<cwd编码>/<时间>_<uuid>.jsonl` 的 JSONL append-only 树；worker 内通过 `session.steer()/followUp()/sendUserMessage()/sendCustomMessage()` 注入纠偏消息，外部进程通过 RPC 的 `steer`/`follow_up`/`prompt` 命令注入。

---

## 2. 工具调用取消（abort/cancel）

### 2.1 AbortSignal 从 CLI/事件层到工具层

Agent 持有当前 run 的 `AbortController`（`packages/agent/src/agent.ts:161-165, 313-321`）：

```ts
161: type ActiveRun = {
162:     promise: Promise<void>;
163:     resolve: () => void;
164:     abortController: AbortController;
165: };
...
313:     /** Active abort signal for the current run, if any. */
314:     get signal(): AbortSignal | undefined { return this.activeRun?.abortController.signal; }
318:     /** Abort the current run, if one is active. */
319:     abort(): void { this.activeRun?.abortController.abort(); }
```

`AgentSession.abort()`（`agent-session.ts:1548-1554`）串起取消链：

```ts
1548: 	async abort(): Promise<void> {
1549: 		this.abortRetry();
1550: 		this.agent.abort();
1551: 		await this.waitForIdle();
1552: 	}
```

signal 传入 agent loop → 工具执行（`packages/agent/src/agent-loop.ts`）：

```ts
679: 		const result = await prepared.tool.execute(
680: 			prepared.toolCall.id,
681: 			prepared.args as never,
682: 			signal,                                   // <-- AbortSignal 直达工具层
683: 			(partialResult) => {...},
684: 		);
```

执行前/执行中多处检查 `signal?.aborted`（`agent-loop.ts:478` 顺序执行 break、`516/535` 并行 break、`629/648` prepareToolCall 时返回 "Operation aborted" 错误结果）：

```ts
478: 		if (signal?.aborted) { break; }
...
648: 		if (signal?.aborted) {
649: 			return { kind: "immediate", result: createErrorToolResult("Operation aborted"), isError: true };
650: 		}
```

工具定义签名里显式带 signal（`packages/coding-agent/src/core/extensions/types.ts:480-486`；`packages/agent/src/types.ts:395-400`）：

```ts
480: 	execute(
481: 		toolCallId: string,
482: 		params: Static<TParams>,
483: 		signal: AbortSignal | undefined,
484: 		onUpdate: AgentToolUpdateCallback<TDetails> | undefined,
485: 		ctx: ExtensionContext,
486: 	): Promise<AgentToolResult<TDetails>>;
```

bash 工具有独立 `AbortController` 集合（`agent-session.ts:338-340, 2780-2806, 2842-2846`）：

```ts
338: 	private readonly _bashAbortControllers = new Set<AbortController>();
...
2780: 		const abortController = new AbortController();
2781: 		this._bashAbortControllers.add(abortController);
...
2842: 	abortBash(): void {
2843: 		for (const abortController of [...this._bashAbortControllers]) abortController.abort();
2844: 	}
```

Abort 时错误消息也通过 agent 状态返回（`agent.ts:511-527`，`stopReason: "aborted"`）。

### 2.2 外部进程触发

RPC 协议提供 `abort`、`abort_retry`、`abort_bash`（`rpc-types.ts:25, 51, 55`），实现（`rpc-mode.ts:428-430` + `551/583`）：

```ts
428: 			case "abort": { await session.abort(); return success(id, "abort"); }
```

扩展内也可取消（`agent-session.ts:2420-2426` bindCore 的 `abort` action → `ExtensionContext.abort()`，见 `types.ts:336`）：

```ts
2420: 				abort: () => {
2421: 					if (this._extensionAbortHandler) { this._extensionAbortHandler(); return; }
2425: 					void this.abort();
2426: 				},
```

工具/信号辅助：`packages/coding-agent/src/utils/abort.ts:14-48`（`raceWithAbortSignal`）。

**机制总结**：`Agent.abort()` 中止当前 run 的 `AbortController`，其 signal 沿 `runAgentLoop → executeToolCalls → tool.execute(signal)` 一路透传，工具在 `execute` 里收到 `AbortSignal|undefined` 并自行监听，loop 在顺序/并行/准备阶段多处检查 `signal.aborted`；外部进程通过 `--mode rpc` 的 `abort`/`abort_bash`/`abort_retry` JSON 命令触发，扩展内用 `ctx.abort()`。

---

## 3. 输出 / 事件流（--mode json）

### 3.1 入口与输出格式

`--mode json` 解析到 `appMode="json"`（`main.ts:122-123`），最终走 `runPrintMode`（`main.ts:962-969`）：

```ts
964: 		const exitCode = await runPrintMode(runtime, {
965: 			mode: toPrintOutputMode(appMode),   // "json"
966: 			messages: parsed.messages,
967: 			initialMessage,
968: 			initialImages,
969: 		});
```

输出（`packages/coding-agent/src/modes/print-mode.ts:106-127`）：先打一行 session header，再逐事件打一行 JSON：

```ts
108: 		unsubscribe = session.subscribe((event) => {
109: 			if (mode === "json") {
110: 				writeRawStdout(`${JSON.stringify(toJsonEvent(event))}\n`);
111: 			}
112: 		});
...
122: 		if (mode === "json") {
123: 			const header = session.sessionManager.getHeader();
125: 				writeRawStdout(`${JSON.stringify(header)}\n`);
127: 		}
```

即：**stdout 第一行是 `{"type":"session",...}` header，之后每行一个事件 JSON**（JSONL）。

### 3.2 事件类型与字段

底层 `AgentEvent`（`packages/agent/src/types.ts:428-443`）：

```ts
428: export type AgentEvent =
430:     | { type: "agent_start" }
431:     | { type: "agent_end"; messages: AgentMessage[] }
433:     | { type: "turn_start" }
434:     | { type: "turn_end"; message: AgentMessage; toolResults: ToolResultMessage[] }
436:     | { type: "message_start"; message: AgentMessage }
438:     | { type: "message_update"; message: AgentMessage; assistantMessageEvent: AssistantMessageEvent }
439:     | { type: "message_end"; message: AgentMessage }
441:     | { type: "tool_execution_start"; toolCallId: string; toolName: string; args: any }
442:     | { type: "tool_execution_update"; toolCallId: string; toolName: string; args: any; partialResult: any }
443:     | { type: "tool_execution_end"; toolCallId: string; toolName: string; result: any; isError: boolean };
```

会话层追加的 `AgentSessionEvent`（`agent-session.ts:141-183`）：`agent_end`（多 `willRetry`）、`agent_settled`、`queue_update`（steering/followUp 数组）、`compaction_start/end`（reason/result/aborted/willRetry）、`entry_appended`、`session_info_changed`、`thinking_level_changed`、`auto_retry_start/end`、`summarization_retry_scheduled/attempt_start/finished`、`bash_execution_update`。

`message_update` 会被 `toJsonEvent` 剥掉累加式 `partial` 快照（`modes/json-event.ts:29-46`），只保留 delta + usage：

```ts
41: 		return { type: "message_update", usage: event.message.usage, assistantMessageEvent };
44: 	const { partial: _partial, ...deltaEvent } = assistantMessageEvent;
45: 	return { type: "message_update", usage: event.message.usage, assistantMessageEvent: deltaEvent };
```

### 3.3 usage / cost 在哪

`Usage` 类型（`packages/ai/src/types.ts:370-391`）：

```ts
370: export interface Usage {
371:     input: number;
372:     output: number;
373:     cacheRead: number;
374:     cacheWrite: number;
376:     cacheWrite1h?: number;
382:     reasoning?: number;
383:     totalTokens: number;
384:     cost: {
385:         input: number; output: number; cacheRead: number; cacheWrite: number; total: number;
390:     };
391: }
```

- 每个 assistant 消息携带 `usage`，`message_update` 事件顶层就是 `usage`（`json-event.ts:41/45`）；`message_end` 的 `message.usage` 是最终值。
- 会话级聚合：`getSessionStats()`（`agent-session.ts:3122-3172`）遍历全部 entry 累加 `tokens{input,output,cacheRead,cacheWrite,total}` 与 `cost`；`getContextUsage()`（`agent-session.ts:3174-3218`）给 `{tokens, contextWindow, percent}`；`getUsageCostBreakdown`（`usage-totals.ts:37-70`）按 provider/model 分桶。
- RPC 提供 `get_session_stats` 命令（`rpc-types.ts:58`），返回上面的 `SessionStats`。

**机制总结**：`--mode json` 在 stdout 先输出 session header 再逐行输出事件 JSON；事件集 = `agent_start/agent_end/turn_start/turn_end/message_start/message_update/message_end/tool_execution_start|update|end` + 会话级 `agent_settled/queue_update/compaction_*/auto_retry_*/thinking_level_changed/...`；僵局检测可用 `turn_start/turn_end` 计数与时间戳、`queue_update` 看积压、`tool_execution_start/end` 看工具是否卡住；usage/cost 在 `message_update` 顶层 `usage`、`message_end.message.usage` 及 `get_session_stats` 聚合里。

---

## 4. 提供方扩展（models.json）

### 4.1 结构与 schema

位置 `~/.pi/agent/models.json`（`config.ts:528-531`）。加载/校验（`packages/coding-agent/src/core/model-config.ts:194-209`）：

```ts
194: const ProviderConfigSchema = Type.Object({
195:     name: Type.Optional(Type.String({ minLength: 1 })),
196:     baseUrl: Type.Optional(Type.String({ minLength: 1 })),
197:     apiKey: Type.Optional(Type.String({ minLength: 1 })),
198:     api: Type.Optional(Type.String({ minLength: 1 })),
199:     oauth: Type.Optional(Type.Literal("radius")),
200:     headers: Type.Optional(Type.Record(Type.String(), Type.String())),
201:     compat: Type.Optional(ProviderCompatSchema),
202:     authHeader: Type.Optional(Type.Boolean()),
203:     models: Type.Optional(Type.Array(ModelDefinitionSchema)),
204:     modelOverrides: Type.Optional(Type.Record(Type.String(), ModelOverrideSchema)),
205: });
207: const ModelsConfigSchema = Type.Object({
208:     providers: Type.Record(Type.String(), ProviderConfigSchema),
209: });
```

模型条目（`model-config.ts:157-171`）：

```ts
157: const ModelDefinitionSchema = Type.Object({
158:     id: Type.String({ minLength: 1 }),
159:     name: Type.Optional(Type.String({ minLength: 1 })),
160:     api: Type.Optional(Type.String({ minLength: 1 })),
161:     baseUrl: Type.Optional(Type.String({ minLength: 1 })),
162:     reasoning: Type.Optional(Type.Boolean()),
163:     thinkingLevelMap: Type.Optional(ThinkingLevelMapSchema),
164:     input: Type.Optional(Type.Array(Type.Union([Type.Literal("text"), Type.Literal("image")]))),
165:     cost: Type.Optional(ModelCostSchema),
166:     contextWindow: Type.Optional(Type.Number()),
167:     maxTokens: Type.Optional(Type.Number()),
168:     samplingParams: Type.Optional(Type.Record(Type.String(), Type.Unknown())),
169:     headers: Type.Optional(Type.Record(Type.String(), Type.String())),
170:     compat: Type.Optional(ProviderCompatSchema),
171: });
```

`api` 的合法取值（`packages/ai/src/types.ts:17-29`）：

```ts
17: export type KnownApi =
18:     | "openai-completions"
19:     | "mistral-conversations"
20:     | "openai-responses"
21:     | "azure-openai-responses"
22:     | "openai-codex-responses"
23:     | "anthropic-messages"
24:     | "bedrock-converse-stream"
25:     | "google-generative-ai"
26:     | "google-vertex"
27:     | "pi-messages";
29: export type Api = KnownApi | (string & {});
```

### 4.2 api key 读取（环境变量占位符）

占位符语法（`packages/coding-agent/src/core/resolve-config-value.ts:145-151` + `28-78`）：

```ts
145: export function resolveConfigValue(config: string, env?: Record<string, string>): string | undefined {
146:     const reference = parseConfigValueReference(config);
147:     if (reference.type === "command") { return executeCommand(reference.config); }
150:     return resolveTemplate(reference.parts, env);
151: }
// parseConfigValueTemplate: $VAR / ${VAR} 取环境变量；$$ 转义字面 $；$! 转义字面 !；
// parseConfigValueReference: 以 "!" 开头 → 当作 shell 命令执行（stdout 裁剪，10s 超时，结果缓存）
```

实际应用（`packages/coding-agent/src/core/provider-composer.ts:351`）：

```ts
351: 				const key = resolveConfigValueOrThrow(rawKey, `API key for provider "${providerId}"`, env);
```

内置 provider 默认环境变量（`args.ts:369-398`）：`ANTHROPIC_API_KEY`、`OPENAI_API_KEY`、`DEEPSEEK_API_KEY`、`GEMINI_API_KEY`、`AZURE_OPENAI_API_KEY` 等。

### 4.3 加 OpenAI 官方 API 与 Anthropic Claude 的最小条目

```jsonc
{
  "providers": {
    "openai": {
      "name": "OpenAI",
      "baseUrl": "https://api.openai.com/v1",
      "apiKey": "$OPENAI_API_KEY",
      "api": "openai-completions",            // 或 "openai-responses"（Chat Completions vs Responses API）
      "models": [
        { "id": "gpt-4o", "name": "GPT-4o", "reasoning": false,
          "input": ["text","image"], "contextWindow": 128000, "maxTokens": 16384,
          "cost": {"input":2.5,"output":10,"cacheRead":1.25,"cacheWrite":2.5} }
      ]
    },
    "anthropic": {
      "name": "Anthropic",
      "baseUrl": "https://api.anthropic.com",
      "apiKey": "$ANTHROPIC_API_KEY",
      "api": "anthropic-messages",
      "models": [
        { "id": "claude-sonnet-4-20250514", "name": "Claude 4 Sonnet", "reasoning": true,
          "input": ["text","image"], "contextWindow": 200000, "maxTokens": 16384,
          "cost": {"input":3,"output":15,"cacheRead":0.3,"cacheWrite":3.75} }
      ]
    }
  }
}
```

`cost` 单位为 美元/百万 token（`types.ts:776-791`），`input` 为 `["text","image"]`。`compat` 可按需给 OpenAI-compatible 端点设 `thinkingFormat`（如 deepseek/openrouter）等（`types.ts:545-605`）。

动态目录另有 `~/.pi/agent/models-store.json`（`models-store.ts:46-59`，供 `refreshModels` 持久化）。

**机制总结**：`models.json` 顶层 `{providers:{<id>:{name,baseUrl,apiKey,api,headers,compat,authHeader,models[],modelOverrides{}}}}`，`api` 取 `openai-completions`/`openai-responses`/`anthropic-messages` 等；apiKey 支持 `$ENV`/`${ENV}` 占位与 `!command` 命令；OpenAI 官方 API 用 `api:"openai-completions"`+`$OPENAI_API_KEY`，Claude 用 `api:"anthropic-messages"`+`$ANTHROPIC_API_KEY`。

---

## 5. 思维等级（--thinking）

### 5.1 合法值与类型

`--thinking` 合法值（`args.ts:60, 133-142`）：

```ts
60: const VALID_THINKING_LEVELS = ["off", "minimal", "low", "medium", "high", "xhigh", "max"] as const;
...
133: } else if (arg === "--thinking" && i + 1 < args.length) {
134:     const level = args[++i];
135:     if (isValidThinkingLevel(level)) { result.thinking = level; }
```

`ThinkingLevel`（`packages/agent/src/types.ts:300`）：

```ts
300: export type ThinkingLevel = "off" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max";
```

传递：`thinkingLevel` 在 `Agent.createLoopConfig` 里映射成 loop 的 `reasoning`（`packages/agent/src/agent.ts:450`）：

```ts
450: 	reasoning: this._state.thinkingLevel === "off" ? undefined : this._state.thinkingLevel,
```

### 5.2 各 provider 推理力度映射

通用 clamp：`xhigh/max` 先被折叠到 `high`（`packages/ai/src/api/simple-options.ts:57-59`）：

```ts
57: export function clampReasoning(effort: ThinkingLevel | undefined): Exclude<ThinkingLevel, "xhigh" | "max"> | undefined {
58:     return effort === "xhigh" || effort === "max" ? "high" : effort;
59: }
```

**Anthropic**（`packages/ai/src/api/anthropic-messages.ts:796-814`，adaptive thinking 走 `effort`，旧模型走 token budget）：

```ts
796: function mapThinkingLevelToEffort(model, level): AnthropicEffort {
800:     const mapped = level ? model.thinkingLevelMap?.[level] : undefined;
801:     if (typeof mapped === "string") return mapped as AnthropicEffort;
803:     switch (level) {
804:         case "minimal": case "low": return "low";
807:         case "medium": return "medium";
809:         case "high": default: return "high";
813:     }
814: }
...
1051:             if (model.compat?.forceAdaptiveThinking === true) {
1053:                 params.thinking = { type: "adaptive", display };
1054:                 if (options.effort) { params.output_config = { effort: options.effort }; }
1057:             } else {
1065:                 params.thinking = { type: "enabled", budget_tokens: options.thinkingBudgetTokens || 1024, display };
```

**OpenAI（completions / responses）**：`reasoning_effort` 直接透传，`xhigh/max`→`high`（`openai-completions.ts:625-626, 838-845`；`openai-responses.ts:320-331`）：

```ts
// openai-completions.ts
625: 	const clampedReasoning = options?.reasoning ? clampThinkingLevel(model, options.reasoning) : undefined;
626: 	const reasoningEffort = clampedReasoning === "off" ? undefined : clampedReasoning;
...
838: 	} else if (options?.reasoningEffort && model.reasoning && compat.supportsReasoningEffort) {
840: 		(params as any).reasoning_effort = model.thinkingLevelMap?.[options.reasoningEffort] ?? options.reasoningEffort;
```

**DeepSeek**（`compat.thinkingFormat === "deepseek"`，`openai-completions.ts:797-806`）：

```ts
797: 	} else if (compat.thinkingFormat === "deepseek" && model.reasoning) {
798: 		if (options?.reasoningEffort) { (params as any).thinking = { type: "enabled" }; }
800: 		else if (model.thinkingLevelMap?.off !== null) { (params as any).thinking = { type: "disabled" }; }
803: 		if (options?.reasoningEffort && compat.supportsReasoningEffort) {
804: 			(params as any).reasoning_effort =
805: 				model.thinkingLevelMap?.[options.reasoningEffort] ?? options.reasoningEffort;
```

其他 thinkingFormat（openrouter→`reasoning:{effort}`、together→`reasoning:{enabled}`+`reasoning_effort`、qwen→`enable_thinking`、baseten/chat-template 等）见 `openai-completions.ts:749-846` 与 `types.ts:566-578` 的 `thinkingFormat` 文档。

### 5.3 每模型覆盖

`model.thinkingLevelMap`（`packages/ai/src/types.ts:801-805`；JSON 里 `model-config.ts:55-63`）允许按 provider/model 覆盖每个等级的具体值，`null` 表示该等级不支持：

```ts
801: 	/**
802: 	 * Maps pi thinking levels to provider/model-specific values.
803: 	 * Missing keys use provider defaults. null marks a level as unsupported.
804: 	 */
805: 	thinkingLevelMap?: ThinkingLevelMap;
```

预算式 provider 的 token 预算（`simple-options.ts:68-73`）：`minimal:1024 / low:2048 / medium:8192 / high:16384`。

**机制总结**：`--thinking` 接受 `off/minimal/low/medium/high/xhigh/max`；pi 把 `off` 转为不传 reasoning，`xhigh/max` 一律折叠到 `high`；DeepSeek 走 `thinking:{type:enabled/disabled}` + `reasoning_effort`，OpenAI 走顶层 `reasoning_effort`，Anthropic adaptive 模型走 `output_config.effort`（low/medium/high）、旧模型走 `thinking.budget_tokens`；`thinkingLevelMap` 可对单个模型覆写具体值。

---

## 6. 扩展机制（-e / kali.ts）

### 6.1 加载与可注册项

`-e` 参数（`args.ts:152-154`）；用 jiti 直接加载 TypeScript 扩展模块（`packages/coding-agent/src/core/extensions/loader.ts:444-455`）：

```ts
444: 	const jiti = createJiti(import.meta.url, { moduleCache: false, ... });
455: 	const module = await jiti.import(extensionPath, { default: true });
```

扩展工厂签名与 API 全貌（`types.ts:1519, 1198-1437`）：

```ts
1519: export type ExtensionFactory = (pi: ExtensionAPI) => void | Promise<void>;
1198: export interface ExtensionAPI {
1203:     on(event, handler): void;            // ~30 种生命周期事件
1251:     registerTool(tool): void;            // LLM 可调用工具（TypeBox schema + execute(signal,onUpdate,ctx)）
1260:     registerCommand(name, options): void;
1263:     registerShortcut(shortcut, options): void;
1272:     registerFlag(name, options): void;   // 注册自定义 CLI flag
1282:     getFlag(name): boolean|string|undefined;
1302:     sendMessage(message, options): void; // 注入自定义消息
1312:     sendUserMessage(content, options): void;
1318:     appendEntry(customType, data): void;
1334:     exec(command, args, options): Promise<ExecResult>;
1353:     setModel / getThinkingLevel / setThinkingLevel;
1417:     registerProvider / unregisterProvider;
1436:     events: EventBus;                     // 扩展间事件总线
1437: }
```

`createExtensionAPI` 实现（`loader.ts:249-426`）：`on()` 写入 `extension.handlers`（257-262），`registerTool/registerCommand/registerShortcut/registerFlag` 写入 `extension.tools/commands/shortcuts/flags`（264-302），`getFlag` 从共享 runtime 读（321-325），`sendMessage/sendUserMessage` 委托共享 runtime（328-336）。

### 6.2 扩展能否注入消息 / 监听事件（LoopDetector / 监督）

**能**。三点证据：

1) `pi.on(...)` 订阅全部生命周期事件（`types.ts:1203-1244`），事件在 `agent-session.ts:_emitExtensionEvent` 中逐点派发（`agent-session.ts:727-808`，含 `turn_start/turn_end/message_start/message_update/message_end/tool_execution_start/update/end/agent_start/agent_end`）。

2) `pi.sendMessage/sendUserMessage` 注入（`loader.ts:328-336` → `agent-session.ts:2367-2384` bindCore → `sendCustomMessage`/`sendUserMessage`，后者实现见 `agent-session.ts:1437-1511`）：

```ts
2367: 			sendMessage: (message, options) => {
2368: 				this.sendCustomMessage(message, options).catch(...)
2376: 			sendUserMessage: (content, options) => {
2377: 				this.sendUserMessage(content, options).catch(...)
```

3) 关键拦截/改写点：`before_agent_start` 可注入 custom message + 替换 system prompt（`types.ts:1102-1106`；`agent-session.ts:1233-1261`）、`input` 可 transform/handle 用户输入（`types.ts:844-847`；`agent-session.ts:1142-1157`）、`message_end` 可替换最终消息（`types.ts:1097-1100`；`agent-session.ts:767-780`）、`tool_call` 可 `block` 工具 + `terminate`（`types.ts:1071-1080`；`agent-session.ts:480-499`）、`context` 可在请求前改写 messages（`types.ts:670-673`；`sdk.ts:353-357`）。

因此 LoopDetector/监督模块可以写成一个 `-e` 扩展：`pi.on("turn_start"/"turn_end"/"tool_execution_start"/"message_end", ...)` 做检测，`pi.sendMessage`/`sendUserMessage`（`deliverAs:"steer"|"followUp"`）注入纠偏，`pi.registerFlag` 暴露开关，`ctx.abort()` 终止。

**机制总结**：`-e kali.ts` 加载一个 `(pi)=>void` 工厂，可注册工具、命令、快捷键、CLI flag、provider，并用 `pi.on(...)` 监听全部 agent/session 生命周期事件、用 `pi.sendMessage/sendUserMessage`（steer/followUp）注入消息、用 `pi.appendEntry` 持久化状态、用 `ctx.abort()` 取消；LoopDetector/监督完全可作为扩展实现。

---

## 7. 双阶段 conclude 可行性

### 7.1 有没有"时间到了强制总结收尾"

**CLI / AgentSession 没有内置 max-turns 或全局超时强制总结的开关**（对 `maxTurns`/`max_turns` 全仓 grep 无结果；唯一的 `timeoutMs` 是 provider HTTP 请求超时，见 `packages/ai/src/types.ts` 与 `settings` 的 `httpIdleTimeoutMs`）。

但有三层可用的机制：

1) **graceful stop hook**（`packages/agent/src/agent-loop.ts:247-257` + `agent/types.ts:212-222`）：

```ts
247: 			if (
248: 				await config.shouldStopAfterTurn?.({ message, toolResults, context: currentContext, newMessages })
254: 			) {
255: 				await emit({ type: "agent_end", messages: newMessages });
256: 				return;
257: 			}
```
```ts
// types.ts:212-222
/** Called after each turn fully completes ... If it returns true, the loop emits agent_end
 *  and exits before polling steering/follow-up queues, without starting another LLM call. */
shouldStopAfterTurn?: (context: ShouldStopAfterTurnContext) => boolean | Promise<boolean>;
```
（注意：这是 `Agent` 构造器选项，`createAgentSession` 目前没把它接到 settings 上；嵌入方自己 new `Agent` 时可传。）

2) **立即中止 + 注入总结指令**：`session.abort()`（`agent-session.ts:1548-1554`）→ `session.sendUserMessage("时间到，请总结", { deliverAs: "followUp" })`（`agent-session.ts:1481-1511`，`followUp` 语义见 `agent.ts:287-290` + `agent-loop.ts:262-268`，只在 agent 无工具调用时才注入）。

3) **compaction 收尾**：`session.compact(customInstructions)`（`agent-session.ts:1790-1933`）会先 abort、再用 LLM 生成摘要写入 `compaction` entry 并重建上下文（`compaction.ts`）；RPC 有 `compact` 命令（`rpc-types.ts:46`）。

工具结果还可带 `terminate:true` 提前结束当前批次（`agent-loop.ts:582-584`）。

### 7.2 怎么触发"让 agent 在当前会话里最后总结一次成果"

推荐两条路径：

- **嵌入（SDK）方式**：`createAgentSession`（`sdk.ts:171-401`）持有 `AgentSession`，编排器起一个计时器，到点先 `await session.abort()`，再 `await session.sendUserMessage("请用一段话总结本次成果与未完成项", { deliverAs: "followUp" })` 或 `session.steer(...)`，然后订阅 `agent_settled`（`agent-session.ts:596-604`）等收尾。
- **RPC 方式**：外部进程依次发送 `{"type":"abort"}` → `{"type":"prompt","message":"请总结","streamingBehavior":"follow_up"}`（或 `steer`）→ 可选 `{"type":"compact","customInstructions":"..."}`（`rpc-types.ts:22-25, 46`）。

**机制总结**：无内置 max-turns/超时强制总结；双阶段收尾 = 阶段1 用 `abort()` 或 `shouldStopAfterTurn` 停住当前 turn，阶段2 用 `sendUserMessage(..., {deliverAs:"followUp"})`/`steer()` 注入"最后总结一次"的指令，或用 `compact(customInstructions)` 生成收尾摘要；RPC 的 `abort`+`prompt/steer/follow_up`+`compact` 命令可让外部编排器完整驱动该流程。

---

## 附：关键结论速览（对 worker 后端的直接含义）

1. **注入/续跑**：`--mode json` 是一次性单发模式；要"运行中注入纠偏 + 取消"，应改用 `--mode rpc`（JSON-lines stdin/stdout 双向）或直接用 SDK `createAgentSession` 内嵌持有 `AgentSession`（`steer/followUp/sendUserMessage/abort/compact`）。
2. **取消**：AbortSignal 全链路透传到 `tool.execute(signal)`，外部可经 RPC `abort` 触发，扩展经 `ctx.abort()`。
3. **事件流**：`--mode json` = header 行 + 每事件一行 JSONL；僵局检测靠 `turn_start/turn_end/tool_execution_*` + `queue_update`，计量靠 `message_update.usage`/`message_end.message.usage`/`get_session_stats`。
4. **提供方**：改 `~/.pi/agent/models.json`（`providers.<id>.{baseUrl,apiKey,api,models[]}`），apiKey 用 `$ENV` 占位。
5. **思维**：`off/minimal/low/medium/high/xhigh/max`，`xhigh/max`→`high`；deepseek→`thinking.type`+`reasoning_effort`，openai→`reasoning_effort`，anthropic→`output_config.effort`。
6. **扩展**：`-e` 扩展可注册工具/flags/命令/provider，并监听事件 + 注入消息（LoopDetector/监督可直接写成扩展）。
7. **conclude**：无内置 max-turns；用 `abort()`（或低层 `shouldStopAfterTurn`）+ `followUp` 注入"总结"指令实现双阶段收尾。
