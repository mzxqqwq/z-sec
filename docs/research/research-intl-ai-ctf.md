# 国际「AI Agent 自动打 CTF / 网络安全挑战赛」调研报告

> 调研目标：为自研「自动打 CTF 比赛 agent」（目标赛事：DASCTF，Jeopardy 赛制，类别 web / pwn / re / crypto / misc）梳理国际知名比赛、冠军/获奖团队的开源方案、学术 benchmark 与关键工程经验。
> 调研方式：多轮中英文 web_search + 直接抓取 GitHub README / arXiv 摘要原文核对。
> 日期：2026-08。

---

## 0. 一个重要澄清：关于「DEF CON AI Village LLM CTF / Rudolf」

在进入正文前必须澄清一个检索事实，以免误导后续方案选型：

- 经多轮中英文检索，**未能找到**名为「Rudolf」、由「Cognitive Agents」团队开发的 DEF CON AI Village「LLM CTF」冠军 agent 的公开仓库或可靠一手资料。
- 公开可查的 DEF CON 31（2023）AI Village 官方 CTF（Kaggle 平台 *AI Village Capture the Flag @ DEFCON31*）本质是**「人类攻击 LLM」的提示注入赛**（选手从被围攻的 LLM 里套出隐藏 secret/flag），而**不是**「AI agent 自动解 Jeopardy 式 CTF」。
- 真正「AI agent 自动解 Jeopardy 式 CTF」的公开比赛/获胜记录，可验证的是：**CSAW'25 Agentic Automated CTF**（NYU 主办，2025）、**BSidesSF 2026**（verialabs/ctf-agent 52/52 全解夺冠）、以及学术 benchmark（CTF-Bench、Cybench、EnIGMA、D-CIPHER 等）。
- 若手头有「Cognitive Agents 团队的 Rudolf」的具体出处（某篇博文/视频/论文），可据此再补一轮定向核实；本报告以下内容均为**可验证的一手来源**。

---

## 1. DEF CON AI Village「LLM CTF」方向（2023/2024）

### 1.1 名称与链接
- **AI Village Capture the Flag @ DEFCON31（2023）**：https://www.kaggle.com/competitions/ai-village-capture-the-flag-defcon31
- 后续**Generative Red Team (GRT)**：https://grt.aivillage.org/announcement

### 1.2 规则要点
- DEF CON 31（2023）那场是「**红队攻击大模型**」：官方把一个受提示词约束的 LLM 当作"受害者"，选手用提示注入等技术让模型泄露隐藏 flag（secret），按泄露难度/数量计分。它考验的是「模型越狱/提示注入」，**不是**「agent 自主解题」。
- GRT（DEF CON 32/33）延续此思路，规模更大、加入了多轮对话与更多防御机制。

### 1.3 对「自研打靶 agent」的意义
- 这一支与 Jeopardy 打靶 agent 关系较弱，但有一个可迁移点：**「解题后如何稳定地从环境/输出里抽取 flag」**（正则、格式约束、去重、候选值校验），详见 Koshary、verialabs 的做法。
- 真正对标「自动打 Jeopardy CTF」的公开比赛请见 §1.4 与 §5.2。

### 1.4 最接近的「AI 自动解 CTF」真实比赛（可验证）
- **CSAW'25 Agentic Automated CTF Competition**（NYU，2025-07-01 ~ 10-08）：参赛者提交一个能解 CTF 题的 LLM agent，官方用 NYU CTF Bench 的题集（含 **CTFTiny** 精简题集）评测并排榜。
  - 首页/榜单：https://nyu-llm-ctf.github.io/csaw_llmctf.html
  - 题集：https://github.com/NYU-LLM-CTF/CTFTiny
  - 参考 agent：NYU CTF Bench baseline、EnIGMA（SOTA）。
- **BSidesSF 2026 CTF**：verialabs 的 `ctf-agent` 全解 52/52 题夺冠（见 §5.2），覆盖 pwn/rev/crypto/forensics/web/misc 全类别——这是目前**最贴近 DASCTF 赛制的真实夺冠开源方案**。

