# Cairn 源码研究报告（oritera/Cairn，AGPLv3）

> 证据基准：`D:\ctf-agent\cairn-ref\cairn\src\cairn`（下文相对路径均以此为根）。
> 阅读日期：以本仓库检出为准（`cairn/__init__.py:1` 版本 `0.2.1`）。

---

## 1. 多任务/多项目调度：如何决定先做哪个任务

**核心结论：** 调度是「全局并发上限 → 项目 round-robin → 项目内固定优先级（bootstrap > reason > explore）→ worker 按 priority 选取」的四层结构，**没有按难度或价值的全局排序**。

### 1.1 全局层：`_dispatch_available`（dispatcher/scheduler/loop.py:191-241）
```python
def _dispatch_available(self, summaries):
    if len(self.futures) >= self.config.runtime.max_workers: ... return   # 全局并发上限
    active = [s for s in summaries if s.status == "active"]               # 只调度 active 项目
    running_projects = self._ordered_projects([s for s in active if s.id in self.runtime_project_ids])
    idle_projects    = self._ordered_projects([s for s in active if s.id not in self.runtime_project_ids])
    dispatched = True
    while dispatched and len(self.futures) < self.config.runtime.max_workers:
        dispatched = False
        for summary in running_projects:                    # 先喂已运行项目（继续推进）
            if self._try_dispatch_project(summary): dispatched = True; ...
        if dispatched: continue
        if self._running_project_count(active) >= self.config.runtime.max_running_projects: return
        for summary in idle_projects:                       # 再启动空闲项目（受 max_running_projects 限制）
            if self._running_project_count(active) >= self.config.runtime.max_running_projects: return
            if self._try_dispatch_project(summary): dispatched = True; break
```
- 约束三档：`max_workers`（全局任务并发）、`max_running_projects`（同时活跃项目数）、`max_project_workers`（单项目并发，loop.py:266-274）。

### 1.2 项目公平轮转：`_ordered_projects`（loop.py:243-252）
```python
def _ordered_projects(self, summaries):
    ids = [s.id for s in summaries]; ids.sort()
    offset = self.project_cursor % len(ids)
    ordered_ids = ids[offset:] + ids[:offset]     # 游标每轮 +1，实现 round-robin
    self.project_cursor += 1
    return [by_id[i] for i in ordered_ids]
```

### 1.3 项目内优先级：`_try_dispatch_project`（loop.py:254-338）
顺序固定的决策链：
1. 初始项目（facts 只有 origin+goal）→ bootstrap 或 reason（loop.py:286-292）；
2. 若 `reason is None` 且存在 `_reason_trigger`（facts/hints/open_intents 变化）→ 先跑 reason（loop.py:293-297）；
3. 否则取**最新**（`max(..., key=lambda i: i.created_at)`）未认领 intent → explore（loop.py:298-318）；
4. 都不可行则跳过（graph_unchanged）。

### 1.4 worker 选择：`_select_worker` + `choose_worker`
- `loop.py:547-607`：先按 `task_types`、`max_running`、`worker_unhealthy_until`、`worker_rejected_until` 过滤，得到候选。
- `scheduler/worker_select.py:8-17`：
```python
def choose_worker(candidates, running_counts):
    grouped = sorted(candidates, key=lambda worker: (
        worker.priority,                       # 升序：数字越小越优先
        running_counts.get(worker.name, 0),    # 同优先级优先选当前负载轻的
        random.random(),                       # 再随机打破平局
    ))
    return grouped
```
- `config.py:176-184` `WorkerConfig` 定义 `name/type/task_types/max_running/priority/env`；`dispatch.example.yaml:55-62` 注释「Worker priority is ascending: lower numbers are preferred first」。
- 设计文档印证：`docs/specs/dispatcher-design.md:934`。

**一句话：** 先满足全局/项目并发上限，用游标对项目做 round-robin，项目内按 bootstrap→reason→explore 固定顺序推进（explore 取最新 intent），worker 按 `priority`（升序）→ 当前负载 → 随机 选取。

