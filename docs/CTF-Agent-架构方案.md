# DASCTF 自动打靶 Agent —— pi 代码拆解总结与架构方案

> 生成日期：2026-08-14
> 目标：参加 game.gcsis.cn（DASCTF，Jeopardy：web/pwn/re/crypto/misc）的自动打靶 agent
> 依据：D:\pi-mono-dissection.md（TypeScript Pi 拆解）、D:\pi-agent-dissection.md（Python pi-agent 拆解）、LLM-CTF-Solver 公开 README、本机工具盘点

---

## 一、三份参考材料的一句话总结

| 项目 | 定位 | 最值得抄的东西 |
|---|---|---|
| **Pi / pi-mono**（earendil-works/pi，TS） | 可扩展编码 agent 运行时（monorepo：ai/agent/coding-agent/tui） | 纯事件流 agent loop + `beforeToolCall` 等 9 个 hook 控制点；`FileSystem/Shell` 可替换执行后端（本地/容器/SSH 同接口）；SKILL.md + Pi Packages 分发；SQLite 会话 Entry/Record + writer lease；失败编码进流不抛异常 |
| **pi-agent**（Ashutosh，Python，~300 行核心） | 极简 ReAct 工具循环 | 中立 transcript + 每家翻译层（对话状态与厂商解耦，可中途换模型）；`Tool` dataclass 统一注册；分层护栏（路径边界 + 命令白名单 + 确定性正则）统一在 `_dispatch` choke-point；skill 相关性路由；有界自审 |
| **LLM-CTF-Solver**（gehewu，Python） | 现成的 CTF 自动解题 agent（BUUCTF_Agent 二开） | 21 个 CTF 专属工具；Kali SSH 远程执行 + 80+ 工具动态探测；6 维僵局检测 + 双熔断；三层记忆 + 关键事实防丢；ChromaDB RAG 自动学习；5 层 flag 检测；断点续跑；MCP 懒加载按题型激活 |

---

## 二、CTF 打靶 agent 的核心需求 → 设计映射

| 需求 | pi-mono 对应 | pi-agent 对应 | LLM-CTF-Solver 对应 |
|---|---|---|---|
| 解题循环 | agent-loop.ts 双层循环 + hooks | Agent._loop (ReAct) | ReAct + 三层解析回退 |
| 长任务不跑飞 | shouldStopAfterTurn / steering 队列 | max_iterations + reflect | 6 维僵局检测 + 熔断 + 断点续跑 |
| 上下文管理 | transformContext / convertToLlm / compaction | _history_for_request 裁剪对齐 user 边界 | 三层记忆 + 关键事实防丢 |
| 按题型加载知识 | SKILL.md + description 自路由 | skills.py top-k 相关性路由 | 45 技能 + RAG 知识库 |
| 换模型/控成本 | Provider 抽象 + getApiKey 动态换 key | 中立 transcript + /model 切换 | 多入口模型配置（solve/analyzer/pre/embedding） |
| 远程执行（Kali） | FileSystem/Shell 抽象 + ssh 扩展示例 | 无（仅本地） | Kali SSH 执行 + 80+ 工具探测 |
| 拿到 flag 后提交 | 无（自写工具 + afterToolCall 检测） | 无 | flag_detector 5 层 + 平台工具需自写 |
| 崩溃恢复/审计 | SQLite Entry/Record + writer lease | .pi/memory.md 极简 | checkpoints/ 按题 MD5 存档 |

**两个 pi 都没做、必须自补的**：比赛平台客户端（登录/拉题/交 flag）、题目状态机、flag 提交。

---

## 三、三条技术路线

### 路线 A：基于 LLM-CTF-Solver 二开（最快出成绩）
- 优点：CTF 工具/记忆/僵局检测/RAG 全部现成；示例模型即 deepseek-v4-pro；Kali SSH 直接对接你现有那台
- 要做：配 config.json（deepseek key + Kali SSH）→ 写一个 DASCTF 平台客户端工具（拉题/交 flag，独立实现）→ 沉淀题型技能包
- 风险：代码量不小、耦合较多，改起来要读懂它的 Workflow/SolveAgent 层