---

## 2. DARPA AIxCC（AI Cyber Challenge，2023–2025）

### 2.1 名称与链接
- 官网：https://aicyberchallenge.com
- 冠军系统论文（Team Atlanta）：**ATLANTIS** — https://arxiv.org/abs/2509.14589
- 开源仓库：https://github.com/Team-Atlanta/aixcc-afc-atlantis （MIT License）
- 系统性综述：**SoK: DARPA's AI Cyber Challenge (AIxCC): Competition Design, Architectures, and Lessons Learned** — https://arxiv.org/abs/2602.07666
- 团队博客（Atlantis 基础设施/多语言漏洞挖掘/LLM+Fuzzing 细节）：https://team-atlanta.github.io

### 2.2 比赛规则要点（对 agent 的限制）
- 目标不是"夺旗"，而是**自动发现并修复真实开源软件（OSS）中的漏洞**：对给定项目（C/Java 等）产出 **PoV（Proof of Vulnerability，能触发崩溃/漏洞的输入）**，并给出**语义正确的补丁（patch）**。
- 限制：完全**自主/无人值守**运行；DARPA 提供算力 + 有限 LLM API 额度；提交需过 **SARIF 校验**（PoV 与补丁的匹配、去误报）；按「发现的真实漏洞数 + 补丁质量」计分。
- 决赛项目含 Linux 内核、Nginx、Jenkins、SQLite 等大型真实代码库，远超 CTF 单题难度。

### 2.3 冠军方案 ATLANTIS 核心架构
- **总体哲学：Ensemble-First（集成优先）**——凡有独特贡献的技术都纳入，用多套独立方法并行提高鲁棒性。
- **多独立漏洞发现模块**通过 **seed sharing（种子共享）** 协作；**8 个 patch agent** 采用多样化修复策略并行出补丁，任一 agent 产出有效补丁即停。
- **LLM × 程序分析**融合：**符号执行 + 定向 fuzzing（directed fuzzing）+ 静态分析**，与 LLM 互补——LLM 负责语义理解/生成，传统分析负责覆盖与精度。
- 工程栈：Python + Rust，**LangGraph** 编排 + LiteLLM 多模型路由；是**唯一微调过 LLM 的决赛团队**。
- 成绩：AIxCC 决赛**第 1 名**；据 SoK 分析，Atlantis 的 PoV-补丁匹配、技术覆盖与"效果/复杂度"平衡均为全场最佳。

### 2.4 其他决赛团队（架构一句话，详见 SoK）
| 团队 | CRS | 一句话架构 |
|---|---|---|
| Trail of Bits | **Buttercup** | 专家式分解：确定性工作流把问题拆成子任务，LLM 只在传统工具不足处补位；刻意不用高端推理模型 |
| Theori | **RoboDuck** | Agentic-first，自定义 agent 库，全流程围绕"bug candidate"（识别→过滤→PoV→补丁→SARIF 校验→打包） |
| Fuzzing Brain | **FuzzingBrain** | 极简架构 + 23 个独立 LLM 策略脚本并行跑，>90% 代码 vibe-coded |
| Shellphish | **Artiphishell** | 53 个组件的最全面技术覆盖，自研编排平台做组件间通信 |
| 42-b3yond-6ug | **BugBuster** | 务实派：传统 fuzzing/程序分析为主，LLM 仅做种子生成等辅助 |
| Lacrosse | **Lacrosse** | Lisp 任务分发 + DSPy 多 LLM 并行/回退工作流 |

### 2.5 开源可用性
- **ATLANTIS 已开源（MIT）**：`github.com/Team-Atlanta/aixcc-afc-atlantis`，含 `example-crs-webservice`（主实现）、`example-crs-architecture`（部署）、`example-crs-appendix`。可跑、可复现，但面向"漏洞挖掘+修复"，**与 Jeopardy 打靶是两套目标**，借鉴价值主要在「LLM × 传统工具编排 + 多 agent 集成 + 种子共享」。
- SoK 综述论文公开了七队架构/技术对照表（PoV 生成、补丁生成、集成策略等），是极好的架构参考。

