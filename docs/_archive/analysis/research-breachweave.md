# BreachWeave 源码研究报告

> 目标仓库：`D:\wangan\yingyong\CTF\第二届openharmony\BreachWeave-main`
> 对象：腾讯云黑客松第二期 1/613 冠军项目（TypeScript/Bun monorepo，Manager/Solver/Observer 三角色，基于 `@mariozechner/pi-coding-agent` SDK）
> 说明：所有行号以仓库内 `packages/core/src` 下的源码为准。核心证据集中在 `challenge/`、`solver/`、`runtime/`、`config/` 四个目录。

---

## 1. Observer（监督者）完整机制

### 1.1 它读什么数据

Observer 是 **Solver 进程内的 pi 扩展**（`challengeObserverExtension` → `attachObserverLoop`），它不读文件、也不读比赛平台，而是订阅 pi SDK 的会话事件流，把「最近几轮」轨迹压缩成结构化记录：

- 订阅事件：`tool_execution_start` / `tool_execution_end` / `message_end` / `agent_end`（`solver/extension/challenge-observer/observer-loop.ts:293,307,334,368`）。
- 每轮记录 `ObserverRoundPayload { round, assistant_summary, tool_logs[] }`，其中工具日志只留 `tool_name / args_summary(160字) / result_summary(160字) / is_error`（`types.ts:3-14`）。
- 落盘到 `<solverSessionDir>/.observer/rounds/NNNNNN.json`（`observer-store.ts:36-37,109-113`）。
- 会话背景压缩：`ctx.sessionManager.getEntries()` + `buildSessionContext()` → `buildCompactSessionContext()` 只保留 baseline 用户指令 + 最近 4 条用户补充（`observer-loop.ts:218-231,189-216`）。

关键片段：

```ts
// observer-loop.ts:338-359
const assistantSummary = "content" in event.message ? extractAssistantSummary(...) : ""
const { roundRecord, reviewReason } = await updateObserverState((state) => {
    const nextRound = state.round + 1
    const roundRecord: ObserverRoundPayload = { round: nextRound, assistant_summary: assistantSummary, tool_logs: state.current_round_tool_logs }
    const periodicDue = nextRound % OBSERVER_REVIEW_EVERY_ROUNDS === 0
    const reviewReason = state.force_review_reason ?? (periodicDue ? "periodic" : undefined)
    ...
})
```

### 1.2 多久触发一次

三个触发条件（`observer-loop.ts:9-13,327,346-372`）：

- **periodic**：每 `OBSERVER_REVIEW_EVERY_ROUNDS = 6` 个 assistant 轮次触发一次；
- **hint**：`challenge_get_hint` 工具成功执行时强制触发（`observer-loop.ts:327`：`force_review_reason = !event.isError && event.toolName === "challenge_get_hint" ? "hint" : ...`）；
- **agent_end**：Solver 回合结束时触发一次收尾审查。

每次审查只回看最近 `OBSERVER_REVIEW_WINDOW_ROUNDS = 10` 轮（`observer-loop.ts:359-365`）。

```ts
// observer-loop.ts:9-11
const OBSERVER_REVIEW_EVERY_ROUNDS = 6
const OBSERVER_REVIEW_WINDOW_ROUNDS = 10
const OBSERVER_REMINDER_COOLDOWN_ROUNDS = 6
```

### 1.3 用什么模型

Observer 是一个**独立的 Agent 会话**，模型可单独指定：`prompt.meta.observerModel`，未指定则回退到 Solver 的 `prompt.meta.model`（`session.ts:127-129`），再由 `config.resolveModelPref()` 解析成 `model` + `thinkingLevel`（`observer-agent.ts:294-299`）。会话目录独立为 `sessionDir/.observer`（`observer-agent.ts:259-263`），与主 Solver 会话物理隔离。

```ts
// observer-agent.ts:282-292
const opts: CreateAgentSessionOptions = {
    tools: [],
    customTools: createObserverSidecarToolsWithOptions({...}),
    resourceLoader,
    authStorage: config.auth,
    modelRegistry: config.models,
    settingsManager: config.settings,
}
```

### 1.4 输出什么契约

**没有强 JSON Schema**——契约是「系统提示词 + TypeBox 工具参数」的组合：

- 输出契约写在系统提示里（`observer-agent.ts:166-179`）：无改动只回 `NO_CHANGE`；有改动只输出 1-4 条短 bullet；禁止复述题面/日志。
- 实际「动作」通过工具落地：`memory_list/memory_add/memory_update/memory_delete`、`idea_list/idea_search/idea_add/idea_update`、`query_solver_history`、`send_efficiency_reminder`（`tools.ts:217-407`）。
- `types.ts:40-44` 的 `ObserverReviewResult { new_ideas, idea_updates, memory_actions }` 只是类型定义，运行时不强制要求模型输出该 JSON。

```ts
// observer-agent.ts:166-170
## Output Contract
- 最终回复不能复述题面、上下文、日志或做题过程。
- 如果本轮无需修改，只回复 `NO_CHANGE`。
- 如果有修改，只输出 1-4 条短 bullet，说明你维护了什么。
```

### 1.5 判定哪些异常、各自怎么判定

四类问题在 Observer 系统提示里对应不同的处理策略（`observer-agent.ts:11-179`）：

