# Cairn 冠军系统源码精读：架构拆解 + 西湖论剑 AI Agent 解题赛道适配分析

> 源码位置：`D:\ctf-agent\cairn-ref`（AGPLv3）。版本 `cairn/__init__.py:1` = `0.2.1`。
> 本文所有引用均为 `文件路径:行号`，关键代码摘录附后，支撑后续架构设计决策。

---

## 0. 一句话结论

Cairn 的本质是**"黑板架构（Blackboard）+ Fact/Intent 因果图 + 三任务 OODA 循环"**：Server 只维护一张 `origin → goal` 的事实-意图图的一致性（不推理），Dispatcher 是唯一写图者/调度者，把 Agent 收敛成 `bootstrap / reason / explore` 三类纯结构化 JSON 输出任务。**它的调度层、输出契约层、心跳/租约层、双阶段 conclude 收尾层与我们自研编排器高度同构，几乎可整体移植；但它的"每项目一个 Docker 容器 + 渗透工具链 + TSEC 提交"这一执行层必须替换成"Kali REST API + Jeopardy 提交"**。

---

# 第一部分：代码级架构拆解

## 1. 模块结构（4 大组件 + 目录职责）

设计文档 `docs/specs/dispatcher-design.md:38-125` 明确划为 4 部分，与代码一一对应：

| 组件 | 代码位置 | 职责 |
|---|---|---|
| Cairn Server | `cairn/src/cairn/server/` | 协议真相源：SQLite 存 Project/Fact/Intent/Hint，维护认领/心跳/结论/reason lease 状态，不推理 |
| Dispatcher | `cairn/src/cairn/dispatcher/` | 核心：拉图、定任务、选 Worker、管容器/进程、session/超时/健康/收尾、写回 |
| 项目容器 | `container/`（`Dockerfile`）+ `dispatcher/runtime/containers.py` | 每项目一个 Kali 容器，承载 Worker 进程 + 工具链 |
| Worker/Agent CLI | `dispatcher/workers/adapters/` | claudecode / codex / pi / mock 四种 driver，收 prompt 出 JSON |

### 1.1 `cairn/src/cairn/dispatcher/` 子目录职责

- `config.py` — Pydantic 配置模型（`DispatchConfig`/`RuntimeConfig`/`WorkerConfig` 等）+ 静态校验 + mock 行为解析（424 行）。
- `models.py` — 调度期运行时数据结构：`RunningTask`（`models.py:8-17`）、`ReasonCheckpoint`（`models.py:20-23`）。
- `contracts.py` — **输出契约校验**：把 LLM 的 `{accepted, data}` 结构校验成 `(kind, data)` 二元组。
- `output_parser.py` — 从任意文本里稳健提取 JSON 对象（fenced block + `raw_decode` 多候选）。
- `prompting.py` — prompt 加载 + 纯字符串占位符替换 + JSON 块格式化（仅 32 行）。
- `protocol/client.py` — 面向 Server 的 HTTP 客户端，**每线程一个 `requests.Session`**（`client.py:150-162`）。
- `scheduler/loop.py` — 主调度循环（935 行，系统心脏）。
- `scheduler/worker_select.py` — Worker 排序选择（17 行）。
- `tasks/{bootstrap,reason,explore,common}.py` — 三类任务的执行体 + 公共工具。
- `runtime/` — 执行后端抽象：`backend.py`（Protocol）、`containers.py`（Docker 后端）、`local_backend.py`+`local_process.py`（本机子进程后端）、`process.py`（Docker exec 进程封装）、`heartbeat.py`（心跳租约后台线程）、`cancellation.py`（协作式取消）、`startup_healthcheck.py`（启动探活）。
- `workers/{base,registry,health}.py` + `adapters/` — Worker driver 抽象/注册/HTTP 探活 + 四个具体驱动。
- `prompts/{default,mock}/` — 5 份 markdown prompt（`bootstrap/explore/reason` + 两个 `*_conclude`）。

### 1.2 关键设计锚点（代码级）

- 入口：`cli.py:57-67` `dispatch` 子命令 → `DispatcherLoop(config_path).run()`。
- Server 入口：`cli.py:28-39` `serve` → `uvicorn.run(app)`；`app.py:28-32` 挂载 5 个 router。
- DB：`server/db.py:8` 默认路径 `~/.local/share/cairn/cairn.db`；`db.py:12-82` SCHEMA（7 张表）；`db.py:111` WAL + 外键。

---

## 2. Server 协议全貌

设计文档 `docs/specs/server-protocol.md`（864 行）是协议权威描述，实现于 `server/` 下。

### 2.1 数据模型（`server/db.py` SCHEMA + `server/models.py`）

| 概念 | 表/模型 | 关键字段 | 语义 |
|---|---|---|---|
| Project | `projects` / `ProjectMeta`（`models.py:46-52`） | `status`、`bootstrap_enabled`、`reason_*` | 三种状态 `active/stopped/completed`（`models.py:49`） |
| Fact | `facts` / `Fact`（`models.py:13-15`） | `id`、`description` | 只增不改；`origin`/`goal` 为特殊 Fact（`projects.py:87-94`） |
| Intent | `intents`+`intent_sources` / `Intent`（`models.py:18-27`） | `from`(数组)、`to`(nullable)、`description`、`creator`、`worker`、`last_heartbeat_at`、`concluded_at` | 图中的**边**；`from` 多 Fact = 超边（`server-protocol.md:79-87`） |
| Hint | `hints` / `Hint`（`models.py:32-36`） | `content`、`creator` | 图外输入，不影响因果 |
| Settings | `settings`（`db.py:13-18`） | `intent_timeout`、`reason_timeout` | 全局超时（秒） |

关键点：`intent_sources` 表把一条 Intent 的多个 `from` 存成多行（`db.py:52-58`），`intent_to_model` 读回（`services.py:150-165`）。ID 生成用全局计数器 `proj_%03d`（`services.py:14-17`）+ 项目内 scoped 计数器 `f/i/h%03d`（`services.py:20-48`）。

### 2.2 状态机（核心）

**Intent 状态由 `to`（是否结论）+ `worker`（谁持有）联合表达**，见 `server-protocol.md:148-157` 的语义表：

- 未结论 + `worker=null` → 待认领；未结论 + `worker` 有值 → 有人执行中；已结论（`to` 非空）→ 永久保留产出者。

**超时清理（惰性，读取时触发）**——这是协议的关键一致性机制，不靠后台任务：