---

## 3. EnIGMA（Automated CTF Competition / Agent，2024–2025）

### 3.1 名称与链接
- 论文：**EnIGMA: Interactive Tools Substantially Assist LM Agents in Finding Security Vulnerabilities** — https://arxiv.org/abs/2409.16165 （ICML 2025）
- 项目主页：https://enigma-agent.com
- 代码：基于 **SWE-agent**（`github.com/SWE-agent/SWE-agent`，v0.7 分支）改造
- 开发题集：`github.com/NYU-LLM-CTF/NYU_CTF_Bench`（development 集）

### 3.2 规则/评测要点
- 在 **390 道 CTF 题**（NYU CTF、Intercode-CTF、CyBench 等四大 benchmark）上评测 agent 自主解题能力；SOTA 成绩（NYU CTF / Intercode-CTF / CyBench 均第一）。

### 3.3 核心架构与关键技巧
- **底座**：SWE-agent 风格的 **Agent-Computer Interface（ACI）**——agent 通过受限命令界面与运行环境交互。
- **关键贡献：Interactive Agent Tools（交互式工具）**，首次让 LM agent 能跑**交互式终端程序**：
  - **debugger 工具**（如 GDB 交互式调试）
  - **server connection 工具**（连接并交互式访问网络服务，pwn/web 题必需）
- 论证：解决 pwn/web 类题，**"能交互式调试 + 连服务"是性能分水岭**，纯一次性生成脚本远不够。
- 提出并量化 **数据泄漏（data leakage）** 与一个新现象 **soliloquizing（自言自语）**：模型在没真正执行的情况下，自我生成幻觉式的"观察结果"，导致虚高成绩——评测/自研时都要防这个坑。

### 3.4 开源可用性
- 代码随 SWE-agent 仓库发布，可用；是学术圈公认的 CTF agent SOTA 之一，**交互式工具设计对 pwn/web 题尤其有直接借鉴价值**。

---

## 4. 学术 Benchmark

### 4.1 NYU CTF-Bench（含 D-CIPHER / auto-prompter）
- **Benchmark 论文**：*NYU CTF Bench: A Scalable Open-Source Benchmark Dataset for Evaluating LLMs in Offensive Security* — https://arxiv.org/abs/2406.05590 （NeurIPS 2024 Datasets & Benchmarks）
- **主页/题集**：https://nyu-llm-ctf.github.io ；**GitHub**：https://github.com/NYU-LLM-CTF/nyuctf_agents 、https://github.com/NYU-LLM-CTF/llm_ctf_automation
- **结构**：200 道题、26 个类别（web/pwn/crypto/rev/misc/forensics…）、4 档难度；题在 Docker 容器里跑，agent 可执行命令并观察输出。**与 DASCTF 的 Jeopardy 类别高度重合**，是最对口的数据集/评测床。
- **D-CIPHER 框架**（论文 https://arxiv.org/abs/2502.10931）：多 agent 协作解 CTF
  - **Planner agent**：全局规划、拆解子任务、分配职责
  - **多个异构 Executor agent**：分头执行子任务（heterogeneous 执行器）
  - **Auto-prompter agent**：自动生成高质量初始 prompt（减少人工调 prompt 的方差）
  - 动态反馈循环（多轮交互）；GitHub 里的 `run_dcipher.py` 可开/关 autoprompt、可跑单 executor 消融。
- **成绩**：D-CIPHER 达到 SOTA——**NYU CTF Bench 22.0%、Cybench 22.5%、HackTheBox 44.0%**，比先前工作高 2.5–8.5 个百分点；并多解出 65% 的 MITRE ATT&CK 技术。
- **baseline agent**：单 agent 的 ReAct 循环，是入门参照（`llm_ctf_automation` 的 `run_baseline.py`）。

