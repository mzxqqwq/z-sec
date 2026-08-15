# AI 自动打 CTF 比赛调研报告 + 4 天冲刺方案

> 生成：2026-08-14 深夜 | 目标赛事：第九届西湖论剑「AI Agent 解题夺旗」赛道（game.gcsis.cn）
> 依据：国内外两线调研（Desktop 上 research-cn-ai-ctf.md / research-intl-ai-ctf.md）+ Cairn 源码精读 + pi 两份拆解 + 官方新闻原文

---

## 一、比赛实况（官方新闻确认，优先级最高）

| 时间 | 事项 |
|---|---|
| 7/13–**8/10 16:00** | 报名（**已截止**，务必确认自己已报名） |
| **8/18 09:00–8/19 17:00** | **测试赛**：熟悉平台、调试 API（还剩 ~3.5 天） |
| **8/21 14:00–17:00** | **线上初赛**（还剩 ~6.5 天） |
| 8/31 前 | 晋级名单（前 12 名进线下决赛） |
| 9 月底前 | 线下决赛：系统演示 + 专家代码审查 + 技术问答 |

**规则要点**：
- 题量远超人工上限 → Agent 批量分析、自动尝试、持续迭代
- **仅开放 API，不提供网页答题入口** → 系统架构与工程化能力是核心考点
- **支持人机持续交互** → 人设定目标、调优先级、修策略；解题主体是 AI
- 阿里云：大学生每人 300 元算力券 + 百炼大模型平台（领取：university.aliyun.com/action/dasctf）

**对设计的直接推论**：①必须有一个"平台 API 客户端"（拉题/交 flag/查分）为核心组件；②架构要干净可讲（决赛代码审查）；③人要能中途注入意图（Cairn 的 Hint 机制就是干这个的）。

---

## 二、冠军/获奖方案情报汇总

### 2.1 国内最高水位：腾讯云黑客松 TCH（唯一 AK 战队 Bytex / Cairn）
- 复盘：http://cn-sec.com/archives/5181921.html | 源码：https://github.com/oritera/Cairn （AGPLv3，2295⭐）
- **架构**：黑板（Blackboard）+ Fact/Intent/Hint 三原语图；Dispatcher 单一控制面；Worker 跑 OODA 循环，**无角色、无分工、纯 stigmergy 协同**；三种任务：Bootstrap（直接试解）/ Reason（读图决策）/ Explore（认领一个 intent 执行）
- **Worker 后端 = Claude Code / Codex / Pi**（pi 是官方一等公民！）
- **反直觉结论（Less is More）**：零 MCP、零 RAG、零预定义角色、全系统只有一个"flag 提交"skill；反对精细多 agent 分工和通识知识库
- **成本**：7692 元 / 5 天 / 10.9 亿 tokens；一个 flag 平均 52~284 元；**第四天零产出靠"人工复盘日志+注入人类意图"才翻盘** → 人机交互接口 = 胜负手
- 约束：官方支持 macOS/Linux；本机是纯 Windows（无 WSL/无 Docker），需装 WSL 或部署到 Kali 机

### 2.2 国际夺冠方案：verialabs/ctf-agent（BSidesSF 2026 52/52 第 1）
- https://github.com/verialabs/ctf-agent （已 clone 到 D:\ctf-agent\ctf-agent-ref）
- **架构**：Coordinator LLM + 每题一个 Solver Swarm（多模型并行竞速，先出 flag 者胜）+ 5s Poller 自动发现新题 + message bus 跨 solver 共享发现 + 每类独立 Docker 工具沙箱（pwn→pwntools/angr/gdb；crypto→SageMath/RsaCtfTool/z3；rev→r2；forensics→volatility3；stego→zsteg）
- 约束：Python 3.14+ ✓（本机 3.14.5）、Docker ✗（本机无）、CTFd 平台专用（DASCTF 平台 API 需适配）