| 异常 | 判定/处理方式 | 证据位置 |
|------|--------------|----------|
| **路径偏移（执行路径逐渐偏移）** | 维护 `ideas` 看板（"接下来值得测试什么"），闭环已有主线，新证据只在"真正打开不同攻击方向"时才新增 idea；默认 `NO_CHANGE`，顺序是「闭环→收缩→扩张」 | `observer-agent.ts:30-42,50-57` |
| **状态混杂（状态累积后混杂）** | `idea`（方向）与 `memory`（事实/证据/失败边界/hint/约束）分层；memory 默认"合并重于累加"，failure 整理成边界结论而非动作流水 | `observer-agent.ts:44-46,83-94` |
| **过早结束** | 严格说由**别处**兜底：`ralph-loop.ts` 的续跑机制 + Planner 的 stale 阻塞（见 §5）；Observer 只负责"持续监督"不负责判结束 | `ralph-loop.ts:58-87` |
| **上下文过重** | 体积硬约束：memory 默认 ≤12 条、ideas ≤8 条，超限时"压缩本身是优先动作"；`send_efficiency_reminder` 针对持续低效（4 个前提同时满足，且带冷却/指纹去重） | `observer-agent.ts:96-106,119-145`；`observer-loop.ts:72-106` |

「低效」提醒的判定与去重（防刷屏）是关键细节：

```ts
// observer-loop.ts:72-106
const withinCooldown = roundsSinceLast < OBSERVER_REMINDER_COOLDOWN_ROUNDS
const repeatedPattern = roundsSinceLast < OBSERVER_REMINDER_REPEAT_WINDOW_ROUNDS && (sameMessage || sameActivity)
const allowed = !withinCooldown && !repeatedPattern
```

### 1.6 纠偏怎么注入回 Solver

调用 pi SDK 的 **`pi.sendUserMessage(..., { deliverAs: "steer" })`**，即以「steer（转向）消息」注入，而非普通 followUp：

```ts
// observer-loop.ts:266-274
await runSolverObserverReview(challengeIdText, next, {
    observerModel: options.observerModel,
    sendCorrectionNotice: async (message) => {
        if (!(await shouldSendEfficiencyReminder(next, message))) return false
        pi.sendUserMessage(`纠偏提醒：${message.trim()}`, { deliverAs: "steer" })
        return true
    },
})
```

工具层的 `send_efficiency_reminder` 也走同一个回调（`tools.ts:384-406`）。`deliverAs: "steer"` 对应 SDK 的转向注入（`scope-guard.ts:154,192,238` 同样用 `steer`/`nextTurn` 区分强提醒与普通下回合消息）。

### 1.7 会不会终止 Solver

**不会。** Observer 只做两件事：改看板（memory/idea 工具）和发 steer 提醒。终止 Solver 的权力在别处：

- `ChallengeManager.finishChallenge()`：题目完成后 `runtime.stopSolver()` 停掉该题所有 Solver（`manager.ts:1280-1305`）；
- Planner 工具 `planner_stop_solver`（且仅当 stale 时才允许，`manager.ts:1474-1496`）；
- `RuntimeManager.stopSolver()` → `docker stop`（`runtime.ts:599-617`）。

一句话机制总结：**Observer 是 Solver 进程内、订阅 pi 事件流的旁路副驾驶，每 6 轮（或 hint/agent_end）用一个独立会话把最近 10 轮压缩轨迹喂给模型，只通过 memory/idea 看板工具 + `steer` 消息做轻量纠偏，从不直接终止 Solver。**

---

## 2. Manager 调度

### 2.1 怎么决定先做哪个 challenge/方向（triage 排序）

调度主体不是确定性算法，而是一个 **LLM Planner**（`CHALLENGE_PLANNER` prompt + 调度工具集），但系统先给它做了确定性预排序和硬约束：

- 快照排序：`untouched`（从未尝试）优先 → `difficulty` 升序 → `remainingScore` 降序（`manager.ts:1568-1572`）。

```ts
// manager.ts:1568-1572
challenges: challengeItems.sort((a, b) => {
    if (a.untouched !== b.untouched) return a.untouched ? -1 : 1
    if (a.difficulty !== b.difficulty) return a.difficulty.localeCompare(b.difficulty)
    return b.remainingScore - a.remainingScore
}),
```

- Planner 提示词规定「调度顺序」与「稳定性原则」：稳定优先于频繁变更、增量补充优先于替换、只有 stale 或资源紧张才允许释放（`config/prompts/builtin/CHALLENGE_PLANNER.md:25-51`）。
- 每轮快照含 `Constraints`（maxActiveChallenges / maxSolvers / idle slots）、每题的 `untouched/stale/attemptCount/submissionCount/correctSubmissionCount/activeSolverIds`、每个 prompt 的历史战绩（`formatPromptPerformance` = solved/total_flags，`manager.ts:366-369`）。

### 2.2 怎么给题目分配和回收 Solver

Planner 通过 4 个工具执行调度（`manager.ts:1383-1498`）：

- `planner_start_challenge`（占用 1/3 实例位）
- `planner_launch_solver`（必填 `solverHandoff` ≤1200 字，`manager.ts:1449-1457`）→ 内部 `launchSolver()` → `runtime.launch()` 起 Docker 容器
- `planner_stop_challenge` / `planner_stop_solver`：**回收有硬门槛**——题目 `stale !== true` 时直接抛错阻止回收：