### 4.2 Cybench
- **论文**：*Cybench: A Framework for Evaluating Cybersecurity Capabilities and Risks of Language Models* — https://arxiv.org/abs/2408.08926 （ICLR 2025 Oral）
- **主页**：https://cybench.github.io ；**GitHub**：https://github.com/andyzorigin/cybench （早期为 `skalskip/cybench`）
- **结构**：40 道专业级 CTF 题（来自 4 场比赛，含 HTB Cyber Apocalypse 2024 等），每题有描述、起始文件、可执行命令的环境；对 17 道题再拆 **subtasks**（子步骤）做细粒度评分。
- **评测**：8 个模型（GPT-4o、o1-preview、Claude 3 Opus/3.5 Sonnet、Mixtral、Gemini 1.5 Pro、Llama 3 70B/3.1 405B）；还对比了 4 种 agent scaffold（structured bash / action-only / pseudoterminal / web search）。
- **成绩要点**：无子任务引导时，Claude 3.5 Sonnet、GPT-4o、o1-preview、Claude 3 Opus 能解出"人类战队 11 分钟内做出来的题"；最难的题人类用了 24 小时 54 分——说明**当前 agent 只在中低难度区间有效，长时/高难仍是瓶颈**。
- **agent 循环**：迭代式（`run_task.sh --max_iterations`），输入/输出 token 截断可控，logs 目录落盘可续跑（`--extend_iterations_from_log`）。

---

## 5. 其他知名开源「自动打 CTF」项目

### 5.1 marvang/ctf-agent（CHAP 上下文交接）
- **GitHub**：https://github.com/marvang/ctf-agent
- **论文**：*Context Relay for Long-Running Penetration-Testing Agents*（NDSS LAST-X 2026），DOI 10.14722/last-x.2026.23042
- **架构要点**：
  - LLM 驱动的渗透/解 CTF agent；**可选 CHAP（Contextual Handoff of Automated Prompts）上下文交接**——长会话里把关键上下文跨轮次压缩/传递，避免上下文窗口爆掉。
  - 自动侦察 + 漏洞利用；**Kali 容器**（Docker）作为执行环境，`ctf-workspace/` 挂载共享。
  - 实时 **cost/token 追踪**；**session log** 记录时间戳、命令、上下文、结果（可复盘）。
  - 内置 **AutoPenBench 改进版 benchmark**（11 个 CVE 靶机：Log4Shell、Spring4Shell、SambaCry、Heartbleed 等）。
- **开源**：可用；需 OpenRouter key + Docker。对「长时任务记忆管理」极有借鉴价值。

### 5.2 verialabs/ctf-agent（多模型并行竞赛，BSidesSF 2026 冠军）
- **GitHub**：https://github.com/verialabs/ctf-agent
- **成绩**：**BSidesSF 2026 CTF 52/52 全解，第 1 名**，覆盖 pwn/rev/crypto/forensics/web/misc 全类别——**当前最贴近 DASCTF 赛制的夺冠开源方案**。
- **架构（核心）**：
  - **Coordinator LLM（协调者，Claude/Codex）+ Solver Swarm（求解蜂群）** 两级结构。
  - 每个题一个 **swarm**，内部**多模型同时并行跑同一题**（Claude Opus 4.6 med/max、GPT-5.4、GPT-5.4-mini、GPT-5.3-codex），**谁先出 flag 谁赢**（first-to-flag）。
  - **Poller（5s）**轮询 CTFd 平台发现新题，自动 spawn swarm。
  - Coordinator **读各 solver 的解题轨迹，给出定向技术提示**；跨 solver 通过 **message bus 共享发现**；支持**赛中人工给提示**（operator messaging）。
  - 每 solver 跑在**独立 Docker 沙箱**，按类别预装工具（pwn: pwntools/angr/ROPgadget/gdb；crypto: SageMath/RsaCtfTool/z3/gmpy2/cado-nfs；rev: radare2/ghidra 类；forensics: volatility3/foremost；stego: zsteg/stegseek；web: curl/requests 等）。
  - "Solvers never give up"——持续换思路直到出 flag。