---

## 2. 选题/难度评估：有没有 triage

**核心结论：没有 triage、没有难度/价值打分。** 题目由人创建（给 origin + goal + 可选 hints），「选题」被拆成两个自动环节：**bootstrap 首轮直解** + **reason 监督者提 intent**。

### 2.1 人来给题：`POST /projects`（server/routers/projects.py:77-121）
```python
def create_project(body: CreateProjectRequest):
    pid = next_project_id(conn)
    ... INSERT projects (..., status='active', ...)
    ... INSERT facts ('origin', body.origin) / ('goal', body.goal)
    if body.hints: ... INSERT hints ...
```
- `server/models.py:83-88` `CreateProjectRequest{title, origin, goal, bootstrap_enabled=True, hints}`：全是人提供的输入，没有任何「难度」字段。

### 2.2 bootstrap：首轮直接尝试（loop.py:286-292、340-364）
初始项目（facts 仅 origin+goal）优先跑 `bootstrap`（直接让 agent 从 origin 冲到 goal），只有无 bootstrap 能力时才直接进 reason。

### 2.3 选题由 reason 提 intent（prompts/default/reason.md:30-39）
```markdown
- If `Open Intents` is empty, you must propose new intents.
- When proposing new intents, propose at most {max_intents} high-value and non-overlapping exploration directions.
- Each Intent should be a high-value exploration direction. ... main requirement is that each intent is an independent, clearly defined, high-value direction.
```
「high-value / non-overlapping」是 LLM 的语义判断，不是可计算的难度分；`max_intents`（config 默认 2）只是数量上限，不是排序。

**一句话：** 没有 triage 或难度评估模块；选题 = 人给 origin/goal/hints + bootstrap 首轮直解 + reason 用自然语言「high-value」规则提 intent，价值判断完全由 LLM 完成。

---

## 3. reason 任务（监督者）：读什么、输出什么、如何指导 bootstrap/explore

**核心结论：** reason 是纯只读的「监督者」——读图快照，输出三类动作（complete / intents / noop），通过写回服务端（complete 或 create_intent）间接驱动 explore 与完成判定，自身不跑工具。

### 3.1 读什么数据（dispatcher/tasks/reason.py:83-116）
```python
open_intents = [ {id, from, description, worker} for intent in project.intents if intent.to is None ]
allowed_fact_ids = [fact.id for fact in project.facts if fact.id != "goal"]   # goal 不可作为 from
prompt = render_prompt(load_prompt(..., "reason.md"), {
    "graph_yaml": write_graph_snapshot_reference(..., export_yaml.strip(), phase="reason_execute"),
    "fact_ids": format_fact_ids(allowed_fact_ids),
    "open_intents": format_open_intents(open_intents),
    "max_intents": str(config.tasks.reason.max_intents),
})
```
- 输入 = **整张图的 YAML 快照**（facts + hints + intents 的因果链，见 export.py:50-98）+ 合法 fact id 列表 + 未结论的 open intents + max_intents。
- reason 在容器内只拿一份写到文件里的 graph YAML（write_graph_snapshot_reference），不直接操作服务端状态。

### 3.2 输出契约（prompts/default/reason.md:15-28）
```json
{"accepted": false, "reason": "..."}                                        // 拒绝（prompt 明令"不得拒绝"）
{"accepted": true, "data": {"complete": {"from": ["f001"], "description": "..."}}}   // Goal 已满足
{"accepted": true, "data": {"intents": [{"from": ["f001"], "description": "..."}]}}  // 提议新方向
{"accepted": true, "data": {}}                                              // 不提议（noop）
```
- 规则（reason.md:31-39）：先判 goal 是否满足；open intents 为空则**必须**提议新 intent；有多个 open intents 且没有更有价值方向时可返回空 data。