```ts
// manager.ts:1432-1435
const challenge = snapshotChallenges.get(params.challengeId)
if (challenge && !challenge.stale) {
    throw new Error(`challenge "${params.challengeId}" is not stale yet; stop is blocked before stale timeout`)
}
```

`launchSolver()` 会复用已 running/pending 的题目实例、避免重复 start，并拒绝已完成题目（`manager.ts:1231-1252`）。

### 2.3 全局并发怎么限

硬约束常量 + 配置钳制（`manager.ts:54-58,1504-1506`）：

```ts
const DEFAULT_MAX_SOLVERS = 7
const MAX_ACTIVE_CHALLENGES = 3          // 最多同时 3 个题目实例
const DEFAULT_STALE_TIMEOUT_MS = 60 * 60 * 1000  // 1h
```

- `maxSolvers = clamp(settings.runtime.maxSolvers ?? 7, 0, 64)`；
- `staleTimeoutMs = clamp(settings.planner.staleTimeoutMs ?? 1h, 5min, 24h)`；
- 题目实例数 `MAX_ACTIVE_CHALLENGES=3` 硬编码进快照 `constraints.maxActiveChallenges` 与工具描述（"occupy one of the 3 challenge-instance slots"）。

一句话机制总结：**Manager 的调度是一个每 30s 一轮的 LLM Planner：系统先按「未动过→难度→剩余分」预排序并注入 stale/并发硬约束，Planner 再通过 start/launch/stop 四个工具在 3 个实例位、最多 7 个 Solver 的预算内分配与回收（回收必须 stale）。**

---

## 3. Solver 生命周期

### 3.1 多 Solver 并行的隔离

**每个 Solver 是一个独立 Docker 容器**，进程、工作目录、会话全部隔离（`runtime.ts:485-563`）：

```ts
// runtime.ts:506-521
const baseDir = solverDir(id)          // ~/.tch-agent/solvers/<id>
const sessionDir = solverSessionDir(id)// .../<id>/session
const workspaceDir = solverWorkspaceDir(id) // .../<id>/workspace
...
const binds = [ ...(this.config.binds ?? []), `${baseDir}:${containerRuntimeDir}`, `${workspaceDir}:${containerWorkspaceDir}` ]
```

- 容器名 `tch-solver-<id>`，`--network host`，`--rm`，以编译后的二进制 `tch-agent solver rpc` 启动（`resolveSolverInjection` → `helpers.ts:163-182`）。
- 会话/工作目录通过环境变量 `TCH_SOLVER_SESSION_DIR` / `TCH_SOLVER_WORKSPACE` / `TCH_SOLVER_BASE_DIR` 注入（`runtime.ts:527-535`）。
- 会话存储 `SessionManager.create(workspaceDir, sessionDir)`（`session.ts:148-152`）；每个 Solver 自己的看板在 `sessionDir/.observer`（`board-store.ts:24-26`）。
- 多 Solver 挂同一题时，共享 **challenge 级** memory/ideas（启动时 `seedSolverBoardFromChallenge` 复制一份到各自 board，`manager.ts:1198-1207`），但各自容器/工作区/会话互不干扰。

### 3.2 Solver 结束条件（谁判定）

结束不由模型主观决定，由多个系统信号共同判定（详见 §5）：

1. **题目完成**：`computeChallengeCompleted()`（`flag_count>0 && flag_got_count>=flag_count`）→ `finishChallenge` 停实例 + 停该题所有 Solver（`manager.ts:1280-1305`）。
2. **续跑循环**：`agent_end` 触发 `attachChallengeContinuation`，只要题目未完成就注入续跑消息 `triggerTurn: true` 强制再来一轮（`ralph-loop.ts:58-87`）；连续 error 最多重试 `MAX_CHALLENGE_RETRY_ATTEMPTS=10` 次、指数退避（`ralph-loop.ts:6,45-47`）。
3. **错误/退出**：进程退出或 `stopReason==="error"` → `RuntimeManager` 标记 `error`/`stopped`（`runtime.ts:850-853`；`getAgentEndError`）。
4. **人工/Planner 回收**：`planner_stop_solver`（需 stale）或 `runtime.stopSolver`（docker stop）。

一句话机制总结：**Solver 之间用「一个 Solver 一个 Docker 容器 + 独立 session/workspace 目录 + 独立 board 副本」隔离；结束由系统信号（flag 计数、agent_end 续跑、error、stale 回收）判定，不交给模型。**

---

## 4. Idea 与 Memory

### 4.1 完整定义

定义在 `challenge/memory.ts:4-34`：

```ts
export type IdeaStatus = "pending" | "testing" | "verified" | "failed" | "skipped"
export type MemoryKind = "fact" | "evidence" | "failure" | "note" | "hint"

export interface MemoryEntry {
    id: string
    challengeId: string
    kind: MemoryKind
    content: string
    refs: string[]
    source: string
    created_at: string
    updated_at: string
}

export interface IdeaRecord {
    id: string
    content: string
    normalized: string
    status: IdeaStatus
    result: string
    created_at: string
    updated_at: string
}
```