- **开源**：可用；Python 3.14 + Docker + 各模型 API key。**这是"多模型并行 + 协调者 + 全类别工具沙箱"打法的最直接范本。**

### 5.3 ahmedreda38/Koshary（CTFd 多 agent 编排）
- **GitHub**：https://github.com/ahmedreda38/Koshary
- **架构要点**：
  - 面向 **CTFd / Hack The Box CTF（MCP 或 cookie）** 的多平台自动解题框架，平台适配器统一成 `NormalizedChallenge + submit_flag()` 接口。
  - **category routing（按类别路由模型）**：`config.json` 里把 web/crypto/pwn/forensics… 分别映射到 gemini/codex/claude（不同模型擅长不同类别）。
  - **主循环**：拉题 → 按类别路由 → 建每题工作区 → **plan 阶段（可选）** → 多轮 model rounds → 抽取命令/代码/flag → 提交。
  - **并行 workers** 并发解多题；**flag 抽取器**（正则 + 非标准候选值）；**状态持久化**（`state/db.json`、每题 `challenges/<id>-<slug>/` 下保存 plan.md、round_XX.{prompt,out,commands,exec,artifact}.txt、walkthrough.md）。
  - 支持 `--no-submit / --manual-submit`、限次错误提交、first_blood 早鸟抢答工具、walkthrough 复盘模式。
- **开源**：可用（v0.1 beta）；需装 `gemini`/`codex`/`claude` CLI。**"类别路由 + 每题工作区 + 多轮执行 + flag 抽取 + 状态落盘"是直接可抄的骨架。**

---

## 6. 官方/论文中的网络安全 Agent 研究

- **OpenAI**：GPT-4 发布前后的 **Preparedness（准备度）框架** 与 **GPT-4o System Card** 中评估了模型的**进攻性网络能力**（漏洞利用、自主网络任务）并给出"能力提升（uplift）"结论（2024 初结论为"边际提升有限，但值得持续监测"）。公开系统卡：https://openai.com/index/gpt-4o-system-card/
- **Anthropic**：在 Claude 3/3.5/3.7 的负责任扩展/能力评估中持续做 **Cyber 能力 uplift** 测试（CTF 任务、漏洞利用等），用于判定是否需要加护栏。
- **UK AISI（英国 AI 安全研究院）**：*How do frontier AI agents perform in multi-step cyber-attack scenarios?* 对比了 **GPT-5.5 与 Claude Mythos** 在多步网络攻击中的表现（https://www.aisi.gov.uk/blog/how-do-frontier-ai-agents-perform-in-multi-step-cyber-attack-scenarios）。结论方向：前沿模型已接近"具备多步攻击能力"，但推理稳定性仍是短板。
- **Google DeepMind**：**Project Naptime / Big Sleep** 等将 LLM 用于真实软件漏洞挖掘（首个在真实在野项目中发现可利用漏洞的 agent 之一），与 AIxCC 思路同源，偏"漏洞挖掘"而非"打靶"。

> 小结：这些官方研究主要回答"模型有多危险/能否被滥用于进攻"，**不直接提供打靶 agent 的开源代码**，但对"选哪个底层模型 + 能力边界"有参考：当前最强打靶效果集中在 Claude/GPT 旗舰推理模型，且**多模型组合普遍优于单模型**（见 §5.2）。

---

## 7. 对中国用户自研 Jeopardy 式 CTF 打靶 agent 最有借鉴价值的 5 条经验

1. **多模型并行竞赛（multi-model racing）+ 协调者/蜂群两层架构**——这是 verialabs 在 BSidesSF 52/52 全解的秘诀：同一道题同时丢给多个不同模型（含不同推理强度档位）并行跑，**先出 flag 者胜**；上层一个 Coordinator LLM 读各求解轨迹、给定向提示、跨题共享发现。单模型单线程的 ReAct 是性能下限，多模型+多路并行是上限杠杆。