```python
# services.py:222-237  expire_workers
UPDATE intents SET worker = NULL
WHERE to_fact_id IS NULL AND worker IS NOT NULL AND last_heartbeat_at IS NOT NULL
  AND (julianday(?) - julianday(last_heartbeat_at)) * 86400 > ?   # > intent_timeout 秒
```

`expire_reason_leases`（`services.py:240-257`）对 `project.reason_*` 同理。两个清理在**每次读**时执行：`list_projects`（`projects.py:47-48`）、`get_project`（`projects.py:127-128`）、`export`（`export.py:23-24`）、claim/heartbeat 前（`projects.py:195/223/244/261`、`intents.py` 经 `services.py:115/127`）。

**认领/续约/释放/结论的原子语义**：

| API | 实现 | 冲突返回 |
|---|---|---|
| 创建 Intent | `intents.py:33-71` | `worker` 须为 null 或 `==creator`（`services.py:95-97`）；`from` 不可含 `goal`（`services.py:90-92`） |
| heartbeat（=claim 或续约） | `intents.py:78-93` | 已结论或被他人持有 → 409（`services.py:112-121`） |
| release | `intents.py:100-115` | 仅持有者本人可释放；已未认领幂等 |
| conclude | `intents.py:122-147` | 原子：`INSERT facts` + `UPDATE intents SET to_fact_id/concluded_at` |
| complete | `projects.py:257-300` | 建 `to='goal'` 的结论 Intent，项目 `status='completed'` 并清空 reason lease |
| reopen | `projects.py:303-358` | 删完成边，新增纠错 Fact + `external_feedback` 边，回 `active` |
| reason claim/heartbeat/release | `projects.py:191-254` | 项目级租约，单项目最多一个 |

**stopped 的硬停止语义**：`projects.py:181-186` 切 stopped 时**立即清空所有 open intent 的 worker + 清空 reason lease**，`projects.py:172-173` completed 项目不可再改 status（只能 reopen）。

### 2.3 导出（Dispatcher 渲染用）

- `GET /projects/{id}/export?format=yaml`（`export.py:50-98`）：图快照，供 prompt 注入。
- `GET /projects/{id}/export?format=timeline`（`export.py:101-151`）：审计时间线。

---

## 3. Dispatcher 调度算法细节

### 3.1 主循环（`scheduler/loop.py:80-110`）

```python
run_startup_healthchecks()
while True:
    if not self._settings_checked: self._validate_server_settings(); ...
    self._reap_futures()          # 收已完成任务、更新 reason checkpoint
    self._reap_cleanup_futures()  # 收容器 cleanup
    summaries = self.client.list_projects()
    self._initialize_reason_checkpoints(summaries)
    self._refresh_runtime_projects(summaries)
    self._cancel_inactive_tasks(summaries)  # 非 active → 取消运行中任务
    self._queue_container_cleanups(summaries)
    self._dispatch_available(summaries)
    time.sleep(self.config.runtime.interval)
```

**`interval` 被刻意复用为"主循环节拍 + 带 claim 任务的 heartbeat 周期"**（`dispatcher-design.md:531-538`、`loop.py:46/58` 传给 `HeartbeatLease.for_intent(..., interval)`）。这是明确设计决策，不是耦合。

### 3.2 任务选择规则（`loop.py:191-241` `_dispatch_available` + `loop.py:254-338` `_try_dispatch_project`）

**全局顺序**（`_dispatch_available`）：
1. `max_workers` 满 → 跳过（`loop.py:192-199`）。
2. **已运行项目优先**（`running_projects` 先于 `idle_projects`，`loop.py:205-210`），轮询游标 `_ordered_projects`（`loop.py:243-252`）保证公平。
3. 运行中项目都无可派发、且未达 `max_running_projects` → 才启动一个新项目（`loop.py:222-241`）。

**单项目内顺序**（`_try_dispatch_project`，这是三任务选择规则的核心代码）：

```python
# loop.py:266-274  项目级并发上限
if self._project_running_task_count(...) >= max_project_workers: skip

project = self.client.get_project(summary.id)        # loop.py:276
if self._is_initial_project(project):                 # loop.py:286
    if project.project.reason is not None: return False
    if self._project_requires_bootstrap(project):     # 走 bootstrap
        return self._dispatch_initial_project(project)
    export_yaml = ...; return self._dispatch_reason(project, export_yaml, "initial")
if project.project.reason is None:                    # loop.py:293  非初始态先看 reason 触发
    trigger = self._reason_trigger(project)
    if trigger is not None:
        return self._dispatch_reason(project, export_yaml, trigger)
# 再消费未认领 explore intent（最新优先）           # loop.py:298-318
unclaimed_intents = [i for i in project.intents if i.to is None and i.worker is None ...]
if unclaimed_intents:
    newest = max(unclaimed_intents, key=lambda i: i.created_at)   # loop.py:316
    return self._dispatch_explore(project, export_yaml, newest)
return False  # 无事可做
```

**三任务的触发条件与保留语义**（`_is_initial_project`/`_is_bootstrap_intent`/`_project_requires_bootstrap`）：

- 初始态判定：`facts == {origin, goal}` 且 intents 为空或全为 bootstrap intent（`loop.py:664-670`）。
- bootstrap 保留 intent 约定：`description=="bootstrap" && creator=="dispatcher.bootstrap" && from==["origin"] && to is None`（`loop.py:647-653`，常量 `loop.py:30-31`）。
- `_project_requires_bootstrap`（`loop.py:672-677`）：`bootstrap_enabled` 且（已有 bootstrap intent 或存在声明支持 `bootstrap` 的 Worker）。

**reason 去重（"新态势"触发，`loop.py:704-718`）**：

```python
def _reason_trigger(self, project):
    checkpoint = self.reason_checkpoints.get(project.project.id)
    if checkpoint is None: return "initial"
    changes = []
    if len(project.facts) > checkpoint.fact_count: changes.append(...)
    if len(project.hints) > checkpoint.hint_count: changes.append(...)
    if checkpoint.open_intent_count > 0 and open_intent_count == 0: changes.append(...)
    return ",".join(changes) or None
```

`ReasonCheckpoint`（`models.py:20-23`）只记 `fact_count / hint_count / open_intent_count` 三个数。**首次无历史且当前无 open intent → 触发；之后只有 Fact/Hint 增加，或"从有 open intent 变无"才再触发**。基线初始化 `_initialize_reason_checkpoints`（`loop.py:858-878`）对"已有 open intent 但无 checkpoint"的项目建立基线，避免吞掉运行中新增的第一批 Fact/Hint。checkpoint 仅在 reason 任务 `success` 时更新（`loop.py:765-773`）。