语义区分（`observer-agent.ts:50,84-85`）：**Idea = "接下来值得测试什么"（方向假设，不是事实）**；**Memory = "压缩后仍必须留下的 durable facts/evidence/failure boundaries/hints/constraints"（事实与边界）**。

### 4.2 谁写谁读

| 角色 | 读写 | 入口 |
|------|------|------|
| **Observer** | 读写（唯一写 ideas 的角色） | 工具 `memory_*` / `idea_*`（`tools.ts:217-347`） |
| **Solver** | 只读 | `challengeObserverAgentTools` 只提供 `memory_list/idea_list/idea_search`（`tools.ts:410-471`）；系统提示硬约束"主 Agent 对 ideas 是只读的"（`observer-agent.ts:157`） |
| **Manager/Planner** | 读写 challenge 级 | `manager.ts:1121-1176`（`appendMemory/addIdea/updateIdea/...`），落盘到 challenge 目录 |

### 4.3 怎么更新/过期/去重

- **Idea 去重**：`normalized = content.trim().toLowerCase()`，新增时撞库直接返回已有项；更新时撞库抛错（`memory.ts:295-324,347-356`）。
- **并发安全**：文件系统目录锁（`withDirectoryLock`，5s 超时 / 60s stale 自动摘锁，`memory.ts:127-160`）+ 原子写（tmp+rename）。
- **Memory 去重/过期**：底层是 append-only 文件、无自动 TTL；收缩交给 Observer 语义——"合并重于累加"、"被更强结论覆盖的记录应 update 或 delete"、"failure 整理成边界结论"（`observer-agent.ts:87-94`）。体积硬上限 memory≤12、ideas≤8（`observer-agent.ts:100`）。
- 注意：**没有真正的过期机制**，只有 challenge 级别的 `stale` 时间戳标记（§2.3）。

### 4.4 怎么进 prompt

两条路径：

1. **Solver 初始任务**：`buildSolverTask()` 把 memory/ideas/submissions 拼成压缩表格，只带最近 `SOLVER_MEMORY_LIMIT=10` 条 memory、`SOLVER_IDEA_LIMIT=8` 条 idea，内容截断到 220/120 字（`manager.ts:62-63,286-319,1629-1660`）。
2. **Solver 看板种子**：`seedSolverBoardSnapshot()` 把 challenge 级 memory/ideas 复制进 Solver 自己的 board（`manager.ts:1198-1207` + `board-store.ts:75-107`），Solver 运行中用 `memory_list/idea_list` 按需回看。

```ts
// manager.ts:62-66
const SOLVER_MEMORY_LIMIT = 10
const SOLVER_IDEA_LIMIT = 8
const SOLVER_HANDOFF_MAX_CHARS = 900
const SOLVER_MEMORY_CONTENT_MAX_CHARS = 220
const SOLVER_IDEA_CONTENT_MAX_CHARS = 120
```

一句话机制总结：**Idea（方向假设，带 pending/testing/verified/failed/skipped 生命周期 + lowercase 去重）与 Memory（事实/证据/失败边界/hint/约束，kind 区分）是分层状态；Observer 写、Solver 只读、Manager 落盘 challenge 级，靠目录锁并发、靠体积上限 + 摘要截断进入 prompt。**

---

## 5. 结束条件外置

核心思想：**「任务是否结束」由系统信号判定，不由模型主观判断**。具体实现：

### 5.1 判完成的唯一标准

```ts
// store.ts:263-266
export function computeChallengeCompleted(challenge: ChallengeInfoRecord | undefined): boolean {
    if (!challenge) return false
    return challenge.flag_count > 0 && challenge.flag_got_count >= challenge.flag_count
}
```

该标准贯穿：`submitFlag` 完成后触发 `finishChallenge`（`manager.ts:1074-1078`）、`listChallenges` 发现已完成即 `finishChallenge`（`manager.ts:931-933`）、启动 Solver 前拦截已完成题目（`manager.ts:1231-1233`）。

### 5.2 host-bridge 把「完成状态」暴露给 Solver 进程

Solver 侧的续跑/提醒都以 `challenge_is_completed` 为准，而非模型自己的判断：

```ts
// ralph-loop.ts:49-56
async function isChallengeCompletedByHostBridge(): Promise<boolean> {
    const result = await requestHostBridge<{ is_completed: boolean }>("challenge_is_completed", {})
    return result.is_completed === true
}
// ralph-loop.ts:61-64
pi.on("agent_end", async (event) => {
    if (await isChallengeCompletedByHostBridge()) return
    ...
```

`challenge_is_completed` 动作在 `host-bridge-handler.ts:241-245` 实现，映射到 `challengeManager.isChallengeCompleted()`。

### 5.3 续跑机制对抗「过早结束」

Solver 一旦 `agent_end`，只要题目没完成，就强制注入续跑消息触发新一轮：

```ts
// ralph-loop.ts:7-8,75-85
const CHALLENGE_CONTINUATION_MESSAGE =
    "继续当前任务。不要重复已经完成的步骤，基于现有上下文继续推进；如果题目有多个 flag，不要因为提交对一个就停止，直到比赛 API 明确显示题目完成。"
...
pi.sendMessage({ customType: CHALLENGE_CUSTOM_MESSAGE_TYPE, content: [{ type: "text", text: CHALLENGE_CONTINUATION_MESSAGE }], display: false }, { triggerTurn: true })
```