2. **按类别路由模型 + 每类独立工具沙箱**——Koshary 的 `config.json` 把 web/pwn/crypto/… 分别路由到擅长模型；verialabs 给每类题预装专用工具（pwn→pwntools/angr/ROPgadget/gdb，crypto→SageMath/RsaCtfTool/z3，rev→r2/ghidra，forensics→volatility3/foremost）。对 DASCTF 的 web/pwn/re/crypto/misc 五类，**先把每类的工具镜像和"最擅长该类的模型"做成配置表**，收益最大。

3. **结构化 agent 循环 + 显式记忆/状态管理**——三层设计：① **规划-执行分离**（D-CIPHER 的 Planner + 异构 Executor + auto-prompter，先出全局计划再分头执行）；② **每轮持久化**（每题的 prompt/命令/执行输出/生成的脚本 artifact 全部落盘到工作区，可断点续跑、可复盘）；③ **长时上下文交接**（CHAP 的上下文压缩/接力，防止上下文窗口爆炸）。"跑完能复现、中断能续"是打长比赛的基本功。

4. **交互式工具是 pwn/web 题的胜负手**——EnIGMA 的核心发现：能**交互式调试（GDB）和交互式连服务（server connection）**，比只能一次性生成脚本，解题率提升显著；同时警惕 **soliloquizing（幻觉式自问自答）**，必须让模型**真实执行并回读环境输出**，而不是信它"脑补"的结果。DASCTF 的 pwn/web 题尤其要吃透这一点。

5. **全流程工程化：plan 先行 → flag 抽取 → 限次提交 → 成本/评分控制**——开工先对每道题做 **plan 阶段**（Koshary 的 `--plan`、D-CIPHER 的 planner）；用**正则 + 非标准候选值**稳健抽取 flag（Koshary 的 `flag_extractor`）；设**错误提交上限 + 提交冷却**避免被平台封号；从简单题/签到题抢 **first blood**，难中易分层调度；全程 **token/费用追踪**。对 DASCTF 这种有平台、有提交惩罚的赛制，这些"脏活"往往比模型选型更能决定名次。

---

## 附：关键开源仓库/论文速查表

| 名称 | 链接 | 类型 | 开源 |
|---|---|---|---|
| NYU CTF Bench | https://nyu-llm-ctf.github.io / https://arxiv.org/abs/2406.05590 | Benchmark（200 题 26 类） | ✅ |
| D-CIPHER | https://arxiv.org/abs/2502.10931 / github.com/NYU-LLM-CTF/nyuctf_agents | 多 agent 框架 | ✅ |
| EnIGMA | https://arxiv.org/abs/2409.16165 / enigma-agent.com / SWE-agent v0.7 | 交互式工具 agent | ✅ |
| Cybench | https://arxiv.org/abs/2408.08926 / github.com/andyzorigin/cybench | Benchmark（40 题+subtask） | ✅ |
| ATLANTIS (AIxCC 冠军) | https://arxiv.org/abs/2509.14589 / github.com/Team-Atlanta/aixcc-afc-atlantis | 漏洞挖掘+修复 CRS | ✅ MIT |
| AIxCC SoK | https://arxiv.org/abs/2602.07666 | 七队架构综述 | ✅ |
| marvang/ctf-agent (CHAP) | https://github.com/marvang/ctf-agent | 打靶 agent + 上下文交接 | ✅ |
| verialabs/ctf-agent | https://github.com/verialabs/ctf-agent | 多模型竞赛 agent（BSidesSF 冠军） | ✅ |
| Koshary | https://github.com/ahmedreda38/Koshary | CTFd/HTB 多 agent | ✅ |
| CSAW Agentic Automated CTF | https://nyu-llm-ctf.github.io/csaw_llmctf.html | 真实比赛 | ✅（题集 CTFTiny） |

*（除特别标注外，本报告所有链接均为调研时可直接访问/可核验的公开来源。）*