### 3.3 如何指导 bootstrap/explore（reason.py:176-273）
```python
kind, data = validate_reason_payload(payload, open_intents_empty=..., max_intents=...)
if kind == "rejected": return "rejected"
if kind == "complete":
    response = client.complete(project.id, data["from"], data["description"], worker.name)   # 完成项目
if kind == "intents":
    for intent_data in data:
        response = client.create_intent(project.id, intent_data["from"], intent_data["description"], worker.name)
        # 409 丢竞争 continue；403 项目失活返回 success；其它失败 continue
```
- reason 产出的 intent 会被 explore 认领执行（loop.py:298-318）；reason 判定 complete 则走 `/complete` 把项目置为 completed（projects.py:257-300）。
- reason 自身有专属 lease（`HeartbeatLease.for_reason`，reason.py:45）+ 完成/失败后释放（reason.py:282-284）。

**一句话：** reason 只读图 YAML 快照 + 合法 fact + open intents，输出 complete/intents/noop 三种动作，靠 `create_intent` 给 explore 供料、靠 `complete` 收束项目，是标准的分层监督模块。

---

## 4. explore 双阶段 conclude：超时/解析失败后同 session 收尾（~241-288）

**核心结论：** 主阶段（execute）超时、命令失败或 JSON 解析失败时，**复用同一个 worker session** 追加一次 `explore_conclude` 阶段，让 agent 停止探索、把已确认结论总结成 fact；两阶段都有独立超时。

### 4.1 触发点（dispatcher/tasks/explore.py:142-206）
```python
if not did_timeout(first) and first.returncode == 0:
    try:
        payload = parse_json_output(model_output); kind, description = validate_explore_payload(payload)
    except Exception as exc:
        return _try_conclude_fallback(..., session, ...)   # 解析失败 → 收尾
    ...
if did_timeout(first):
    return _try_conclude_fallback(..., session, ...)       # 超时 → 收尾
```

### 4.2 收尾流程 `_try_conclude_fallback`（explore.py:241-384）
```python
def _try_conclude_fallback(..., session, lease, cancellation):
    if not driver.supports_conclude() or not session:          # 前置：driver 支持 + 有 session
        best_effort_release(...); return "failed"
    if lease.failure is not None: ... return "failed"          # 心跳已丢则不再收尾
    if cancellation.is_cancelled: ... return "cancelled"       # 已取消则不再收尾
    if not project_allows_conclude_fallback(client, project_id, ...):  # 项目非 active 则放弃
        ... return "failed"
    container_name = container_manager.ensure_running(project_id)
    prompt = render_prompt(load_prompt(..., "explore_conclude.md"),
        {"graph_yaml": ..., "intent_id": intent.id, "intent_description": intent.description})
    conclude_argv = driver.build_conclude(worker, prompt, session)     # 关键：沿用同一 session
    result = _run_process(..., phase="explore_conclude", timeout=config.tasks.explore.conclude_timeout, ...)
    ... parse_json_output + validate_explore_payload ...
    return write_conclude_result(..., source="explore_conclude", ...)
```
- **同 session 的关键**：主阶段的 `session = driver.extract_session(session, first.stdout, first.stderr)`（explore.py:118）被原样传入 `_try_conclude_fallback`（explore.py:159/206），`driver.build_conclude(worker, prompt, session)` 复用；`bootstrap.py` 走完全对称的 `_try_conclude_fallback`（bootstrap.py:234 起）。
- prompt 层面配合（prompts/default/explore.md:19、bootstrap.md:19）：*"If you later receive a conclude-phase instruction in the same session, that newer conclude instruction overrides this keep-working rule immediately."*

### 4.3 超时预算（dispatch.example.yaml:26-41）
`bootstrap/explore.timeout: 300`（主阶段）、`conclude_timeout: 90`（收尾阶段）；两阶段独立计时、独立超时。

**一句话：** execute 超时/解析失败后，若 driver 支持 conclude 且 session 仍在、心跳未丢、未取消、项目仍 active，就用**同一 session** 追加 conclude 阶段（独立 90s 超时），把已确认结果总结成一个 fact 写回。