### 2.3 学术 SOTA 与 benchmark（评测/训练用）
- **NYU CTF-Bench**（200 题 26 类，最对口 DASCTF 类别的评测床）：https://github.com/NYU-LLM-CTF/llm_ctf_automation
- **D-CIPHER**（Planner + 异构 Executor + auto-prompter；SOTA 22%/22.5%/44%）
- **EnIGMA**（ICML 2025）：**交互式调试器 + 交互式连服务 = pwn/web 胜负手**；警惕 soliloquizing（模型幻觉自问自答，必须真实执行回读输出）
- **Cybench**（ICLR 2025 Oral）：当前 agent 只在中低难度区间有效
- **AIxCC 冠军 ATLANTIS**（MIT 开源）：Ensemble-First，LLM×符号执行×定向 fuzzing，8 个 patch agent——思路可借鉴但目标不同

### 2.4 国内工程参考
- **LLM-CTF-Solver**（gehewu，12⭐）：Windows 可跑、**Kali SSH 远程执行**、OpenAI 兼容直连 DeepSeek、ReAct+三层解析回退+六维僵局检测+三层记忆+断点续跑——对"本机 Windows + Kali 远程 + DeepSeek"约束最省事
- **BUUCTF_Agent**（MuWinds，256⭐）：作者判断"通用 coding agent + 强模型已足够，专门 CTF agent 边际价值下降"
- **Koshary**：按类别路由模型 + plan 阶段 + 每题工作区落盘 + flag 抽取器 + 限次提交

### 2.5 纠偏
- DEF CON 31 AI Village CTF 实为"人攻 LLM"提示注入赛，与本题无关；"Rudolf"冠军 agent 查无实据
- DataCon AI 安全赛道/之江大模型靶场 = "打 LLM"（越狱），与"用 LLM 打 CTF"是两回事

---

## 三、pi 代码能否用于比赛 agent 设计：**能，且有冠军背书**

1. **直接背书**：Cairn（国内唯一 AK 冠军系统）官方支持 **Pi 作为 Worker 后端**——pi 的设计（事件流 loop + hooks + FileSystem/Shell 可替换 + SKILL.md）恰好满足 Cairn worker 的"收 prompt → 干活 → 输出结构化 JSON"契约
2. **两条复用路径**：
   - **路径 1（省力）**：Cairn 做黑板编排层（AGPLv3，教育用途 OK），pi CLI 当 worker，DeepSeek/百炼 Qwen 做模型，Kali 容器做执行环境 → 100% 冠军验证过的组合，自研部分只剩"平台客户端 + flag 提交 skill + 配置"
   - **路径 2（更自研）**：以 pi-agent-core/pi-ai 为库（MIT，npm 直接引），自写轻量黑板/DAG 编排 + CTF 专用工具集 + 平台客户端；复刻 Cairn 三原语设计和 EnIGMA 交互式工具
3. **pi 拆解中最值得抄进自研的 6 点**（详见两份拆解报告）：事件流 hook 循环（beforeToolCall 做权限门禁）；convertToLlm 消息分层；FileSystem/Shell 可替换后端（接 Kali SSH）；SKILL.md 技能包；失败编码进流不抛异常；SQLite 断点续跑（Entry/Record + writer lease）
4. **pi 缺的、必须自补的**：平台客户端、题目状态机、flag 提交、僵局检测、成本熔断、并行竞速调度——恰好 Cairn/verialabs/LLM-CTF-Solver 分别提供了范本

---

## 四、4 天冲刺方案（到 8/18 测试赛）与 7 天初赛方案

### 原则：测试赛是"熟悉平台、调试 API"的低风险演练 → 8/18 的目标不是解题，是拿到 API 并跑通闭环；8/21 才是真战场。