### 3.3 claim → 派发 → 心跳 → 超时 → 收尾（`loop.py:366-545` + `tasks/*` + `heartbeat.py`）

三类任务派发流程一致（以 explore 为例，`loop.py:488-545`）：

```python
selection = self._select_worker(project.project.id, "explore")     # 选 Worker
claim = self.client.heartbeat(project.id, intent.id, worker.name)  # 先 claim
if claim.status_code in (403, 409): ... return False
future = self.executor.submit(run_explore_task, ..., cancellation := TaskCancellation())
self.futures[future] = RunningTask(project.id, "explore", worker.name, cancellation, intent_id=intent.id)
```

**先 claim 成功才真正起进程**（`dispatcher-design.md:446-450`）。reason 走 `client.claim_reason`（`loop.py:381`）。任务线程内部：

- 起 `HeartbeatLease` 后台线程（`bootstrap.py:46-47`、`reason.py:45-46`、`explore.py:43-44`）。
- `ensure_running` 拉容器（`bootstrap.py:49`）。
- （可选 `startup_and_task`）派发前健康检查（`bootstrap.py:51-90`）。
- `render_prompt` + `build_execute` + `run_worker_process`（`bootstrap.py:92-110`）。
- 解析输出 → 写回 conclude/complete/release。

**心跳租约 `heartbeat.py:88-123`**：独立 daemon 线程每 `interval` 发一次心跳；`403/409` 立即判失败；瞬态失败给 `max(interval, 2*interval)` 宽限（`heartbeat.py:14/98`）；失败后 `process.kill()`（`heartbeat.py:120-123`）。

**超时**：容器模式用 coreutils `timeout -k 5s {timeout}s`（`containers.py:185-194`）；`communicate` 再额外加 15s 宽限（`common.py:15/46-47`）。本机模式用 Python `wait(timeout)`（`local_process.py:71-95`）。`did_timeout` 判定 `timed_out or returncode in (124,137)`（`common.py:34-35`）。

**收尾（cleanup）不阻塞主循环**：独立 `cleanup_executor`（`loop.py:54`），completed→`cleanup_completed`（stop/remove）、stopped→`cleanup_stopped`（`loop.py:784-818`）。

### 3.4 失败处理与重试（关键：**不做立即重试**）

设计文档多处强调"**不做立即重试，只记日志，释放 claim 交给下一轮**"（`dispatcher-design.md:380/469/497/517`）。代码落地为三类退出结果字符串：

- 任务函数返回 `"success" / "failed" / "cancelled" / "unhealthy" / "rejected"`（`loop.py:720-782` 消费）。
- `unhealthy` → 该 worker 进 5s 不可选窗口 `worker_unhealthy_until`（`loop.py:28/742-749`）。
- `rejected` → 按 `(project, task_type, worker)` 键进 5s 不可选窗口（`loop.py:29/753-762`）。
- 不可选窗口在 `_select_worker` 里过滤（`loop.py:563-570`）。

**写回失败语义**（`tasks/common.py:168-225` `write_conclude_result_with_fact_id`）：conclude 写回失败 → `best_effort_release` 释放 intent + 记日志，不重试。reason 的 complete/intent 写回失败 → 记日志 + 作废（`reason.py:204-273`）。

### 3.5 日志设计（状态变化优先）

- 格式：`DispatcherLogFormatter` 把 logger 名缩短为 `cairn.dispatcher.` 后的短名（`logging.py:6-16`）。
- **去抖**：`_log_changed`（`loop.py:890-898`）缓存 `(level, msg, args)`，重复轮询/重复 skip 不刷屏，状态变了才打；派发/clear 时清状态（`loop.py:897-904`）。
- 输出截断：`preview()` 压缩空白并限长 1200（`common.py:27-31`）；健康检查 detail 限 200（`health.py:7`）。
- `requests`/`urllib3` 日志降为 WARNING（`logging.py:31-32`）。

---

## 4. Worker 交互契约

### 4.1 Driver 抽象（`workers/base.py`）

`WorkerDriver` 抽象方法（`base.py:18-54`）：`check_health`、`build_execute`、`build_conclude`、`extract_session`、`extract_response_text`；`supports_conclude()` 默认 True。两个辅助基类：

- `SeedSessionDriver`（`base.py:57-59`）：`prepare_session()` 预生成 uuid（claude/pi/mock 用）。
- `RegexSessionDriver`（`base.py:62-71`）：从 stderr 正则 `session id:\s*([0-9a-fA-F-]+)` 提取 session（codex 用）。

**注册**（`workers/registry.py:10-24`）：`DRIVERS`（容器模式，注入 provider）+ `LOCAL_DRIVERS`（本机模式，用 CLI 自身配置）。`get_driver(name, execution)` 二选一。

### 4.2 四个 driver 的命令构造（关键差异）

| driver | 环境变量（`config.py:20-38`） | 健康检查端点 | execute 命令 | conclude 命令 | session 来源 |
|---|---|---|---|---|---|
| claudecode | `ANTHROPIC_*` | `{base}/v1/messages`（`claudecode.py:17-33`） | `claude --session-id {uuid} --dangerously-skip-permissions -p -- {prompt}`（`claudecode.py:38-51`） | `claude -r {session} ...` | 预生成 uuid |
| codex | `CODEX_*`+`OPENAI_API_KEY` | `{base}/responses`（`codex.py:17-32`） | `codex exec --dangerously-bypass-approvals-and-sandbox --model ... -c model_providers.cairn.* ...`（`codex.py:37-71`） | `codex exec resume {session} ...`（`codex.py:73-107`） | stderr 正则 |
| pi | `PI_MODEL/BASE_URL/API_KEY/PROVIDER_API` | 按 `PI_PROVIDER_API` 三选一（`pi.py:21-51`） | `pi --provider cairn --model ... --mode json --session-dir ... [--session {id}] -p {prompt}`（`pi.py:57-74`） | 同上 + `--session`（`pi.py:76-94`） | 预生成 uuid / stdout NDJSON `type:"session"`（`pi.py:119-128`） |
| mock | `MOCK_*` | 进程内随机（`mock.py:136-139`） | `python3 -c _SCRIPT <behavior> <prompt>`（`mock.py:131-134`） | 同 execute（`mock.py:147-148`） | 预生成 uuid |

**pi driver 与我们最相关**（我们 worker 就是 pi）：`_wrap_with_models` 通过 shell 脚本把 `models.json` 写进 `PI_CODING_AGENT_DIR` 再 `exec pi`（`pi.py:161-189`）；`extract_response_text` 解析 pi 的 NDJSON 事件流，取 `turn_end`/`agent_end` 的 assistant 文本（`pi.py:130-159`）——**这是"结构化输出"的真正来源，不是靠模型自觉**。