### 5.4 回收门槛对抗「过早释放」

`planner_stop_challenge` / `planner_stop_solver` 在题目 `stale !== true` 时直接拒绝（`manager.ts:1432-1435,1486-1488`）；`stale` 定义为「最老活跃 Solver 超过 staleTimeout 且无任何正确提交」（`manager.ts:1613`）。

### 5.5 平台 API 信号

题目是否完成的权威来源是比赛平台的 flag 计数（`api-client.ts:29-34` 的 `ChallengeApiSubmitData { correct, message, flag_count, flag_got_count }`），`submitFlag` 用 `remote.flag_got_count/flag_count` 更新并判定。

一句话机制总结：**结束判定外置为「`flag_count>0 && flag_got_count>=flag_count`」这一纯系统信号（经 host-bridge 暴露给进程、经续跑循环阻止模型早停、经 stale 门槛阻止提前回收），模型只能推进不能宣布结束。**

---

## 6. 上下文压缩与降噪

### 6.1 pi 的 compaction 接入（当前未启用）

RPC 层暴露了 SDK 的 compaction 命令，且项目自己写了一个「必须保留」指令集，但**目前在 Solver 会话中处于注释/停用状态**：

- RPC 命令：`compact` / `set_auto_compaction` → `session.compact(customInstructions)`（`rpc-server.ts:223-231`）。
- 自有扩展 `pentestCompactionExtension` 监听 `session_compact`，并给出一份「压缩时必须保留」清单（`pentest-compaction.ts:14-43`）：

```ts
// pentest-compaction.ts:26-43
`When summarizing this conversation for compaction, you MUST preserve:
1. Scope ... 2. Known Assets ... 3. Active Hypotheses ...
4. Confirmed Findings ... 5. Auth State: Any authentication tokens, cookies, or session information ...
6. Evidence Paths ... 7. Current Phase ... 8. Next Actions ...
You may discard: Verbose HTTP response bodies / Failed dead-end exploration / Redundant tool output ...`
```

- 但 `session.ts:131-135,183-188` 中 `largeToolResultExtension / rtkRewriteExtension / pentestCompactionExtension / scopeGuardExtension` **全部被注释掉**，实际只挂载 `challengeObserverExtension`：

```ts
// session.ts:131-135
const extensions = [
    // largeToolResultExtension({ workspaceRoot: workspaceDir }),
    challengeObserverExtension({ observerEnabled, observerModel }),
    // rtkRewriteExtension(),
]
```

结论：**compaction 目前依赖 pi SDK 默认行为，BreachWeave 自己的 compaction 钩子/指令集是「写好但未接线」的储备代码。**

### 6.2 大工具结果溢出（当前未启用）

`largeToolResultExtension`：超过 32k 字的工具结果落盘到 `.tool-results/<file>.md`，上下文里只留 600 字预览 + 指引（"用 grep/find 先定位，再 offset/limit 分段读"），防止 flag/IP/端口淹没在巨型输出里（`large-tool-result.ts:5-6,77-89,91-120`）。同样被注释掉。

### 6.3 实际生效的降噪手段

- **Observer 看板硬上限**：memory ≤12、ideas ≤8，"超限时压缩本身就是优先动作"（`observer-agent.ts:100-104`）。
- **耐压缩性约束**：看板文字"像代码注释一样精炼，优先保留假设、边界和证据，而非过程流水"（`observer-agent.ts:164`）。
- **全链路 clipText 截断**：160/220/240/600/1200 字不等（`observer-loop.ts:14-20`；`manager.ts:62-67`）。
- **入上下文只带摘要**：Solver 启动只带最近 10/8 条且截断的表格（§4.4）。

### 6.4 压缩时如何防关键事实丢失

关键是**把关键事实从"上下文窗口"里挪到"带外持久存储"**，而不是靠压缩时靠运气保留：

- `MemoryKind` 显式区分 `fact/evidence/hint`（flag 候选、IP/端口、凭据、提示都属于此类），Observer 的 `memory_add` 描述是 "worth surviving compaction"（`tools.ts:232-235`）。
- compaction 指令集把 auth token/cookie/session、evidence 路径、confirmed findings 列为 MUST preserve（`pentest-compaction.ts:31-33`）。
- Solver 系统提示明确"只有值得跨轮保留的事实/证据/失败边界/题目提示才写入 memory"（`manager.ts:1663`）。

一句话机制总结：**真正的压缩/摘要扩展（pentest-compaction、大结果溢出）目前被注释停用，实际降噪靠「Observer 看板 12/8 条硬上限 + 耐压缩文案约束 + 全链路截断 + 入上下文只带摘要」，关键事实防丢靠把 fact/evidence/hint 持久化到带外 memory 而非依赖压缩保留。**

---

## 7. 多 Solver 协作

### 7.1 发现怎么共享（有没有 message bus）

**没有中央 message bus / pub-sub**，是「Manager 中心 + 点对点 stdin RPC + 定向广播」的 hub-and-spoke：