### Day 0（今晚）决策 + 环境
- [ ] 确认报名状态（已截止！没报要立刻找渠道）
- [ ] 领取阿里云 300 元算力券 + 百炼 API key（测试赛可能要用官方模型）
- [ ] 选型定稿：建议 **路径 1 起步**（Cairn + pi worker + DeepSeek/百炼 + Kali），自研增量 = 平台客户端 + flag skill
- [ ] Kali 机确认可 SSH（10.174.153.128）+ 装 Docker 或直接用本地模式

### Day 1（8/15）环境搭建
- [ ] 本机装 WSL2（或决定 Cairn 部署在 Kali 机）
- [ ] Cairn 跑通 local mode + mock worker；pi 装好并配 DeepSeek provider（pi 内置 deepseek 目录）
- [ ] Kali：pip 装 pwntools/angr/z3/sympy/SageMath/volatility3/binwalk 等全套（换清华源）
- [ ] 写 mock CTF 平台（本地 Flask：/challenges + /submit），打通"拉题→pi 解题→交 flag"最小闭环

### Day 2（8/16）平台客户端 + flag 链路
- [ ] DASCTF 平台客户端骨架：cookie 登录 + 题目列表 + 附件下载 + 交 flag + 查分（参考 openharmonyctf-platformskill 的 contest_api.py：cookie jar + captcha + 加密）
- [ ] flag 检测器：多正则 + 格式校验 + 提交冷却/限次（Koshary/verialabs 做法）
- [ ] 人机交互接口：人工注入 Hint / 改优先级（Cairn 原生支持，跑通演示）

### Day 3（8/17）端到端演练
- [ ] 用 NYU CTFTiny 或往届 DASCTF 题跑 3~5 道全流程（web/pwn/crypto 各一），记录成本与失败模式
- [ ] 补 pwn/web 的交互式工具（EnIGMA 结论：GDB 交互 + nc 连服务）
- [ ] 写 README/架构图（决赛代码审查要讲）

### Day 4（8/18–19）测试赛
- [ ] 抓真实 API（登录、题目 schema、提交响应、限频、captcha）
- [ ] 适配客户端；小规模试打；记录平台行为（题量、题型分布、动态计分？）
- [ ] 当晚复盘，修问题

### Day 5–6（8/20）初赛前
- [ ] 按测试赛暴露的问题修系统；把题型→模型/工具路由表调好
- [ ] 准备成本熔断与预算（初赛 3 小时，控制单题 token 上限）

### Day 7（8/21 14:00–17:00）初赛实战
- [ ] 人盯盘：只做"定目标、调优先级、注入意图"，不手工解题

---

## 五、风险与备用方案

| 风险 | 对策 |
|---|---|
| 报名已截止/未报名 | 立刻核实；如未报，确认是否可补报或转传统赛道 |
| Cairn 在 Windows/WSL 上跑不起来 | 部署到 Kali 机（Linux）或降级用 LLM-CTF-Solver 骨架 |
| 平台 API 有 captcha/风控 | 复用 openharmonyctf-platformskill 的 captcha/加密经验；测试赛重点探测 |
| 官方要求用阿里云百炼模型 | pi-ai 支持自定义 baseUrl，百炼是 OpenAI 兼容端点，直接配 |
| 预算失控 | 成本熔断 + 便宜模型跑枚举（pi 中立 transcript 换模型能力正好用上） |

---

## 附：本机资产清单（全部就绪）

- D:\ctf-agent\：pi-mono、pi-agent、cairn-ref、ctf-agent-ref、docs（拆解×2 + 架构方案 + 本报告）、src（空，留给自研代码）
- DSH 环境：10 个渗透 skills + burp/chrome-devtools/kali MCP（驾驶舱/人工辅助用）
- 本机：Python 3.14.5 / Node 24 / pnpm / bun / git；缺 WSL、Docker、pwntools 等（Kali 机补）
- 参考报告：Desktop 上 research-cn-ai-ctf.md、research-intl-ai-ctf.md、CTF-Agent-架构方案.md