### 4.3 Prompt 渲染（`prompting.py` + `tasks/*`）

- 极简：`render_prompt` = 逐 key `str.replace("{key}", value)`（`prompting.py:12-16`）。
- `format_json_block` 用 `json.dumps(indent=2)` 输出 JSON 块（`prompting.py:31-32`）。
- **大图 YAML 不进 prompt 正文**，而是写到容器内文件再告诉模型去读（`common.py:56-70` `write_graph_snapshot_reference`，路径 `/tmp/cairn-prompts/{phase}-{uuid}/graph.yaml`）。reason/explore 用 `graph_yaml` 占位符时走这个（`reason.py:106-111`、`explore.py:92-97`）。
- bootstrap 用 `{origin}/{goal}/{hints}` 三个占位符（`bootstrap.py:384-399`），**不读图 YAML**（`dispatcher-design.md:238-239`）。

### 4.4 结构化输出格式与校验（`contracts.py` + `output_parser.py`）

所有任务统一 `{"accepted": bool, "data": {...}}` 或 `{"accepted": false, "reason": "..."}`。`_unwrap_wrapped_payload`（`contracts.py:12-21`）拆包，还**兼容"裸 payload"**（无 accepted 包裹也能识别，`_looks_like_*` 系列 `contracts.py:28-59`）。

| 任务 | 输出 | 校验函数 | 关键约束 |
|---|---|---|---|
| bootstrap execute | `data:{fact:{description}, complete:{description}}` | `validate_bootstrap_execute_payload`（`contracts.py:104-132`） | fact+complete 都必填 |
| bootstrap conclude | `data:{fact:{description}}` | `validate_bootstrap_conclude_payload`（`contracts.py:135-154`） | 只允许 fact |
| reason | `data:{complete:{from,description}}` / `{intents:[{from,description}]}` / `{}` | `validate_reason_payload`（`contracts.py:62-101`） | complete 与 intents 互斥；**open_intents 为空则必须给 intent**；`intents[:max_intents]` 截断（`contracts.py:95`）；兼容单数 `intent`（`contracts.py:77-80`） |
| explore | `data:{description}` | `validate_explore_payload`（`contracts.py:157-170`） | description 必填且必须客观 |