- 底层通道：Manager → Solver 容器的 `stdin` JSONL RPC，`runtime.sendCommand()`（`runtime.ts:591-596`）；Solver → Manager 走 `stdout` + `host_bridge_request`（`host-bridge-client.ts:20-49`）。
- 广播实现：`broadcastChallengeBoardUpdateToRunningSolvers` / `broadcastHintToRunningSolvers` 遍历同题 running Solver 逐个 `follow_up`（`manager.ts:635-671`）；host-bridge 层的 `broadcastToChallengeSolvers` 支持 `excludeSolverId` 和 `steer/follow_up` 两种投递（`host-bridge-handler.ts:89-112`）。

### 7.2 有效结果怎么共享 / 避免重复试错

- **flag 解锁广播**：某 Solver 提交正确后，Manager 向同题其他 Solver 广播（排除提交者、`steer` 投递），附带 flag、进度、剩余数、writeup 路线摘要、当前 ideas/memory 摘要（`host-bridge-handler.ts:212-240,141-177`）：

```ts
// host-bridge-handler.ts:222-237
if (result.remote.correct) {
    broadcastToChallengeSolvers(context, challengeId, formatFlagSolvedBroadcastMessage({ flag, gotCount, flagCount, isCompleted, writeup, ideas, memory }), { excludeSolverId: solverId, delivery: "steer" })
}
```

- **看板变更广播**：challenge 级 memory/idea 增删改都会广播给同题 Solver（`formatChallengeMemoryBroadcastMessage` / `formatChallengeIdeaBroadcastMessage`，`manager.ts:241-269`）。
- **提示共享**：hint 获取后广播（`broadcastHintToChallengeSolvers`，`host-bridge-handler.ts:74-87`）。
- **避免重复**：idea 归一化去重（§4.3）；Solver 任务里先给 Submissions 摘要「优先理解哪些入口/突破口/漏洞链已验证过，避免无差别重复劳动」（`manager.ts:1660`）；续跑/扩展提示「如果其他 solver 已拿到一个 flag，不要重复同一路线，转向剩余 flag」（`ralph-loop.ts:30`）。

### 7.3 有效结果怎么沉淀复用

- challenge 级 memory/ideas 持久化到 `~/.tch-agent/challenge/<id>/`（`memory.ts:86-108`），新 Solver 启动时 `seedSolverBoardFromChallenge` 注入（§4.4）。
- 提交日志 + writeup 沉淀（`store.ts:39-50`），供后续 Solver 和统计复用。
- 统计 `buildChallengeStatsOverview` 按 prompt/model 算战绩，喂回 Planner 做 prompt 选型（`stats.ts:270-427`；`manager.ts:1515-1528`）。

一句话机制总结：**协作靠「Manager 中心化的 stdin RPC + 定向 follow_up/steer 广播」而非消息总线；flag 解锁/看板变更/hint 以 steer 广播给同题其他 Solver，去重靠 idea 归一化 + 提交摘要 + 显式提示语，结果靠 challenge 级 memory/ideas + writeup + 战绩沉淀复用。**

---

## 8. 提交与平台交互

### 8.1 flag 提交在哪一层做

调用链：**Solver 工具 `challenge_submit_flag`**（`config/tools/challenge-tools.ts:15-43`）→ `requestHostBridge("challenge_submit_flag")` → **host-bridge handler**（`host-bridge-handler.ts:212-240`）→ **`ChallengeManager.submitFlag()`**（`manager.ts:1046-1084`）→ **`ChallengeApiClient.submitFlag()`**（`api-client.ts:120-128`）→ `POST /api/submit`。

```ts
// api-client.ts:120-128
async submitFlag(code: string, flag: string): Promise<ChallengeApiSubmitData> {
    return this.runLimited(() => {
        if (this.mockState) return this.mockState.submitFlag(...)
        return this.request<ChallengeApiSubmitData>("/submit", "POST", { code, flag })
    })
}
```

### 8.2 去重 / 冷却 / 错误处理

**没有 flag 级去重、没有提交冷却（cooldown）**——同一 flag 可被重复提交。实际有的防护：

- **全局限速**：每 client 串行化 + 3 req/s（`api-client.ts:43-44,195-215`）；
- **超时**：单请求 `CHALLENGE_API_REQUEST_TIMEOUT_MS = 2500ms`，AbortController 中止（`api-client.ts:45,150-168`）；
- **错误处理**：HTTP 非 2xx / JSON 解析失败 / `envelope.code !== 0` 都抛结构化错误（`api-client.ts:170-193`）；日志层对噪声错误做节流（`manager.ts:599-619`）；
- mock 模式里的 `alreadySolved` 只是测试桩（`manager.ts:853-854`）。

提交后统一记 `appendChallengeSubmissionLog`（含 flag/correct/writeup），完成后触发 `finishChallenge`（`manager.ts:1057-1078`）。

### 8.3 平台接口封装长什么样

`ChallengeApiClient`（`api-client.ts:78-216`）是最薄的一层 fetch 封装，端点与信封结构：

| 方法 | HTTP | 端点 |
|------|------|------|
| listChallenges | GET | `/challenges` |
| startChallenge | POST | `/start_challenge` |
| stopChallenge | POST | `/stop_challenge` |
| submitFlag | POST | `/submit` |
| getHint | POST | `/hint` |