---

## 5. hints 结构化：id/creator/版本/ack 与触发重规划

**核心结论（重要校正）：** 本代码库的 Hint **只有 4 个字段 `{id, content, creator, created_at}`，不存在 `version` 和 `ack` 字段**。「触发重规划」不是靠 hint 的 ack，而是靠 **hint 计数变化触发 reason 重跑**；「flag 提交失败需纠正」走的是另一条 `reopen`（external_feedback）通道。

### 5.1 数据结构（server/models.py:32-37）
```python
class Hint(BaseModel):
    id: str
    content: str
    creator: str
    created_at: str
```
- id 由 `next_hint_id` 生成：`server/services.py:47-48` → `_next_scoped_id(conn, "hint", "h", project_id)`，项目内自增，形如 `h001`（services.py:20-36）。
- 创建端点 `POST /projects/{pid}/hints`：`server/routers/hints.py:10-25`，`check_project_hint_writable`（services.py:65-69）允许对 active/stopped/completed 项目写 hint。

### 5.2 hint 如何进入 agent 视野
- 进图快照：`server/routers/export.py:70-78` 把 hints 写成 YAML `hints: [{content, creator, created_at}]`；timeline 导出（export.py:116-120）为 `HINT by {creator}`。
- 进 bootstrap prompt：`dispatcher/tasks/bootstrap.py:384-399` `_bootstrap_prompt_replacements` 把 hints 序列化为 JSON `{id, content, creator, created_at}`，喂给 `bootstrap.md` 的 `{hints}` 占位符（bootstrap.md:36-39）。explore/reason 通过图 YAML 间接看到 hint。

### 5.3 「触发重规划」= hint 计数变化触发 reason（dispatcher/scheduler/loop.py:704-718）
```python
def _reason_trigger(self, project):
    checkpoint = self.reason_checkpoints.get(project.project.id)
    changes = []
    if len(project.facts) > checkpoint.fact_count: changes.append(f"facts:{...}")
    if len(project.hints) > checkpoint.hint_count: changes.append(f"hints:{...->...}")   # hint 增加 → 触发
    if checkpoint.open_intent_count > 0 and open_intent_count == 0: changes.append("open_intents:...->0")
    if not changes: return None
    return ",".join(changes)
```
- 配套的 checkpoint（`dispatcher/models.py:14` `ReasonCheckpoint`；更新于 loop.py:765-773）记录 `fact_count / hint_count / open_intent_count`，即「版本」实质上是服务端各表的计数快照，而非 hint 自身的 version 字段。
- hint 增加时 `_try_dispatch_project` 会优先跑 reason（loop.py:293-297），让监督者结合新线索重新提议 intent（这就是"触发重规划"）。

### 5.4 没有 ack；外部反馈走 reopen
- grep 全仓库 `ack` 无 Hint 相关语义。用户视角的「提交的 flag 是错的」这条反馈在 `POST /projects/{pid}/reopen`（server/routers/projects.py:303-358）：删除完成边、新增一条 `description` 为反馈内容的 fact + 一条 `external_feedback` 意图，项目回到 active 重新调度（`services.py` 无 ack 字段；设计文档 `docs/specs/server-protocol.md:606、862`）。

**一句话：** hint = `{id(项目内自增 hN), content, creator, created_at}`，无 version/ack 字段；「重规划」由 `_reason_trigger` 监测 `hint_count` 增加而重跑 reason 实现，flag 错误这类外部反馈改由 `reopen` 的 external_feedback 边承担。

---

## 6. 执行后端协议：runtime/backend.py 的 Protocol 与等价 Kali REST API 所需字段

**核心结论（分层校正）：** `backend.py` 里的 `ExecutionBackend` Protocol **不是** HTTP 任务协议，而是「执行基板」接口（容器/进程粒度）；「启动任务/查状态/取消/心跳」这套语义分布在三个层面：**① 进程协议 ExecProcess**、**② dispatcher↔server 的 HTTP 协议（client.py + routers）**、**③ 心跳/取消的运行时设施**。

