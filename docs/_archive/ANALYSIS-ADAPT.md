# 外部作品代码级适配分析总报告（v1，持续增补）

> 目的：把"idea 来源"从二手摘要升级为**逐文件源码级**适配依据。
> 方法：每个作品 = 子代理全量精读 + 我本人抽核关键文件（read 原文、对照行号）。
> 引用格式：`文件:行号`，全部对应本地 checkout。

---

## 一、Cairn（oritera/Cairn，TCH 唯一 AK，AGPLv3）

### 1.1 架构事实（已核验）
- 四组件：Server（图一致性，不推理）/ Dispatcher（调度+写回）/ 每项目容器 / Worker CLI（claude/codex/**pi**）
- 三任务 OODA：bootstrap（直接试解）→ reason（读图决策）→ explore（单方向深挖），全部返回**单 JSON 对象**
- 双阶段 conclude：超时/解析失败 → 杀进程 → **同 session 总结收尾**（explore.py:241-288）——超时不丢成果
- 调度四层并发 + Worker 排序 `(priority, running_count, random)`（worker_select.py:8）
- 失败 5 态（success/failed/cancelled/unhealthy/rejected），不立即重试

### 1.2 可整抄（已由我本人核验原文）
| 文件 | 要点 | 核验 |
|---|---|---|
| `output_parser.py`（47 行） | 多候选 JSON 提取：整段+代码块+每个 `{` 起 raw_decode | ✅ 已读 |
| `contracts.py`（170 行） | 输出契约校验：accepted/data 包裹、单复数兼容、max_intents 截断、非法即拒 | ✅ 已读 |
| `adapters/pi.py`（232 行） | **pi 用 `--mode json` 跑，从事件流提取文本**（extract_response_text:130-159）；每 worker 独立 models.json（_models_json:215-232） | ✅ 已读 |
| `runtime/local_process.py`（135 行） | 独立进程组 + SIGTERM→宽限→SIGKILL 组杀（**解决我们孤儿 worker 问题**）+ 双线程流式排水 | ✅ 已读 |
| `runtime/heartbeat.py` | 心跳租约，掉心跳杀进程 | 子代理核验 |

### 1.3 必须改（5 项，具体设计）
1. **ContainerManager → KaliBackend**：按 `runtime/backend.py` Protocol 重写；Kali REST API 需补 task_id+状态查询+cancel 三个能力（否则超时/心跳/cancel 全部落不了地）
2. **提交抽象**：Cairn 的 flag 提交是 TSEC 专用 curl；我们的 Jeopardy 提交做成独立工具（dasctf_client），端点只改配置
3. **3 小时限时语义**：任务超时下调到 90–180s；bootstrap 语义改成"读题→解题→交 flag"；**新增 triage 难度排序**（Jeopardy 抢分 vs Cairn 深挖是根本差异，Cairn 完全没有这个）
4. **竞速 vs 协作双模式**：同题竞速（我们的现状）与 Cairn 的 claim 排他协作是调度模型冲突；做成双模式开关，共用 state.json
5. **Hint 结构化**：Cairn 的 hint 有 id/creator/时间且**新增 hint 触发 reason 重规划**（loop.py:712）；我们的静态 hints 文件改成 结构化+版本+定向+ack+运行中可追加

### 1.4 应放弃
独立 Server 服务、Docker 全套、cytoscape 前端、reopen 闭环、claude/codex driver。

### 1.5 关键发现：v4-pro 空输出问题有解了
Cairn 跑 pi 用 `--mode json`（不是 print）+ extract_response_text 从 turn_end/agent_end 事件提取 text 部件。我们的 print 模式拿不到 reasoning 模型最终文本 → **换 json 模式 + 事件提取**即为修复路径（待验证）。

---

## 二、verialabs/ctf-agent（BSidesSF 2026 52/52 冠军）

### 2.1 架构事实（子代理全量精读 + 我抽核）
- coordinator 与 swarm 解耦：coordinator_loop.py 事件循环只拼文本；工具（coordinator_core.py do_*）是改状态唯一入口
- ChallengeSwarm：每题 N 模型 `asyncio.wait(FIRST_COMPLETED)` 竞速；"Cost is not a concern / NEVER kill swarm" 哲学
- message_bus：append-only findings + per-model 游标 + check() 只回传"别人"的未读（防回声）——**已核验全 54 行**
- 提交纪律（swarm.py:153-192）：**已核验**——递增冷却 [0,30,120,300,600]s 按模型计、精确去重、flag 锁

### 2.2 可移植 10 项（按价值）
① message bus（落盘版）② 提交纪律去重+递增冷却 ③ LoopDetector（已核验）④ "只允许 flag_found"约束 ⑤ JSONL 轨迹 ⑥ broken-solver 检测 ⑦ poller 防抖（poller.py:91-98）⑧ 工具纯函数分层 ⑨ 首动作强制连服务 ⑩ 自建计费

### 2.3 Docker→Kali 的差异改造
- 无容器隔离 → `/root/ctf/<cid>/w<idx>/` 三级命名空间做并发/文件隔离
- tar 通道 → base64 二进制安全；container delete 兜底 → Kali 进程组 setsid+timeout 清理
- **关键反转**：prompts.py 的 localhost→host.docker.internal 改写必须去掉（我们没有 Docker 网络层）

### 2.4 3 小时限时冲突
- "NEVER kill swarm / 无限预算"直接冲突 → 全局倒计时 + 先易后难降档竞速（flash/low 抢 easy、pro/high 攻坚）
- 提交冷却压缩为 [0,15,60,180]

### 2.5 平台抽象
- poller 的 diff+防抖平台无关可原样复用；ctfd.py 换成 Platform 接口 + SubmitResult 归一化（correct/already_solved/incorrect/unknown）
- 放弃：Claude SDK/codex JSON-RPC/多云适配/podman 嵌套容器/HTML 登录抓取/ARM64 镜像

---

## 三、Koshary + LLM-CTF-Solver

### 3.1 Koshary（CTFd/HTB 多 agent 编排）
- 单文件编排器 + `platforms/` 抽象层；静态题型路由表（config.json:68-89）
- 每题工作区 + `.lock` 锁 + plan 阶段 + StateDB 黑板（orchestrator.py:467-569）
- flag 抽取/占位过滤（core/flag_extractor.py:31-64）、提交限错+去重
- **可抄**：`BasePlatform` 平台抽象（我们对 DASCTF 未知 API 该照此做）、提交去重、工作区锁
- **改/弃**：它的 gemini/codex/claude CLI 依赖全弃（我们走 DeepSeek API）；CTFd CSRF nonce 逻辑等平台确认后再说

### 3.2 LLM-CTF-Solver（国内 ReAct 工程范本）
- **三层解析回退**（solve_agent.py:863-1013）：原生 tool_calls → 堆栈法 JSON → XML → general_next —— **可整抄**（我们 pi worker 无此层，输出不稳时直接丢题）
- **6 维僵局检测**（solve_agent.py:1393-1479）—— **已由我亲自核验**：D2 最近 4 工具≥3 相同 / D3 输出相同或相似度>0.85 / D4 同思路≥3 次 / D6 错误率≥60%（纯规则）/ D1 LLM 标记+规则信号（第 5 步后生效）/ D5 步数≥15 无进展。可移植到编排器做"kill+重派"判据
- **三层记忆**（memory.py:36-40 + 关键事实防丢 :257-290）：3 小时限时下**只保留精简版**（去重+关键事实防丢+失败计数），砍掉每步 journal 二次 LLM 叙事和 ChromaDB RAG
- **checkpoint 断点续跑**：可抄（按题 MD5 存档、恢复重跑 step-0）
- **Kali SSH → 我们的 Kali REST**：transport 全换，接口对齐 ssh_client.py
- **3h 阈值收紧**：stuck 5→3、consecutive_none 5→3、总步数 100→12~15、逐题 deadline
- **弃**：渗透模式全套、first_blood brute-first、语义缓存 L2、ChromaDB

### 3.3 3 小时限时的价值排序（结论）
解析回退 > 僵局检测 > 精简记忆（防丢+去重）> checkpoint >> RAG/多叙事（放弃）

---

## 四、综合架构 v2 定稿（14 项改动，全部有源码出处）

### 4.1 编排器改造（ctf_orchestrator.py）
1. **平台层重构**：dasctf_client → Koshary `BasePlatform` 接口（NormalizedChallenge/SubmitResult），Mock 与 DASCTF 两适配器同接口（已核验 platforms/base.py）；poller diff+防抖复用（verialabs poller.py:85-120）
2. **进程管理**：Cairn local_process.py → Windows 等价（进程组 + 组杀），消灭孤儿 worker（已核验）
3. **僵局双层防御**：编排器层 LLM-CTF 6 维判据（收紧：stuck 3、步数 12~15、逐题 deadline 90-180s，已核验 :1393-1479）；worker 层 verialabs LoopDetector 警告注入（已核验 loop_detect.py）+ broken-solver 检测
4. **输出解析三层回退**：原生 JSON → fenced/raw_decode（Cairn output_parser.py，已核验）→ 文本兜底
5. **双阶段 conclude**：超时不丢成果，同 session 收尾总结（Cairn explore.py 模式）
6. **Hint 结构化**：版本+定向+优先级+ack；新增 hint 触发立即重规划 + 取消在跑 worker（Cairn loop.py:712 语义）
7. **竞速/协作双模式**：开关配置共用 state.json；竞速模式加 message bus 落盘版（verialabs message_bus.py，已核验，防回声游标）
8. **triage 难度排序**：先易后难（3h 抢分核心，Cairn 完全没有、必须新增）

### 4.2 worker 侧改造（run-pi + kali.ts）
9. **换 --mode json** + Cairn extract_response_text 事件提取（修 v4-pro 空输出，已核验 pi.py:130-159）
10. **每 worker 独立 models.json + 工作区**（Cairn _models_json 模式 + verialabs 三级命名空间 /root/ctf/<cid>/w<idx>/）
11. **结构化输出约束**：只接受 flag_found（verialabs output_types.py:11-26）+ 首动作强制连服务

### 4.3 提交与轨迹
12. **提交纪律升级**：去重 + 递增冷却 [0,15,60,180]s 按模型计（verialabs swarm.py:153-192，已核验；3h 压缩版）
13. **JSONL 轨迹落盘**（verialabs tracing.py）+ 精简记忆（LLM-CTF 关键事实防丢，砍 RAG/二次叙事）+ 成本统计

### 4.4 Kali 桥改造
14. Kali REST 补 task_id+状态查询+cancel；或换 SSH 通道（待用户提供凭据评估）

### 4.5 实施顺序（倒排 8/18）
- P0（8/15 今天）：9（json 模式）、2（进程组杀）、12（提交纪律）
- P1（8/16）：1（BasePlatform）、3（僵局双层）、4（解析回退）
- P2（8/17）：8（triage）、5、6、10、11、13 + 全链路复测
- 测试赛后：14、7、其余

### 4.6 明确放弃（四大源共同结论）
独立 Server 服务、Docker 全套、ChromaDB RAG/auto_learn、渗透模式、语义缓存、claude/codex SDK、无限预算哲学、cytoscape 前端。

---

## 五、自检修正（2026-08-15，对照官方考察点）

### 5.1 许可证合规（自检发现，约束一切"抄"）
- Cairn AGPLv3 / Koshary 无 LICENSE → **只借鉴思想、自己重写**，不复制不逐行翻译
- verialabs MIT / LLM-CTF-Solver Apache 2.0 → 可复用（保留版权声明）
- pi MIT ✓

### 5.2 官方考察点对照（覆盖率 60% → 补齐后目标 95%+）
官方原文关键词：设计/训练/调度/优化；大量任务/高频反馈/有限时间；复杂问题拆解/自动化处理；
AI 的 信息理解/路径规划/批量尝试/代码生成/日志分析/持续迭代；人类的 目标设定/策略判断/过程监督/结果复核。

已覆盖：设计、调度、批量尝试、代码生成、持续迭代、大量任务。
缺口与补齐设计：
1. **拆解+规划**：状态机加 planning 阶段——triage 后 LLM 生成解题计划注入 worker 提示词（D-CIPHER Planner 思想自研）
2. **训练/优化闭环**：logs → 复盘脚本（失败模式统计+成功经验提取）→ 半自动更新技能包/提示词（版本化+git）→ 演练验证；决赛以成绩曲线+版本记录呈现
3. **人机交互产品化**：Flask 看板（题状态/耗时/花费/提交/日志）+ 人工复核提交开关 + hints 网页界面（覆盖 目标设定/策略判断/过程监督/结果复核 四价值）
4. **全局日志分析**：编排器层失败模式统计（工具失败/题型卡点/常见错误），赛中指导人工 hints
5. **高频反馈**：poller 间隔可调（测试赛确认平台容忍度后 5-15s）

### 5.3 状态机 v2.1（统摄 14 项改动）
```
new → triage → queued → planning → solving(竞速) → verify(可选人工复核) → submit
    ↘ needs_hint（人工提示）←────────── dead（预算耗尽/僵局上限）
```
状态入 state.json；14 项改动按状态机分阶段挂载，每步回归（保住 M2 能跑版本）。

### 5.4 实施节奏修正
- P0（8/15）：状态机定义 + json 模式 + 进程组杀 + 提交纪律（小步回归）
- P1（8/16）：BasePlatform + 僵局双层 + 解析回退 + planning 阶段
- P2（8/17）：triage + 看板 + 复核开关 + 训练闭环脚本 + 全链路复测
- 测试赛后：SSH 通道、消息总线、高频 poller、其余