- 认证：`Agent-Token` 头（`api-client.ts:138-140`）；信封 `{ code, message, data }`，`code===0` 才取 `data`（`api-client.ts:1-5,185-192`）。
- 双模式：`create()` 真 API / `createMock()` 本地 mock（`api-client.ts:91-97`；mock 实现见 `manager.ts:787-898`），由 `hostSettings.challenge.mockEnabled / apiBaseUrl / agentToken` 决定（`config/types.ts:15-24`）。
- 比赛平台对接文档见 `docs/第二届腾讯云黑客松智能渗透挑战赛API文档.md`（及 MCP 接入文档）。

一句话机制总结：**提交收敛在 `ChallengeManager.submitFlag`（host-bridge → 单薄 fetch 客户端 POST /api/submit，Agent-Token 认证 + `{code,message,data}` 信封 + 3req/s 限速 + 2.5s 超时），但无 flag 去重、无提交冷却，同一 flag 可重复提交。**

---

## 9. pi 的使用方式

### 9.1 CLI 还是 SDK

**SDK（不是 pi 的 CLI）**：`packages/core/package.json` 依赖 `@mariozechner/pi-coding-agent`、`@mariozechner/pi-ai`、`@mariozechner/pi-agent-core`、`@mariozechner/pi-tui`。Solver 是项目自己编译的二进制 `tch-agent solver rpc` 在 Docker 内运行（`helpers.ts:163-182`），通过 **stdin/stdout JSONL 的 RPC**（镜像 pi SDK 的 rpc-types）与 Manager 通信（`rpc-server.ts:1-14`）。

### 9.2 session / steer / compaction 管理

- `createAgentSession(...)` 创建会话：Planner 用 `SessionManager.inMemory()`（`manager.ts:1327-1332`）；Solver 用 `SessionManager.create(workspaceDir, sessionDir)`（`session.ts:148-152`）；Observer 用独立目录（`observer-agent.ts:360-364`）。
- RPC 命令全集映射到 SDK 方法（`rpc-server.ts:138-298`）：`steer`→`session.steer`、`follow_up`→`session.followUp`、`abort`、`get_state`、`set_model`/`cycle_model`、`set_thinking_level`、`compact`→`session.compact(customInstructions)`、`set_auto_compaction`、`set_auto_retry`、`bash`、`get_messages` 等。
- 关键点：compaction/steer 能力是 SDK 自带、经 RPC 透传；BreachWeave 主要自定义的是「事件驱动扩展 + 工具」。

### 9.3 自定义了哪些工具 / 扩展

**自定义工具**（`config/tools/`）：

- `challenge_submit_flag` / `challenge_get_hint`（走 host-bridge，`challenge-tools.ts`）
- `security_kimi_search`（Kimi+Qwen 双源联网安全检索，`security-kimi-search.ts`）
- `subagent`（起独立 `tch-agent subagent` 进程，single/parallel(≤8, 并发 4)/chain 三模式，`subagent.ts`）
- `submit_sub_agent_output` / `ingest_sub_agent_output` / `document_finding`（pentest 工作区契约，`submit-sub-agent-output.ts` / `ingest-sub-agent-output.ts` / `document-finding.ts`，目前在 `customTools` 中被注释，`tools/index.ts:28-33`）
- Planner 工具 `planner_get_state/start/stop/launch/stop_solver`（`manager.ts:1383-1498`）
- Observer 工具 `memory_*/idea_*/query_solver_history/send_efficiency_reminder`（`observer/tools.ts`）

**自定义扩展**（`solver/extension/`）：

- `challengeObserverExtension`（**启用**：续跑 `ralph-loop` + Observer sidecar，`challenge-observer/index.ts:12-38`）
- `pentestCompactionExtension` / `largeToolResultExtension` / `rtkRewriteExtension`（用 `rtk` CLI 重写 bash 命令）/ `scopeGuardExtension`（工具调用门禁/预算）——**当前全部注释停用**（`session.ts:131-135,183-188`）

一句话机制总结：**用 pi-coding-agent SDK（非 CLI），Solver 以 Docker 内 `tch-agent solver rpc` 二进制跑、经 stdin/stdout JSONL RPC 透传 steer/followUp/compact 等 SDK 能力；自定义了挑战提交/提示/联网检索/子代理/看板/调度六类工具和 observer+续跑扩展，而 compaction/大结果/rtk/scope-guard 四个扩展处于注释停用态。**

---

## 10. 与 Cairn / verialabs 的关键差异