### 6.1 执行基板 Protocol（dispatcher/runtime/backend.py:8-41）
```python
@runtime_checkable
class ExecutionBackend(Protocol):
    def container_name(self, project_id: str) -> str: ...      # 项目 → 容器名
    def ensure_running(self, project_id: str) -> str: ...      # 启动/复用容器（≈启动任务的宿主）
    def build_exec_process(self, container_name, env, command, timeout_seconds=None, kill_after_seconds=5) -> ExecProcess: ...  # 构造任务进程
    def write_text_file(self, container_name, path, content) -> None: ...   # 注入文件（graph yaml 等）
    def needs_completed_cleanup / needs_stopped_cleanup / cleanup_completed / cleanup_stopped: ...   # 清理
    def close(self) -> None: ...
```
两个实现：`ContainerManager`（每项目一个 Docker 容器）与 `LocalBackend`（主机子进程），通过 `runtime.execution` 切换。

### 6.2 进程协议（dispatcher/runtime/process.py:27-41）
```python
class ExecProcess(Protocol):
    def start(self) -> None: ...                       # 启动任务
    def communicate(self, timeout) -> ProcessResult: ...  # 等待/查状态（超时→kill）
    def kill(self) -> None: ...                        # 杀进程
    def cancel(self, reason) -> None: ...              # 带原因的取消
```
`ProcessResult{returncode, stdout, stderr, timed_out, cancelled, cancel_reason}`（process.py:17-24）。

### 6.3 HTTP 协议：任务认领/心跳/取消/状态（dispatcher/protocol/client.py:75-129 + routers）
| 语义 | 客户端方法 | 端点 | 关键字段 |
|---|---|---|---|
| 认领 intent（≈启动 explore/bootstrap） | `heartbeat` (client.py:75-80) | `POST /projects/{pid}/intents/{iid}/heartbeat` | `{worker}`（intents.py:74-93；认领=写 worker+last_heartbeat_at） |
| 认领 reason | `claim_reason` (client.py:82-87) | `POST /projects/{pid}/reason/claim` | `{worker, trigger}`（projects.py:191-216） |
| 心跳（续约） | `reason_heartbeat` (client.py:89-94) | `POST /projects/{pid}/reason/heartbeat` | `{worker}`（projects.py:219-237） |
| 释放/取消认领 | `release` / `release_reason` (client.py:96-108) | `/intents/{iid}/release`、`/reason/release` | `{worker}`（intents.py:96-115；projects.py:240-254） |
| 写回结论 | `conclude` (client.py:110-115) | `/intents/{iid}/conclude` | `{worker, description}` → 新建 fact（intents.py:118-147） |
| 完成项目 | `complete` (client.py:117-122) | `/projects/{pid}/complete` | `{from[], description, worker}`（projects.py:257-300） |
| 查状态 | `list_projects` / `get_project` (client.py:51-59) | `GET /projects`、`GET /projects/{pid}` | 返回 facts/intents/hints/reason |

### 6.4 心跳与取消运行时
- `dispatcher/runtime/heartbeat.py:23-123` `HeartbeatLease`：后台线程按 `interval` 发心跳；`403/409` 立即失败、瞬时失败有 `interval*2` 宽限（heartbeat.py:94-110）；失败时 `_fail` 会 `process.kill()`（heartbeat.py:112-123）。
- `dispatcher/runtime/cancellation.py:8-38` `TaskCancellation`：`cancel(reason)` 幂等、`attach_process` 后取消会连带杀进程。
- 服务端超时回收：`expire_workers`（services.py:222-237，超 `intent_timeout` 清 worker）、`expire_reason_leases`（services.py:240-257，超 `reason_timeout` 清 reason claim）。