### 路线 B：抄 pi 骨架自研（最可控、工作量最大）
- 以 pi-agent-core + pi-ai 为底座（或按 Python pi-agent 的极简风格自写），抄 harness 抽象 + hooks + SKILL.md + SQLite session
- 优点：完全贴合比赛需求、无历史包袱；缺点：僵局检测/记忆/CTF 工具全部要自己写，赛前未必赶得完

### 路线 C：混合（推荐）
1. **赛前先跑通 A**：LLM-CTF-Solver + deepseek-v4-pro + Kali SSH + 自写 DASCTF 平台工具，先拿到"能自动打"的基线
2. **再按 pi 的设计给它补强**：把 pi-mono 的 `FileSystem/Shell` 抽象、hook 状态机、失败编码进流等思想改造进去；逐步把 LLM-CTF-Solver 里不满意的部分替换掉
3. 你的 DSH 环境（本页面）继续当"驾驶舱"：我在 DSH 里用已有 skills/MCP 做侦察与人工解题，脚本化的部分交给路线 A 的 agent

---

## 四、推荐架构（路线 C 落地）

```
┌─────────────────────────────────────────────────────────┐
│ 编排层（orchestrator）                                    │
│ 拉题（DASCTF 平台 API：cookie 登录 → 题目列表 → 附件下载）  │
│ → 题型分类（challenge_classifier）→ 路由到对应 skill/工具集  │
│ → 调度解题 agent（可并行多题）→ 监测 flag → 提交 → 记录分数 │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│ 解题 agent（LLM-CTF-Solver 的 SolveAgent / 或 pi 底座）    │
│ ReAct 循环 + 僵局检测 + 三层记忆 + 断点续跑                │
│ 模型：deepseek-v4-pro（主）+ 便宜模型（枚举/爆破）          │
└──┬──────────────┬──────────────┬──────────────┬─────────┘
   ▼              ▼              ▼              ▼
 本地工具       Kali 远程      MCP 外部       平台客户端
 python/git   SSH：nmap/     burp/          DASCTF API：
 文件/编码     sqlmap/       chrome-        拉题/交flag/
             pwntools/      devtools/      查分数
             gdb/metasploit kali-tools
```

**关键决策点**：
1. 模型：主用 deepseek-v4-pro（你已配好 DSH；LLM-CTF-Solver 也支持）；枚举类用 GLM/Groq free 省钱——这正是 pi-agent"中立 transcript 可换模型"设计的用途
2. Kali 接入：优先 SSH（LLM-CTF-Solver 原生支持，比 MCP 稳）；MCP 作为备选（DSH 已挂好 kali MCP）
3. 平台客户端：等比赛开放后抓 DASCTF 的 API（Vue SPA，/api/*），按抓包结果独立实现（cookie jar + captcha 钩子 + 加密钩子 + 提交）
4. 本机补装：pwntools、z3-solver、sympy（pip 一条命令）；gdb/gcc 留给 Kali
5. 规则确认：先确认比赛规则是否允许联网 LLM / 使用现成开源框架（有些 AI 专项赛会限制），这决定 A 还是 B

---

## 五、产出物清单（已就位）

- 拆解报告：`D:\pi-mono-dissection.md`、`D:\pi-agent-dissection.md`（桌面有备份）
- 源码：`D:\pi-mono`、`D:\pi-agent`
- DSH 环境：10 个渗透 skills + 3 个 MCP（burp/chrome-devtools/kali）已就绪
- 待办：clone LLM-CTF-Solver → 配 deepseek key + Kali SSH → 写 DASCTF 平台工具 → 赛前演练（用往届 DASCTF 题测）