**JSON 提取**（`output_parser.py:11-37`）：先整段 `json.loads`；失败则取 ``` 围栏块；再失败则对每个 `{` 位置 `raw_decode`。返回第一个 dict。这保证了"模型夹带解释文字也能解析"。

### 4.5 双阶段 conclude 收尾（**3 小时限时最关键的可移植机制**）

`explore`/`bootstrap` 支持"第一阶段 execute 超时或解析失败 → 杀进程 → **保留 session** → 同 session 进 conclude 总结"：

- 触发条件只有两类：**执行超时**、**输出解析/结构校验失败**（`dispatcher-design.md:471-480`）。
- `accepted:false`、退出码非 0、无结果 → **不进 conclude**，直接失败（`dispatcher-design.md:503-517`）。
- 进 conclude 前四重检查：`supports_conclude() && session`、`lease.failure is None`、`!cancellation.is_cancelled`、`project_allows_conclude_fallback`（`explore.py:255-288`、`bootstrap.py:247-285`）。
- conclude prompt 明确要求"停止探索、只总结已确认事实"（`prompts/default/explore_conclude.md:4,20-24`）。
- **被 `stopped` 取消的任务不进 conclude**（`dispatcher-design.md:142`、`cancellation.is_cancelled` 短路）。

---

## 5. 并发与资源控制

四层并发（`dispatcher-design.md:586-598`，实现于 `config.py:165-173` + `loop.py`）：

| 层 | 字段 | 实现 |
|---|---|---|
| 全局任务总数 | `runtime.max_workers` | `ThreadPoolExecutor(max_workers=...)`（`loop.py:53`）+ `len(self.futures) >= max_workers` 短路（`loop.py:192`） |
| 已接手 active 项目数 | `runtime.max_running_projects` | `runtime_project_ids` 集合 + `_running_project_count`（`loop.py:640-642`、`loop.py:222-241`） |
| 单项目并发 | `runtime.max_project_workers` | `_project_running_task_count`（`loop.py:615-616`），**统一计入 bootstrap+reason+explore** |
| 单 Worker 并发 | `workers[].max_running` | `_worker_counts`（`loop.py:609-613`）+ `_select_worker` 过滤（`loop.py:559-561`） |

**Worker 选择**（`worker_select.py:8-17`）：排序键 `(priority, running_count, random.random())` —— priority 升序、同优先级选运行数少的、再随机。`_select_worker`（`loop.py:547-607`）先按 `task_types` 过滤 → `max_running` → 不健康窗口 → 拒绝窗口，产出候选。

**容器生命周期**（`containers.py`）：`ensure_running` 幂等（`containers.py:35-72`）：inspect 状态，不存在则 `docker run image sleep infinity`，名字冲突（409）则复用。容器名 `cairn-dispatch-{project_id}`（`containers.py:31-33`）。进程执行：`docker exec`（`process.py:60-72`），kill 走 `exec_inspect` 取 Pid 后 `kill -KILL`（`process.py:97-111/173-189`）。写文件走 `put_archive`（tar 流）（`containers.py:197-205/238-269`）。

**本机后端**（`local_backend.py` + `local_process.py`）——**无 Docker 时我们最该参考的实现**：

- `ensure_running` = 建 `<workspace_root>/<project_id>/` 目录（`local_backend.py:35-39`）。
- `build_exec_process` = `LocalProcess(cwd=项目目录, env={**os.environ, **worker.env})`（`local_backend.py:41-56`）。
- `LocalProcess.start` 用 `start_new_session=True` 建独立进程组（`local_process.py:50-61`）；`_terminate` 先 SIGTERM 组 → 等 grace → SIGKILL 组（`local_process.py:105-116`）。
- cleanup 只 rmtree 工作目录（`local_backend.py:71-79`）。

**取消**（`cancellation.py:8-29`）：`TaskCancellation` 持一个 `ExecProcess` 引用 + `_reason`；`cancel()` 幂等设置 reason 并杀进程；`attach_process` 时若已有 reason 立即补杀（防止"取消发生在进程启动前"竞态）。

---

## 6. 配置与启动校验

`config.py` 三层校验（`dispatcher-design.md:807-857`）：

- **加载时**：`DispatchConfig.load`（`config.py:274-279`）→ `model_validate`（含 `common_env` 合并到每个 worker，`config.py:218-246`；worker 名唯一、`max_project_workers <= max_workers`，`config.py:248-257`；容器模式校验 LLM env key，`config.py:259-272`）→ `validate_prompt_resources`（`config.py:294-307`，校验 prompt 组存在 + 每份 prompt 包含必需占位符）。
- **启动健康检查**：`run_startup_healthchecks`（`startup_healthcheck.py:25-48`）并发探活所有 worker，**任一 healthy 即通过**（`loop.py:931-935`）；本机模式改为 `--help` 探测 CLI（`loop.py:132-189`）。
- **运行时**：`_validate_server_settings`（`loop.py:906-929`）要求 `server intent_timeout/reason_timeout > dispatcher interval`，否则抛错；`< 2*interval` 告警"心跳余量不足"。

---

# 第二部分：适配分析（拿什么 / 改什么 / 怎么改 / 为什么）

> 我们的约束：Jeopardy（web/pwn/re/crypto/misc）、API-only、3 小时初赛限时抢分、人机交互、决赛代码审查、Windows 编排器+worker + Kali REST（`http://<host>:5000/api/command`，无 Docker）、DeepSeek（OpenAI 兼容）/Qwen、已有 `state.json` 黑板 + hints 注入 + 多题并行 + 同题竞速 + pi worker + `kali.ts` 工具转发。

## 7. 可直接抄进我们编排器的机制（逐条给文件:行号 + 移植要点）

### 7.1 三任务分解 + 双阶段 conclude 收尾（最高价值，整套抄）

- **拿**：`tasks/bootstrap.py`、`tasks/reason.py`、`tasks/explore.py` 三份状态机 + `tasks/common.py` 公共工具。
- **为什么**：3 小时限时下，`explore` 超时后"同 session 总结已确认进展再落 Fact"（`explore.py:241-384`）能**把超时的探索抢救成一条可用线索**，而不是白丢 300 秒。这是 Cairn 打满 54/54 的关键鲁棒性设计。
- **移植要点**：
  1. 三任务返回值统一为 `"success"/"failed"/"cancelled"/"unhealthy"/"rejected"` 字符串，`_reap_futures`（`loop.py:720-782`）据此更新 checkpoint/不可选窗口——直接沿用这个枚举协议。
  2. `did_timeout` 判 `timed_out or returncode in (124,137)`（`common.py:34-35`）要适配我们 worker（pi 子进程被我们 kill 时的退出码需核对）。
  3. conclude fallback 的四重前置检查（`explore.py:255-288`）原样保留：`supports_conclude && session` / 心跳未丢 / 未被取消 / 项目仍 active。

### 7.2 输出契约 + 校验（`contracts.py` 整文件可搬）

- **拿**：`_unwrap_wrapped_payload`（`contracts.py:12-21`）+ `_looks_like_*`（`contracts.py:28-59`）+ `validate_*`（`contracts.py:62-170`）。
- **为什么**：`accepted` 包裹 + **兼容裸 payload** + `intent` 单复数兼容（`contracts.py:77-80`）+ `max_intents` 截断（`contracts.py:95`）这套防御性解析，直接对抗"模型偶尔不按模板输出"。决赛代码审查时这也是最容易被问"为什么健壮"的部分。
- **移植要点**：几乎零改动；把 `ReasonTaskConfig.max_intents`（`config.py:132-134`）一起带过来作为"reason 单轮最多提几条探索方向"的硬上限。

### 7.3 JSON 提取器（`output_parser.py` 整文件）

- **拿**：`extract_json_object`（`output_parser.py:11-37`）。
- **为什么**：pi `--mode json` 仍可能夹带前置说明；fenced-block + `raw_decode` 多候选扫描是廉价且高成功率的兜底。

### 7.4 Worker driver 抽象 + 注册 + HTTP 探活（`workers/` 整目录，裁剪后用）

- **拿**：`base.py`（ABC + Seed/Regex session）、`registry.py`、`health.py`（`http_ping` 2xx 判健康 `health.py:31-36`、`proxies_from_env` `health.py:39-50`）。
- **为什么**：我们"DeepSeek↔Qwen 可能切换"正是 Cairn 用 `type` 注册表解决的同一问题；`PI_PROVIDER_API` 三选一（`pi.py:21-51`）已经把 OpenAI-compatible 的 `/chat/completions` 分支写好了——**DeepSeek 和 Qwen 都走 `PI_PROVIDER_API: openai-completions` 即可，无需新 driver**。
- **移植要点**：
  1. 保留 `pi` driver 的 `_local_argv`（`pi.py:96-117`）作为我们 Windows worker 的基准，但把 `--tools read,write,edit,bash,grep,find,ls`（`pi.py:111`）替换成我们 `kali.ts` 暴露的工具集（bash 要转发到 Kali）。
  2. `extract_response_text` 的 NDJSON 解析（`pi.py:130-159`）直接复用——它决定了"结构化输出"从哪里取。
  3. 健康检查保留 `proxies_from_env`：我们 Windows 编排器如果也要走代理访问模型，这一行能避免"健康检查与 worker 出网路径不一致"的坑。

### 7.5 HeartbeatLease 后台心跳（`heartbeat.py` 整文件）

- **拿**：`HeartbeatLease.for_intent/for_reason`（`heartbeat.py:42-71`）+ `_run` 的失败语义（`heartbeat.py:88-123`）。
- **为什么**：把"认领后持续续约、掉了心跳就自杀进程"这条 lease 语义，从 Server 协议解耦成一个独立线程类，可以直接挂到我们的 `state.json` 黑板实现上（只需替换 `heartbeat` callable 为"更新 state.json 里该 intent 的 last_heartbeat 时间戳"）。
- **移植要点**：`403/409` 立即失败 vs 瞬态失败 2×interval 宽限（`heartbeat.py:94-109`）这套分层要保留；`HEARTBEAT_FAILURE_GRACE_MULTIPLIER=2`（`heartbeat.py:14`）调成适合我们轮询周期的值。

### 7.6 reason 去重（ReasonCheckpoint，防浪费 3 小时）

- **拿**：`models.py:20-23` + `_reason_trigger`（`loop.py:704-718`）+ `_initialize_reason_checkpoints`（`loop.py:858-878`）。
- **为什么**：3 小时里最怕"reason 反复被触发、每次都重新读图判断、不产生新价值"。Cairn 只按 **Fact 数 / Hint 数 / open-intent 有无** 三个计数决定是否重触发（`dispatcher-design.md:577-584`），**明确不把"intent 总数增加"当触发条件**——因为那只是上一轮 reason 刚产出新 intent，不是新态势。
- **移植要点**：把三个计数存进我们的 `state.json` 每题的 checkpoint 字段；reason 成功才更新（`loop.py:765-773`）。

### 7.7 Worker 选择排序 + 四层并发上限

- **拿**：`worker_select.py:8-17`（`(priority, running_count, random)`）+ `_select_worker` 过滤链（`loop.py:547-607`）+ 四层并发（`config.py:165-173`、`loop.py:192/222/266/559`）。
- **为什么**：我们"多题并行 + 同题多 worker 竞速"天然需要 `max_workers`（全局线程池上限）和 `workers[].max_running`（单模型 key 配额，DeepSeek/Qwen 都有 RPM/TPM 限制，`dispatcher-design.md:649-653` 明确"一个 Worker = 一个独立 key 配额单元"）。
- **移植要点**：我们"同题竞速"模式要**绕开** Cairn 的 `max_project_workers` 单题上限语义（见 8.5），但保留 `priority` 分层——例如"pwn 题优先给强模型、misc 给弱模型"。

### 7.8 日志状态去抖 + 截断

- **拿**：`_log_changed`（`loop.py:890-898`）+ `preview`（`common.py:27-31`）+ `DispatcherLogFormatter`（`logging.py:6-16`）。
- **为什么**：3 小时长跑，稳定轮询/重复 skip 若刷屏会淹没关键事件（超时/收尾/释放 intent 必须可见，`dispatcher-design.md:25`）。决赛代码审查也看重"日志可读性"。

### 7.9 配置静态校验 + 启动健康检查

- **拿**：`DispatchConfig.load` 的 `model_validator`（`config.py:218-279`）+ `validate_prompt_resources`（`config.py:294-307`）+ `run_startup_healthchecks`（`startup_healthcheck.py`）+ `_validate_server_settings`（`loop.py:906-929`）。
- **为什么**：比赛现场最怕"跑了几分钟才发现 prompt 占位符拼错 / timeout 小于轮询间隔"。Cairn 把这类错误全部前置到启动期。**`intent_timeout > interval` 校验（`loop.py:909-913`）是必抄项**——否则心跳租约还没续上就被服务端超时清空。

### 7.10 mock driver 做离线自测（裁剪版）

- **拿**：`mock.py` 的骨架（`python3 -c` 内联脚本 + `phase` 字段 + 概率/规则驱动，`mock.py:10-148`）+ `dispatch_mock.yaml`。
- **为什么**：真实端点测试赛才知道，**赛前必须用 mock 把整套编排器+黑板+竞速+超时+conclude 路径全跑通**。Cairn 的 `test_mock_end_to_end.py` / `test_scheduler_logic.py` / `test_worker_tasks.py` 就是证据：它把 90% 逻辑做成可无 Docker、无真实模型回归的。我们至少复刻"mock worker 注入假结果"的能力。

---

## 8. 必须改造的机制（渗透+容器+Docker → CTF+API-only+Kali+3h）

### 8.1 执行后端：`ContainerManager` → Kali REST 后端（最核心改造）

- **要改的代码**：`runtime/containers.py`（Docker 全套）+ `process.py`（`docker exec` 流式读）+ 依赖 `docker` 库（`pyproject.toml:14`）。
- **为什么**：无 Docker；我们唯一的命令执行通道是 Kali 的 `POST /api/command`。
- **怎么改（具体设计）**：
  1. 按 `runtime/backend.py:8-41` 的 `ExecutionBackend` Protocol 实现一个 `KaliBackend`，替换 `ContainerManager`/`LocalBackend`（后者仍可留作 Windows 本机回退）。协议方法映射：
     - `container_name(project_id)` → 返回 Kali 上该题的工作目录路径（如 `/work/{challenge_id}`）。
     - `ensure_running(project_id)` → **幂等 no-op 或预热**：调 Kali API 建目录/探测存活（对齐 `local_backend.py:35-39` 的 mkdir 语义）。
     - `build_exec_process(...)` → 返回一个 `KaliExecProcess`，其 `start()` 把命令 POST 给 `/api/command`，`communicate()` 轮询任务结果。
     - `write_text_file(...)` → 把文件内容 base64 + 路径 POST 给 Kali（对齐 `containers.py:197-205` 的 put_archive 语义）。
  2. **关键风险点（需先确认 Kali API 能力）**：
     - 我们现有 `kali.ts` 是"把 pi 的 bash 工具转发到 Kali API"，但 Cairn 的 `ManagedProcess` 需要的是 **Dispatcher 侧直接起进程 + 流式读 stdout/stderr + 超时 kill**（`process.py:60-95`）。若 Kali API 是同步阻塞式（发一条命令等返回），则"超时 + cancel"要么靠 API 提供 task_id + `/cancel`，要么靠 Dispatcher 侧 `TimeoutError` 放弃等待（但 Kali 上进程仍在跑，需 API 支持 kill）。
     - **必须给 Kali API 增加：异步任务 id + 状态查询 + cancel/kill 端点**，否则 `TaskCancellation`（`cancellation.py`）和 `HeartbeatLease` 的"掉心跳即杀进程"（`heartbeat.py:120-123`）都落不了地。
  3. **并发隔离**：Cairn 每项目一个容器天然隔离；我们多 worker 共用一个 Kali，需要 `ensure_running` 为每个 (project_id, worker) 分配**独立工作目录**，并给 bash 命令包一层 `cd <workdir> &&`（pi driver 的 `--session-dir`（`pi.py:68-70`）只隔离 session 不隔离文件系统）。端口冲突（多 worker 同时起反弹 shell/http）要提前用占位/协调，参考 `container/AGENTS.md:22-23` 的"对外 IP/端口"约定，在我们的 `state.json` 里加端口分配表。

### 8.2 工具链与"提交 flag"动作：从 TSEC 硬编码 → Jeopardy 抽象

- **要改的**：`container/Dockerfile`（Kali 工具链 + TSEC 环境）+ `container/.agents/skills/tsec-actions/SKILL.md:11-18`（curl 提交 flag 的 skill）+ `dispatch.example.yaml:5-7`（`common_env` 注入 `TSEC_SERVER_HOST/TSEC_AGENT_TOKEN`）。
- **为什么**：西湖论剑平台 API 端点测试赛才知道，且是 Jeopardy 五类题（不是渗透靶场），flag 提交是"交答案"不是"交 shell 证明"。
- **怎么改**：
  1. 把"提交 flag"做成一个 **pi 工具/skill**（等价于 Cairn 把 curl 提交写成 SKILL.md 喂给 worker），而不是写死在编排器。skill 内容读 `state.json` 里的 `submit_endpoint` + token，**测试赛拿到真实端点后只改一处配置**。
  2. 环境注入沿用 Cairn 的 `common_env` 机制（`config.py:218-246`）：`common_env < worker.env` 覆盖顺序保留，把 `PLATFORM_BASE_URL/PLATFORM_TOKEN` 放 `common_env`。
  3. Kali 工具链：我们 Kali 已有工具，但 **Jeopardy 的 pwn/re 需要编译环境 + pwntools + 调试器**，Cairn 容器只装了 pwntools（`container/Dockerfile:18`），需在 Kali 上补 gcc/gdb/checksec 等；这属于部署侧而非代码改造。

### 8.3 3 小时限时：超时参数 + bootstrap 语义 + 抢分策略

- **要改的**：`tasks.*.timeout` 默认值 + `bootstrap` 的任务定义。
- **为什么**：Cairn 是"渗透一题多步深挖"（explore 600s、bootstrap 300s，`dispatch.local.example.yaml:29-36`），3 小时 Jeopardy 是"多题并行抢分、先易后难、快速试错"。600s 的 explore 在 3 小时里跑不了几轮。
- **怎么改**：
  1. 超时下调：bootstrap/explore 主阶段 90–180s、conclude 30–45s（对齐 `dispatch.local.example.yaml:28-36` 的 reason=45s 思路），且**做成按题类别分档**——web/pwn 长、misc/crypto 短。
  2. `bootstrap` 语义改造：Cairn 的 bootstrap 是"一次性直接解完整题并给 flag+shell 证明"（`prompts/default/bootstrap.md:12-22`），对应 Jeopardy 应改为"**读题面 → 直接解题 → 拿 flag → 交 flag**"；bootstrap 输出契约的 `fact+complete`（`contracts.py:104-132`）可保留，但 `complete.description` 从"证明 goal 达成"改为"flag 已提交成功"。
  3. **增加"题目难度估计/分类路由"**：Cairn 没有"先扫哪道题"的概念（一项目=一题）。我们编排器应新增一个轻量 `triage` 前置步骤（复用 reason 的读图输出契约），在开局一次性给所有题打分，按"预计耗时/预计分数"排序调度——这是 Jeopardy 抢分与 Cairn 深挖的根本差异，**必须新增而非移植**。

### 8.4 人机交互（Hint）：Cairn 是"软提示+下轮生效"，我们要"即时纠偏"

- 详见第 9 节（Hint 机制对比）。核心结论：**必须新增 hint 版本号 + 中断/定向注入**，不能照搬 Cairn 的"写进图等 reason 下一轮吸收"。

### 8.5 同题竞速 vs 协作：调度模型冲突，需双模式

- **冲突点**：Cairn 的 claim 机制（`intents.py:78-93` + `services.py:112-121`）**保证同一条 intent 同一时刻只有一个 worker**，靠"认领→结论"避免重复劳动——这是"协作"模型（stigmergy，`server-protocol.md:19`）。我们已有"同题多 worker 竞速"是**故意让多个 worker 独立解同一题取最快**。
- **怎么改**：把"竞速"和"协作"做成两个显式模式：
  - **竞速模式（初赛默认）**：绕过 intent claim 的排他性，同一题允许多个 worker 各持独立 session 解题，谁先出 flag 谁赢，其余 worker 收到 `cancellation`（`cancellation.py`）停掉。这时 `state.json` 里的 intent 只作"记录"，不作"锁"。
  - **协作模式（决赛/难题）**：完整启用 Cairn 的 claim/lease，多 worker 分工探索同一题的图。
- **为什么**：3 小时初赛"多 worker 竞速"能显著提高单题命中率，但决赛要代码审查 + 可能限并发，协作模式的图审计价值更高。**两个模式复用同一套 `state.json` 数据模型**，差异只在"claim 是否排他"这个开关。

### 8.6 模型切换：只需 pi driver 的 `openai-completions` 分支

- **要改的**：`config.py:20-38` 的 `WORKER_ENV_KEYS`（`pi` 分支已含 `PI_PROVIDER_API`）。
- **结论**：DeepSeek 与 Qwen 都是 OpenAI 兼容，`pi.py:44-51` 的 fallback 分支（`/chat/completions`）直接可用；切换只需改 `PI_BASE_URL/PI_MODEL/PI_API_KEY/PI_PROVIDER_API` 四个 env。**无需写新 driver**。唯一注意：`pi.py:161-189` 的 `models.json` 里 `api` 字段值要与 `PI_PROVIDER_API` 一致（`openai-completions`）。

---

## 9. 应放弃的机制 + 理由

| 机制 | 位置 | 放弃理由 |
|---|---|---|
| Docker 容器模式整套 | `runtime/containers.py`、`runtime/process.py`、`docker` 依赖、`docker-compose.yaml`、`container/Dockerfile` | 无 Docker；由 8.1 的 Kali REST 后端替代 |
| 独立 FastAPI+SQLite Server 的"多消费者并发写图" | `server/` 全部 | 我们已有 `state.json` 黑板 + 单一编排器（单进程），无需再起一个 Server 服务。**但** claim/lease 的原子性要保留——在 `state.json` 上用文件锁/线程锁实现等价语义 |
| 前端图可视化 UI | `server/static/`（cytoscape 全家桶、alpine、index.html） | 决赛要代码审查 ≠ 要实时图 UI；可用极简状态页替代，省掉 10+ 个 vendor 依赖 |
| `completed_action=remove/stop` 容器保留现场 | `containers.py:93-135` | 无容器；改为 Kali 工作目录 `keep/remove`（对齐 `local_backend.py:71-79`） |
| claudecode / codex driver 的 Anthropic/Responses wire API 细节 | `claudecode.py`、`codex.py` | 我们 worker 是 pi；若未来要接 Codex CLI 可保留 codex 骨架，但 claude/codex 的 provider 注入参数大概率用不上 |
| mock 的完整概率+rules 系统 | `config.py:310-376`、`mock.py` | 保留"mock worker 能注入假结果"的能力即可，`fact_ids_gte/lte/open_intents_empty` 规则（`config.py:359-374`）是为 Cairn 的图语义设计的，我们可裁 |
| `reopen`/`external_feedback` 纠错闭环 | `projects.py:303-358` | 服务于"flag 判错→重开项目"；我们的提交闭环由平台判题反馈替代（提交返回 wrong → 直接让 worker 重试），无需图内 `external_feedback` 边 |
| `export?format=timeline` 审计 | `export.py:101-151` | 决赛代码审查要看的是**代码**，不是运行时时间线；若需要赛后复盘可后补 |

**一句话**：放弃"执行/部署/可视化"层，保留"调度/契约/租约/收尾/去重/日志"层。

---

## 10. Hint（人注入意图）机制对比与改进

### 10.1 Cairn 的 Hint 实现（现状）

- 写入：`POST /projects/{id}/hints`（`hints.py:15-25`），存 `content/creator/created_at`（`models.py:32-36`），**active/stopped/completed 都可写**（`services.py:65-69`）。
- 消费：Hint 不进 Fact 因果图，只在"读图"时随项目返回（`projects.py:134-143`）；**下一轮 OODA 循环才被读到**（`server-protocol.md:93`）。
- 触发：Hint 数量增加是 `_reason_trigger` 的三种触发之一（`loop.py:712-713`），会促发一轮 reason 重新判断。
- 注入 prompt：bootstrap 用 `{hints}`（`bootstrap.py:386-398` 序列化 `id/content/creator/created_at`）；**reason/explore 不单独注入 hints，只通过 graph YAML 的 `hints:` 段间接可见**（`export.py:70-78`）。
- 额外用法：Hint 也用于 agent 之间传"态势评估"（"已排除 SSH 和 SQL 注入方向"），降低后续读图认知成本（`server-protocol.md:95`）。

### 10.2 我们现有 hints 文件机制比它差在哪

按用户描述（"黑板 state.json + hints 注入"），推断现状是：**一个静态 hints 文件，编排器启动时读入、拼进 prompt**。差距逐条：

1. **无结构**：没有 `id/creator/created_at`，无法追溯"谁在何时注入"（决赛代码审查/赛后复盘都缺审计）。
2. **无触发**：注入后不会像 Cairn 那样促发"重新 reason/重新规划"；worker 继续按旧计划跑，hint 被吞。
3. **无消费状态**：不知道哪些 hint 已注入给哪个 worker、是否已被采纳，可能重复注入或永远没生效。
4. **静态**：运行中无法追加/定向，人看到卡住想纠偏只能等或重启。
5. **无优先级/定向**：不能区分"全局策略"vs"某题专属"vs"指定 worker"；也不能表达"人类纠偏"高于"agent 自产态势"。
6. **无"态势评估"回流**：没有 Cairn 的"agent 把已排除方向写成 Hint"机制，长 context 里重复走死路。

### 10.3 改进方案（借鉴 Cairn + 补上它的短板）

**目标**：把人机交互从"被动读文件"升级为"带版本、可定向、可触发重规划、可审计"。

1. **结构化 Hint 落进 state.json**（每条题一个 `hints[]`）：字段 `{id, content, creator("human"|"agent:<worker>"), created_at, scope(global|challenge:<id>|worker:<name>), priority(0|1|2), consumed_by:[worker], ack}`。对齐 `models.py:32-36` + 加 scope/priority/ack 三个 Cairn 没有的字段。
2. **Hint 版本号 + 重规划触发**：state.json 里维护 `hint_version`（每次人写 hint 自增）。调度循环借鉴 `_reason_trigger`（`loop.py:704-718`）的"hint_count 增加→触发 reason"逻辑：**检测到 hint_version 变化 → 对受影响的题标记"需要重新 reason/triage"**，并（可选）对正在跑该题的 worker 发 `TaskCancellation.cancel("human_hint")` 走 conclude 收尾，而不是让它按旧方向继续烧时间。
3. **定向 + 优先级注入**：`scope=worker:<name>` 的 hint 只在渲染该 worker 的 prompt 时注入；`priority=0`（人类纠偏）覆盖 `priority=2`（agent 自产态势）。渲染处统一走一个 `format_hints`（对齐 `prompting.py:27-28` + `bootstrap.py:386-398`），**并把 hints 注入扩展到 reason/explore**（Cairn 的 reason/explore 不单独注入 hints 是个明显短板，我们补上）。
4. **消费 ack**：worker 输出里加可选 `consumed_hints:[id]`（或由编排器记录"本次 prompt 注入了哪些 hint"），避免重复注入、支撑"hint 是否生效"的可观测性——这是决赛代码审查会问的"人机交互闭环"证据。
5. **借鉴"态势评估 Hint"回流**：让 reason/conclude 阶段把"已排除方向"写成 `creator="agent:<worker>"` 的 Hint（对齐 `server-protocol.md:95` 的用法），显著降低 3 小时里每个 worker 的 context 成本——这是 Cairn 打满 54 题的一个隐性武器，我们现有 hints 机制完全缺失。

---

## 附录 A：关键文件清单（按移植优先级）

| 优先级 | 文件 | 移植动作 |
|---|---|---|
| P0 | `dispatcher/contracts.py`、`dispatcher/output_parser.py` | 整文件抄 |
| P0 | `dispatcher/tasks/{bootstrap,reason,explore,common}.py` | 抄状态机，替换执行后端调用 |
| P0 | `dispatcher/runtime/heartbeat.py`、`cancellation.py` | 整文件抄，替换 heartbeat callable |
| P0 | `dispatcher/workers/{base,registry,health}.py` + `adapters/pi.py` | 保留 pi，裁 claude/codex |
| P1 | `dispatcher/scheduler/{loop.py,worker_select.py}` | 抄主循环骨架 + 排序，改造调度策略（竞速/协作双模式） |
| P1 | `dispatcher/models.py`（RunningTask/ReasonCheckpoint）、`config.py`（配置模型） | 抄数据模型，裁剪 mock rules |
| P1 | `dispatcher/prompting.py` + `prompts/default/*.md` | 抄渲染 + 改写 prompt 为 Jeopardy 语境 |
| P2 | `dispatcher/logging.py`、`runtime/startup_healthcheck.py` | 整文件抄 |
| P2 | `runtime/backend.py`（Protocol）+ 新写 `KaliBackend` | 抄接口，重写实现 |
| 放弃 | `server/**`、`runtime/containers.py`、`runtime/process.py`、`container/**`、`server/static/**` | 语义迁移到 state.json + Kali 后端 |

## 附录 B：最值得背下来的 5 段代码

1. **超时清空 worker（Server 一致性核心）** `services.py:222-237`。
2. **reason 去重（防重复思考）** `loop.py:704-718`。
3. **双阶段 conclude 收尾（抢救超时探索）** `explore.py:241-288`。
4. **心跳失败即杀进程** `heartbeat.py:88-123`。
5. **Worker 选择排序（并发配额）** `worker_select.py:8-17` + `loop.py:547-607`。

---

*本文档基于对 cairn-ref 全部源码的逐文件精读，所有行号对应当前 checkout 版本（`__version__ = "0.2.1"`）。*