**若在 Kali REST API 上实现等价物，需要的字段/语义：** 任务对象 `{id, project_id, status(open/claimed/concluded), worker, last_heartbeat_at, from[], description, creator}`；认领即写 `worker`+`last_heartbeat_at`（幂等、409 表示已被他人认领）；心跳续租，服务端按超时阈值把过期 claim 清空；释放/取消清 `worker`；结论写回生成新 fact；`complete` 需 `{from[], description, worker}` 并把项目置 completed。执行侧用 `ensure_running`（建/复用隔离环境）+ `build_exec_process`（`timeout -k` 包装命令）+ `write_text_file`（注入上下文）+ `ExecProcess.start/communicate/kill/cancel`。

**一句话：** backend.py 是执行基板（容器/进程）协议；任务级「认领=写 worker+心跳、查状态=GET、取消=release/清 worker、完成=conclude/complete」在 client.py + routers 的 HTTP 层，心跳租约与超时回收在 heartbeat.py/services.py，做 Kali REST 等价物需复刻这三层字段与幂等/超时语义。

---

## 7. 容器隔离：ContainerManager 如何建容器、挂载、限资源、防污染

**核心结论：** 隔离粒度是「**一个项目一个 Docker 容器**」，容器名带项目 id、以 `sleep infinity` 常驻；文件通过 `put_archive`（tar）注入；资源限制只有**超时 + cap_add + network_mode**，**没有内存/CPU 配额**。

### 7.1 建容器（dispatcher/runtime/containers.py:31-72）
```python
_PREFIX = "cairn-dispatch-"
def container_name(self, project_id): return f"{self._PREFIX}{project_id.replace('/','-')}"   # 项目 id 进容器名
def _ensure_running_locked(self, project_id, name):
    state = self.inspect_state(name)
    if state == "running": return name                       # 复用
    if state is not None: self._start_existing(name); return name
    self._client.containers.run(self._config.image, ["sleep", "infinity"],
        detach=True, name=name,
        network_mode=self._config.network_mode,              # 默认 host（dispatch.example.yaml:46）
        cap_add=self._config.cap_add or None)                # 按需加 NET_RAW/NET_ADMIN
```
- `ensure_running` 用 per-name 线程锁防并发重复创建（containers.py:35-38、74-80）。

### 7.2 挂载/写文件（containers.py:197-205、238-269）
```python
def write_text_file(self, container_name, path, content):
    archive_path, archive = self._text_file_archive(path, content)   # 打包成 tar
    container.put_archive(archive_path, archive)                     # Docker put_archive 注入
```
- `_text_file_archive` 校验绝对路径、逐段建目录（0755）、文件 0644，防路径穿越（containers.py:238-269）。graph yaml 快照即通过此通道写进容器再喂给 agent。
- 注意：**没有 bind mount 宿主机目录**，工作目录是镜像内固定的 `/home/kali/workspace`（container/Dockerfile:80、91）。

### 7.3 限资源
- 命令用 `timeout -k kill_after timeout` 包装（containers.py:175-195），是**唯一的时间配额**。
- 能力与网络：`cap_add`（默认空）、`network_mode`（默认 host）来自 `ContainerConfig`（config.py:153-157）。
- **未发现 `mem_limit` / `nano_cpus` / pids_limit 等资源配额**——这是与「防互相污染」相关的真实缺口。

### 7.4 防互相污染 / 清理
- 项目 id → 独立容器名，天然隔离各项目文件系统（containers.py:31-33）。
- 清理：`cleanup_completed`（按 `completed_action` stop 或 remove，containers.py:93-119）、`cleanup_stopped`（containers.py:121-135）、`cleanup_orphan`（containers.py:137-150）；调度器在项目变 completed/stopped 后排队清理（loop.py:784-834），项目 inactive 时取消在跑任务并停容器（loop.py:845-856）。
- 隔离边界明确写进设计文档：`docs/specs/dispatcher-design.md:27`（非 active 即硬停止：取消任务、停容器、杀 agent 进程）。