> 说明：本节对 BreachWeave 的结论全部来自本仓库源码（已验证）；对 Cairn（[oritera/Cairn](https://github.com/oritera/Cairn)，AI 状态空间搜索渗透框架）与 verialabs（[verialabs/ctf-agent](https://github.com/verialabs/ctf-agent)，"多模型并行竞赛"、BSidesSF 2026 52 题全解）的对比基于其公开 README/技术拆解文（[friday-go 拆解](https://friday-go.icu/security/offensive/ai-broke-ctf-2026-veria-labs-agent-analysis)、[Cairn 架构分析](https://www.gm7.org/archives/117909)）；本次沙箱无法拉取其源码逐行核实，涉及他们内部实现细节的对比为方向性判断。

### 10.1 BreachWeave 独有的（多了什么）

| 能力 | BreachWeave 实现 | 证据 |
|------|------------------|------|
| **Observer sidecar（旁路监督副驾驶）** | 独立会话每 6 轮审查、只写看板 + steer、绝不代解/不终止 | §1 |
| **Idea / Memory 双层状态** | 方向假设 vs 事实/证据/失败边界分层，归一化去重 + 目录锁 + 体积硬上限 | §4 |
| **host-bridge RPC 回程** | Solver 进程内通过 `host_bridge_request` 向 Manager 反查平台（提交/取 hint/查完成态） | `host-bridge-client.ts` / `host-bridge-handler.ts` |
| **LLM Planner + stale 阻塞回收** | 30s 一轮 LLM 调度，回收必须 stale，快照预排序 | §2 |
| **挑战续跑循环（ralph-loop）** | `agent_end` 只要未完成就强制续跑，杜绝早停 | `ralph-loop.ts` |
| **flag 解锁广播 + writeup 共享** | 提交正确后向同题其他 Solver steer 广播路线摘要 | `host-bridge-handler.ts:141-177` |
| **每 Solver 独立 Docker 隔离** | 一 Solver 一容器 + 独立 session/workspace | §3 |

### 10.2 BreachWeave 缺的（少了什么）

针对题目点名的四项，逐条对照：

1. **message bus（消息总线）——没有**。BreachWeave 是 Manager 中心的点对点 stdin RPC + 定向 `follow_up/steer` 广播，没有 topic 订阅、没有持久队列、没有跨进程的发布/订阅。相比之下 verialabs 类架构常带一个显式的消息总线让多 solver/多模型共享发现；BreachWeave 的"共享"是 Manager 主动 push，而非 solver 主动 publish/subscribe。

2. **提交纪律（submission discipline）——基本没有**。无 flag 去重、无提交冷却、无"同一 flag 已被提交过就跳过"的守卫；只有 API 客户端 3 req/s 限速 + 2.5s 超时 + 信封错误处理（§8.2）。重复/无效提交会直接打到平台。

3. **僵局检测（deadlock detection）——没有显式实现**。只有时间维度的 `stale`（题目/Solver 空闲超过 staleTimeout 且无正确提交），以及 Observer 的低效 `steer` 提醒（且提醒只注入不终止）；没有"检测到 Solver 陷入循环/无进展就 kill/重路由"的机制（续跑循环对 error 才计数，对"活着但空转"不处理）。

4. **triage（确定性分诊）——弱化版**。BreachWeave 的"分诊"是一个 LLM Planner 看 markdown 快照做决策，系统侧只有一条粗排序（untouched → difficulty → remainingScore）；没有确定性的优先级打分队列、没有按漏洞类型/攻击面的硬分诊。Cairn 则把渗透抽象成**状态空间搜索 + 图**（bootstrap/explore/conclude 阶段、图快照），与 BreachWeave 的"看板 + 副驾驶"范式本质不同；verialabs 是**多模型并行竞赛 + 显式 triage/solver 分工**。

### 10.3 一句话结论

**BreachWeave 的独到之处是"Observer 旁路监督 + Idea/Memory 双层看板 + host-bridge 平台反查 + 续跑防早停 + 每 Solver 独立 Docker"，比 Cairn 的状态空间搜索图、比 verialabs 的多模型竞赛多了"带外看板整理"与"结束外置"；但它没有它们那样显式的 message bus、提交纪律（flag 去重/冷却）和僵局检测，triage 也只是 LLM + 粗排序而非确定性分诊队列。**

---

## 附录：关键文件索引

| 主题 | 文件 |
|------|------|
| Manager / Planner 调度 | `packages/core/src/challenge/manager.ts` |
| 调度 prompt | `packages/core/src/config/prompts/builtin/CHALLENGE_PLANNER.md` |
| Idea / Memory 数据结构与存储 | `packages/core/src/challenge/memory.ts` |
| 题目/提交/尝试日志存储 | `packages/core/src/challenge/store.ts` |
| 统计 | `packages/core/src/challenge/stats.ts` |
| 平台 API 客户端 | `packages/core/src/challenge/api-client.ts` |
| host-bridge（进程↔Manager 反查） | `packages/core/src/challenge/host-bridge-client.ts` / `host-bridge-handler.ts` / `host-bridge-types.ts` |
| Observer 主循环 | `packages/core/src/solver/extension/challenge-observer/observer-loop.ts` |
| Observer Agent + 系统提示 | `packages/core/src/solver/extension/challenge-observer/observer-agent.ts` |
| Observer 工具/看板 | `packages/core/src/solver/extension/challenge-observer/tools.ts` / `board-format.ts` / `observer-store.ts` / `types.ts` |
| 续跑循环 | `packages/core/src/solver/extension/challenge-observer/ralph-loop.ts` |
| Solver 会话创建 | `packages/core/src/solver/session.ts` |
| Solver 看板存储 | `packages/core/src/solver/board-store.ts` |
| RPC 协议 | `packages/core/src/solver/rpc/rpc-server.ts` / `rpc-types.ts` |
| 运行时（Docker） | `packages/core/src/runtime/runtime.ts` / `types.ts` / `helpers.ts` |
| 自定义工具 | `packages/core/src/config/tools/*.ts` |
| 压缩/大结果/rtk/scope-guard 扩展 | `packages/core/src/solver/extension/pentest-compaction.ts` / `large-tool-result.ts` / `rtk-rewrite.ts` / `scope-guard.ts` |