**一句话：** 每项目一个独立容器（`cairn-dispatch-<project_id>`，`sleep infinity` 常驻），文件用 `put_archive` tar 注入、无 bind mount，时间配额靠 `timeout -k`、能力靠 `cap_add`、网络靠 `network_mode`，但**没有内存/CPU 硬配额**，隔离主要靠「每项目一容器 + 项目级 scoped id + inactive 即清理」。

---

## 8. flag 提交：TSEC curl？重试/冷却/防重复？

**核心结论：** flag 提交**不在 Cairn 的 Python 代码里**，而是容器内给 agent 用的 **skill（tsec-actions）**，让 LLM worker 自己 `curl` 调 TSEC 接口；**没有重试/冷却/防重复机制**，只有两条自然语言规则。

### 8.1 提交方式（container/.agents/skills/tsec-actions/SKILL.md:1-23）
```markdown
name: tsec-actions
description: 在 tsec CTF/智能渗透比赛中，当需要对某道题提交 flag 时使用
## 用法
* 直接使用 curl 调 API，TSEC_SERVER_HOST 和 TSEC_AGENT_TOKEN 已存在于环境变量
* flag 统一都是 flag{...} 格式
提交 flag：
curl -X POST "http://${TSEC_SERVER_HOST}/api/submit" \
  -H "Agent-Token: ${TSEC_AGENT_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"code": "<challenge_code>", "flag": "<flag>"}'
## 规则
- 只在高置信时提交 flag，不要把它当成爆破接口
- 提交 flag 以接口返回结果为准，不要自行假设提交成功
```
- 环境变量注入：`dispatch.example.yaml:5-7`（`TSEC_SERVER_HOST`、`TSEC_AGENT_TOKEN`，经 `common_env` 合并进每个 worker）；mock 用 `dispatch_mock.yaml:31-33`（`TSEC_BASE_URL`、`TSEC_AGENT_TOKEN`）。skill 实际用的变量名是 `TSEC_SERVER_HOST`。

### 8.2 重试/冷却/防重复
- 对 `cairn/src` 全量 grep `submit|cooldown|retry|dedup|curl`：**无任何重试/冷却/去重代码**（唯一匹配是容器 Dockerfile 里装 aliyuncli 的 `curl`）。
- 唯一「防滥用」是 skill 里两句 prose 约束（高置信才提交、以接口返回为准），执行正确性完全依赖 LLM 自觉。

**一句话：** flag 提交 = 容器内 `tsec-actions` skill 让 agent 用 `curl -X POST ${TSEC_SERVER_HOST}/api/submit` + `Agent-Token` 头 + `{code,flag}` 完成，环境变量由 `common_env` 注入；**没有重试、冷却或防重复逻辑**，仅靠提示词的软约束。

---

## 9. 输出解析与契约：output_parser.py + contracts.py 完整机制

**核心结论：** 解析层做「多候选 JSON 提取（先整个文本/围栏块，再逐 `{` 扫描 `raw_decode`）」，契约层做「`{accepted,data}` 信封解包 + 各任务 schema 白名单校验」，**非法即抛 ValueError → 任务判 failed（explore/bootstrap 再进入 conclude fallback）**。

### 9.1 多候选 JSON 提取（dispatcher/output_parser.py:8-47）
```python
FENCED_BLOCK_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.IGNORECASE | re.DOTALL)

def extract_json_object(text):
    decoder = json.JSONDecoder(); seen = set()
    for candidate in _candidate_segments(text):          # 候选 = 整段文本 + 每个 fenced block
        segment = candidate.strip()
        if not segment or segment in seen: continue      # 去重
        seen.add(segment)
        try:
            parsed = json.loads(segment)                 # 优先整段直接解析
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(parsed, dict): return parsed
        for start in _object_start_positions(segment):   # 失败则从每个 '{' 位置 raw_decode
            try: parsed, _ = decoder.raw_decode(segment[start:])
            except json.JSONDecodeError: continue
            if isinstance(parsed, dict): return parsed
    raise ValueError("no JSON object found in output")
```
- `_candidate_segments`（40-43）：`[整个文本] + [每个 ```json 围栏块内容]`；`_object_start_positions`（46-47）：文本中每个 `{` 的下标。返回第一个能解出的 **dict**，否则抛错。

### 9.2 契约层（dispatcher/contracts.py）
- 入口：`parse_json_output(stdout) = extract_json_object(stdout)`（contracts.py:8-9）。
- 信封解包 `_unwrap_wrapped_payload`（contracts.py:12-21）：读 `accepted`；`False`→rejected；`True`→要求 `data` 必须是 dict；`None`（没写 accepted）→ 走「裸 payload」识别。
- 裸 payload 识别 `_looks_like_*`（contracts.py:28-59）：按精确 key 集合判定类型（如 reason=`{"complete"}`/`{"intents"}`/`{"intent"}`，bootstrap_execute=`{"fact","complete"}`，explore=`{"description"}`），不匹配即「accepted must be true or false」抛错。
- 各校验器（contracts.py:62-170）：
  - `validate_reason_payload`：complete 与 intents 不能共存；intents 必须非空数组且每项含 `from`+`description`；`max_intents` 截断；open_intents 为空时禁止空 intents；兼容单数 `intent`（76-80）。
  - `validate_bootstrap_execute_payload`：`fact.description` 与 `complete.description` 均必填非空。
  - `validate_bootstrap_conclude_payload`：额外 key 一律拒绝（145-147）；`fact.description` 必填。
  - `validate_explore_payload`：只认 `description` 非空字符串。

### 9.3 非法即拒的执行路径
- reason：`reason.py:176-193` parse/validate 异常 → 记日志返回 `"failed"`（reason 无 conclude fallback）。
- explore：`explore.py:142-172` 解析异常 → `_try_conclude_fallback`（同 session 收尾，见第 4 节）；`kind == "rejected"` → release 返回 `"rejected"`（173-184）。
- bootstrap：对称处理（bootstrap.py:136-165 等）。
- rejected/unhealthy 的 worker 会被调度器短暂拉黑（`UNHEALTHY_RETRY_AFTER_SECONDS=5`、`REJECTED_RETRY_AFTER_SECONDS=5`，loop.py:28-29、742-764）。

**一句话：** output_parser 用「整段 + 围栏块 + 逐 `{` raw_decode」多候选提取第一个合法 JSON dict，contracts 用 `{accepted,data}` 信封 + 精确 key 白名单逐任务校验，任何不合法都抛 ValueError → 任务 failed/rejected（explore/bootstrap 再落到同 session 的 conclude fallback），被拒 worker 进入 5s 拉黑窗口。

---

## 附：关键文件索引

| 主题 | 文件 |
|---|---|
| 调度主循环 | `dispatcher/scheduler/loop.py` |
| worker 选取 | `dispatcher/scheduler/worker_select.py` |
| 配置（并发/优先级/超时） | `dispatcher/config.py`、`dispatch.example.yaml` |
| reason 监督者 | `dispatcher/tasks/reason.py`、`dispatcher/prompts/default/reason.md` |
| bootstrap/explore 双阶段 | `dispatcher/tasks/bootstrap.py`、`dispatcher/tasks/explore.py` |
| hints | `server/models.py`、`server/routers/hints.py`、`server/routers/export.py` |
| 执行基板 | `dispatcher/runtime/backend.py`、`process.py`、`containers.py`、`local_backend.py` |
| HTTP 协议 | `dispatcher/protocol/client.py`、`server/routers/{intents,projects,export,hints}.py` |
| 心跳/取消 | `dispatcher/runtime/heartbeat.py`、`cancellation.py` |
| 输出契约 | `dispatcher/output_parser.py`、`dispatcher/contracts.py` |
| flag 提交 skill | `container/.agents/skills/tsec-actions/SKILL.md` |
| 设计文档 | `docs/specs/dispatcher-design.md`、`docs/specs/server-protocol.md` |
