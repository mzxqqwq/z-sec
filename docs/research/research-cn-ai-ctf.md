# 中国「AI/LLM 自动打 CTF、人工智能网络安全」赛道与获奖方案调研报告

> 调研对象：为一位准备自研「自动打 CTF 比赛 agent」、将参加 **game.gcsis.cn 上的 DASCTF 比赛** 的用户提供参考。
> 调研方式：多轮中英文 web_search + 关键网页/README 原文核对。
> 生成时间：2026-08。

---

## 0. 核心结论速览（TL;DR）

1. **game.gcsis.cn 就是安恒信息（DBAPPSecurity）的竞赛平台**，DASCTF、西湖论剑等安恒系赛事都在此报名/比赛。2026 年第九届西湖论剑（官网即 game.gcsis.cn）**首次开设「AI Agent 解题夺旗」赛道**：只开放 API、题量远超人工上限、解题主体必须是 AI、决赛要代码审查。这几乎就是用户要打的赛道的「官方预告」，应重点研读其规则。
2. 国内「AI 自动攻防」最高水位在 **腾讯云黑客松（TCH）智能渗透挑战赛**（两届 238→610 支战队）：全程禁止人工介入靶场，LLM 自主渗透；唯一 AK 战队的「Cairn」系统给出反直觉结论——**Less is More，不要做精细多 Agent 分工、不要通用 RAG 知识库**，一个「黑板 + DAG 通用求解引擎 + Kali 容器」就够了。
3. 国外开源标杆 **verialabs/ctf-agent**（707⭐）用「协调者 LLM + 每道题一组求解 swarm 多模型并行竞速 + Docker 沙箱」在 BSidesSF 2026 拿下 52/52 全杀；国内 **MuWinds/BUUCTF_Agent**（256⭐）与二次开发 **gehewu/LLM-CTF-Solver**（12⭐）是最贴近「DeepSeek/GLM/Qwen + Kali SSH」的成熟工程参考。
4. 真正「命题式 AI 安全」竞赛（DataCon AI 安全赛道、之江铸网大模型靶场、护航丝路大赛）与「用 LLM 打 CTF」是两回事：前者考**越狱/幻觉/RAG**，后者考 **agent 工程**。用户要做的是后者。

---

## 1. DASCTF 与 game.gcsis.cn 平台

### 1.1 平台与机构
- **game.gcsis.cn**：安恒信息（杭州安恒信息技术股份有限公司 / DBAPPSecurity）运营的网络安全竞赛平台，**DASCTF 与西湖论剑大赛的报名/比赛入口**。证据：第九届西湖论剑官方新闻稿明确写「参赛者可通过大赛官网（game.gcsis.cn）报名」（安恒官网新闻：[安恒信息·第九届西湖论剑](https://www.dbappsecurity.com.cn/content/details6004_174117.html)）。
- **DASCTF**：安恒信息旗下的 CTF 品牌赛事，在 CTFtime 有登记页（[CTFtime.org / DASCTF](https://ctftime.org/ctf/1133)）。历届赛事大量在 BUUCTF（buuoj.cn）平台举办，例如 [DASCTF 2024 金秋十月](http://buuoj.cn/match/matches/211)、[DASCTF X HDCTF 2024](http://buuoj.cn/plugins/ctfd-matches/matches/204) 等。
- 关于 "GCSIS" 本身：公开渠道未找到安恒官方对该缩写的权威解释；ARIN 的 `GCSIS` 是另一个美国机构（Grand Central Station），与国内赛事无关。可视为安恒竞赛平台的内部命名，不影响参赛。

### 1.2 赛制惯例
- DASCTF 传统上以 **Jeopardy（解题模式）** 为主，题目分类与主流 CTF 一致：Web / Pwn / Reverse / Crypto / Misc（含取证、隐写、编码），详见 [DASCTF 命题指南 love-wiki](https://love-wiki.dasctf.com/)。
- 历届既有单人解题也有 AWD 攻防，还会与高校/品牌联名（X CBCTF、X HDCTF、X 0psu3 等）。往届 WriteUp 可在 CTF 导航（ctfiot.com）、cnblogs、CSDN 检索 "DASCTF wp"。
- **对 agent 最重要的变化**：2026 年起安恒在西湖论剑（game.gcsis.cn 上）新增 AI Agent 赛道（见 §2.1）。

### 1.3 DASCTF 与 AI 的结合点
- 未见 DASCTF 历史上存在独立「AI 赛道」的公开记录（多轮检索 "DASCTF AI / 人工智能 / AIGC / 智能体" 未命中历史独立 AI 赛道）。
- 但安恒有自研 CTF 向模型 **DASD-4B-Thinking**，宣传场景即「CTF 解题助手 / AI 助教」（[CSDN 介绍](https://blog.csdn.net/weixin_34640289/article/details/158944574)），说明主办方已把 LLM 解题视为常态并给出官方模型资源。

---

## 2. 国内「AI 网络安全」类竞赛

### 2.1 第九届西湖论剑「AI Agent 解题夺旗」赛道（最相关）
- 链接：[安恒信息官方新闻](https://www.dbappsecurity.com.cn/content/details6004_174117.html)、[杭州政府转载](https://www.hangzhou.gov.cn/col/col812266/art/2026/art_483d44943a4f4b898e1998389d4ee3b0.html)、[中国青年报·首次将 AI 智能体嵌入赛事](https://news.cyol.com/gb/news/articles/2026-07/13/content_YO9oevcvOE.html)。
- 主题「人才：引领AI安全新范式」；2026-07-13 报名，8 月测试赛 + 线上初赛，9 月线下决赛答辩。
- **规则要点（对 agent 最关键）**：
  - 在传统 CTF 之上**首次引入「AI Agent 解题夺旗」赛制**。
  - **题量远超人工处理上限**，选手必须靠 Agent 批量分析、自动尝试、持续迭代。
  - **仅开放 API 接口，不提供传统网页端答题入口**——考的是系统架构与工程化能力。
  - **支持人机持续交互**：人可设定目标、调整优先级、修正执行策略；解题主体是 AI，人负责建 Agent、定战略、监督。
  - **决赛需现场系统演示 + 专家代码审查 + 技术问答**。
  - 资源：阿里云为选手提供 300 元/人算力券、阿里云百炼（可调主流大模型）、QoderWork、秒悟 AI 开发工具；报名链接 `university.aliyun.com/action/dasctf`。
- 解读：这条赛道基本就是「给你一堆题 + 一个 API，让 agent 自己打」的 Jeopardy-agent 赛，和用户目标几乎 1:1 重合。

### 2.2 腾讯云黑客松（TCH）智能渗透挑战赛（国内 AI 自动攻防最高水位）
- 官方：`https://zc.tencent.com/hackathon`、`https://challenge.zc.tencent.com/`。
- 性质：腾讯云鼎实验室 + 腾讯安全众测联合发起的**国内首个以 AI 智能体为核心攻防主体的渗透赛事**。
- 规模：第一届 238 支战队/518 人；第二届 610 支战队/1345 人（清华、北大、复旦、绿盟、长亭、移动、电信等）。
- **规则要点**：**全程禁止人工直接介入靶场操作**，要求构建以 LLM 为核心的自主渗透智能体，在隔离云环境独立完成「信息收集 → 漏洞分析 → 攻击利用」完整链路。
  - 第一届：题目来自公开基准 `xbow-engineering/validation-benchmarks`，偏 CTF Web；按天随机抽题，agent 自主答题计分。
  - 第二届：官方命题纯黑盒，分四赛区（SRC/通用漏洞挖掘、典型 CVE 与 AI 基础设施漏洞、多层内网渗透与权限维持、基础域渗透），**动态计分**（开局落后很难反超）。
- 复盘文章：[唯一 AK 战队 Bytex 复盘（ChainReactor，cn-sec 转载）](http://cn-sec.com/archives/5181921.html)、[国内首个 Agent 安全攻防赛落幕（中国日报）](https://caijing.chinadaily.com.cn/a/202604/28/WS69f073a0a310942cc49a9e6a.html)。
- **获奖方案要点（Cairn 系统，作者即唯一 AK 战队 Bytex）**：
  - 架构 = **黑板架构 + DAG 图驱动的通用问题求解引擎**（Cairn Server + Dispatcher），Agent Worker 的协作协议。
  - **最小调度单位是 Claude Code / Codex**（称之为 Agent Worker），而非自研复杂子 agent。
  - **Less Is More**：明确**反对**「信息收集 Agent / Web 利用 Agent / 后渗透 Agent」式精细分工，反对传统 RAG 知识库，反对通用 Skill（漏洞利用方法、渗透流程），认为这些会「锁死上限」。
  - **整个系统只有一个 Skill**：为比赛适配的「flag 提交 Skill」；无 MCP、无 RAG。适配渗透测试只需「给引擎配一个装满工具的 Kali 容器」。
  - 成本数据：全程 Claude Opus + GPT，5 天约 **7692 元人民币、10.9 亿 tokens**；解开一个 flag 平均 52~284 元。存在边际收益递减；第四天零产出后靠「人工复盘日志 + 注入人类意图」才在第五天 AK。
  - 拟开源：`https://github.com/oritera/Cairn`（起零衍迹 Oritera）。

### 2.3 DataCon 大数据安全分析竞赛 · AI 安全赛道
- 官方/百科：[DataCon 百度百科](https://baike.baidu.com/item/DataCon%E5%A4%A7%E6%95%B0%E6%8D%AE%E5%AE%89%E5%85%A8%E5%88%86%E6%9E%90%E7%AB%9E%E8%B5%9B)、[DataCon2025 介绍（InForSec）](https://www.inforsec.org/wp/?p=6795)。
- DataCon2024 共 706 支战队、1556 名选手，设 **AI 安全、软件供应链、网络基础设施、网络黑产、漏洞分析** 五赛道；AI 安全赛道冠军为中科院信工所「啊对对对」战队。
- **AI 安全赛道考的是「打 LLM」，不是「用 LLM 打 CTF」**，子题有三（详见冠军 WriteUp：[DataCon2024 AI安全赛道 WriteUp](https://www.ctfiot.com/224443.html)）：
  - 大模型幻觉触发：构造让目标模型（黑盒 QAX-GPT）产生幻觉的 prompt；解法用「反转/近义词替换/语义背景（指环王/原始社会）」触发幻觉，按语义相似性、逻辑性、幻觉度三维评分。
  - 大模型幻觉缓解：用 RAG + 提示工程从知识库检索正确答案（文档分块 + 日期元数据 + bge-large-zh/en 向量检索）。
  - 大模型多轮对话越狱：把有害问题拆成 ≥4 个子问题、单问题 ≤40 词，多轮诱导（参考微软 Crescendomation、北大 Speak Out of Turn），黑盒、docker/函数构建提交。
- 规则要点：黑盒模型、限制提交次数、docker 提交、无 GPU（缓解题）。

### 2.4 之江铸网 2025 · 大语言模型安全靶场
- 链接：[搜狐·大模型安全靶场亮点](https://www.sohu.com/a/920715198_122212240)、[工信部转载](https://www.miit.gov.cn/xwfb/gxdt/dfdt/art/2025/art_9a93427c424340fabd1350009040e12b.html)。
- 主办：浙江省通信管理局 + 省经信厅；7/28–31；新增「智能网联汽车实车攻防」+「大语言模型安全靶场」赛道；靶场系统由君同未来提供。
- 规则/形态：以文本对话大模型为靶标，构建**关键词过滤、诱导式防御、多轮检测三阶动态防御**；选手用**越狱攻击、提示词攻击**突破防线「夺旗」得分；21 支攻击队、50 个种子问题、277 轮对抗、13838 条攻击样本。
- 意义：这是「AI 红队/越狱」夺旗赛，可作为「AI 安全专项」背景参考。

### 2.5 中国—东盟「护航丝路」人工智能安全大赛
- 链接：[广西政府门户](http://www.gxzf.gov.cn/zt/jd/qmybrgznsd_231227/sssl/ywjjj_231458/t25894255.shtml)、[百科](https://baike.baidu.com/item/%E4%B8%AD%E5%9B%BD%E2%80%94%E4%B8%9C%E7%9B%9F%E2%80%9C%E6%8A%A4%E8%88%AA%E4%B8%9D%E8%B7%AF%E2%80%9D%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD%E5%AE%89%E5%85%A8%E5%A4%A7%E8%B5%9B/66243296)。
- 主办：广西党委网信办 + 大数据发展局 + 南宁市政府；承办：奇安信。
- **五大赛道**：算力安全、算法安全、数据安全、应用安全、**实战攻防**（自动驾驶/智能家居/智慧社区等 AI 场景）。
- 报名走「智桂通」平台，提交报名表 + demo/技术路线等作品材料——偏「作品赛/方案赛」，与 agent 打靶关系较小，但「实战攻防赛道」值得关注。

### 2.6 其他国内 AI 网络安全赛/相关
- **全国大学生信息安全竞赛（CISCN）**：传统 CTF 官方赛事（ciscn.cn），尚未见独立 AI 解题赛道。
- **「复兴杯」全国大学生网络安全精英赛·人工智能安全赛道**（[参赛须知](https://www.nisp.org.cn/NewsDetail/5998353.html)）、**大学生人工智能安全竞赛**（[topsec](https://www.topsec.com.cn/newsx/6688)）：天融信系「作品赛」性质。
- **大学生服务外包创新创业大赛 A10 题**：安恒信息出题「基于大模型的自动化渗透测试系统」（[fwwb.org.cn](https://www.fwwb.org.cn/topic/show/f57634ea-a224-4720-a624-0901ba3f9460)）——直接对应「自研自动渗透/CTF agent」。
- **天池 AutoSec-Evo 赛道八**：基于 Hermes 自进化机制的多 Agent 编排智能渗透测试系统（[天池论坛](https://tianchi.aliyun.com/forum/post/1060480)）。
- **长亭「长亭外」AI 智能体夺冠**（[公众号文章](https://mp.weixin.qq.com/s/gFi3Lo1KFcjt0gwhetaT5w)）、[云鼎实验室：国内首个 AI 智能渗透挑战赛](https://cloud.tencent.cn/developer/article/2651925)——均为 TCH 系同生态的 AI 攻防叙事。

---

## 3. 开源 LLM 自动打 CTF 项目

### 3.1 verialabs/ctf-agent（国外标杆，最值得抄架构）
- 链接：<https://github.com/verialabs/ctf-agent>；**707⭐ / 111 fork**（Python）。
- 成绩：**BSidesSF 2026 全部 52/52 题、第 1 名**，覆盖 pwn/rev/crypto/forensics/web/misc 全类别。
- 架构（README 原文）：
  - **Coordinator LLM（协调者）** 管理整场比赛；对每道题派发一个 **Solver Swarm（求解 swarm）**，swarm 内**多模型同时并行攻击同一道题**，先拿到 flag 者胜。
  - 顶层 **Poller（每 5s）轮询 CTFd 平台** → Coordinator（Claude/Codex）→ 每个 swarm 内置多模型：Claude Opus(med/max)、GPT-5.4、GPT-5.4-mini、GPT-5.3-codex。
  - 每个求解器跑在**独立 Docker 沙箱**，预装全套工具：pwn（pwntools/ROPgadget/angr/unicorn/capstone）、rev（radare2/gdb/binwalk）、crypto（SageMath/RsaCtfTool/z3/gmpy2/cado-nfs）、取证（volatility3/Sleuthkit/foremost）、隐写（steghide/zsteg/zsteg/OCR）、web（curl/nmap/flask）、misc（ffmpeg/Pillow/PyTorch）。
  - 特性：自动发现新题、跨求解器共享洞察（message bus）、竞赛中可给运行中的 solver 发提示、多模型竞速、求解器「永不言弃」持续换思路。
- 接入方式：Python 3.14+、Docker、CTFd URL + CTFd token、Anthropic/OpenAI/Google API key、`claude` CLI / `codex` CLI。借鉴了 `es3n1n/Eruditus` 的 CTFd 交互。
- 对国内用户的启示：CTFd poller + 多模型 swarm 竞速 + Docker 沙箱是「Jeopardy 自动打靶」的标准答案骨架，可把模型换成 DeepSeek/GLM/Qwen。

### 3.2 MuWinds/BUUCTF_Agent（国内原版）
- 链接：<https://github.com/MuWinds/BUUCTF_Agent>；**256⭐ / 33 fork**（Python）。
- 功能：全自动解题（题目分析→靶机探索→代码执行→flag 分析全流程）、命令行交互式解题、本地 Bash 执行、可扩展 CTF 工具框架、可自定义 Prompt 与模型文件。
- 架构（DeepWiki 归纳）：**Multi-Agent LLM Pipeline**，把「思考推理」与「代码/指令编写」分派到**不同 LLM**；含 Solve Agent、Prompt Templates、Core Components；**支持 MCP**；支持 XBOW 平台（默认 `http://10.0.0.53:8000`）批量刷题。
- 接入方式：OpenAI Chat Completions 兼容格式（OpenAI SDK），改 `api_base` 即可接 **DeepSeek / Moonshot / vLLM / Ollama / one-api**；**不支持** Anthropic/Gemini 原生接口。
- 作者在 README 顶部的重要判断：**「时至今日，各路 Coding Agent 配合强模型已足够强，不再需要专门的 CTF Agent」**——即 2026 年用通用 coding agent（如 Claude Code 类）+ 强推理模型直接解题已足够，专门 CTF agent 的边际价值在下降。

### 3.3 gehewu/LLM-CTF-Solver（基于 BUUCTF_Agent 二次开发，最贴近国内环境）
- 链接：<https://github.com/gehewu/LLM-CTF-Solver>；**12⭐**（Python，Apache 2.0）。
- 定位：双模式（**CTF 自动解题 + 授权渗透测试**），CLI / TUI / Web UI 三入口，ReAct 范式。
- 架构分层：入口层（main/tui/backend FastAPI+Vue+WebSocket）→ Workflow 编排层（双流程 + 自动学习 + 质量门控 + 报告）→ **SolveAgent 推理引擎**（原生 tool_calls + 2 层回退、阶段感知 recon→exploit、**6 维僵局检测**、LLM/成本双熔断、断点续跑）→ Analyzer / Memory（**三层记忆 + 关键事实防丢**）/ ToolUtils（模式过滤）/ KnowledgeBase（**ChromaDB 混合检索 + 自动学习**）。
- 关键工程特性：**三层解析回退、六维僵局检测、三层记忆、三级缓存、RAG 知识库、攻击面结构化管理、断点续跑、writeup 自动导出（中断仍生成）**。
- 工具系统：20+ 内置工具，按模式过滤——CTF 专属（crypto_attacks/crypto_tools/codec/stego/forensics/reverse/binary_analysis/android/challenge_classifier）、渗透专属（web_tools/exploit_templates/powershell）、共享（ssh_shell/python/network/jwt/file_analyzer/mcp）。
- **关键：远程执行走 Kali SSH**（config 里填 `host/port/user/pass`，自动上传附件），Python 可本地 AST 沙箱或远程 SSH。
- 接入方式：**OpenAI 兼容 API**（`model/api_key/api_base` 四入口：solve_agent/analyzer/pre_processor/embedding），示例直接是 `openai/deepseek-v4-pro` + `https://api.deepseek.com`——即原生适配 DeepSeek，同样可接 GLM/Qwen。
- 这是「DeepSeek/GLM/Qwen + Kali 远程执行」最省事的国产起点。

### 3.4 其他相关开源
- **GreyDGL/PentestGPT**（渗透向 agent 框架）：<https://github.com/GreyDGL/PentestGPT>。
- **gitee：kill-life/pentest-gpt2（PentestGPT2）**：<https://gitee.com/kill-life/pentest-gpt2>。
- **es3n1n/Eruditus**：CTFd 交互/HTML 解析（被 ctf-agent 借用）。
- 检索 "gitee LLM CTF agent" 未见其他大规模国产 CTF-agent 项目，生态以 GitHub 为主。

---

## 4. 知乎 / CSDN / 公众号实战文章

- [国内最强 AI 渗透测试 Agent —— TCH 第二届智能渗透挑战赛唯一 AK 战队复盘](http://cn-sec.com/archives/5181921.html)（ChainReactor）：Cairn 系统 + Less is More + 成本数据，见 §2.2。
- [DataCon2024 解题报告 WriteUp — AI 安全赛道（冠军）](https://www.ctfiot.com/224443.html)：越狱/幻觉/RAG 三题完整思路。
- [AI CTF 方法论（GKLBB，cnblogs）](https://www.cnblogs.com/GKLBB/p/21609493)：中文 AI-CTF 方法论随笔。
- [AI 自动化攻防：人机协作在 CTF 竞赛中的实践与量化成效（腾讯云开发者）](https://cloud.tencent.cn/developer/article/2650349)：人机协作 + 量化成效。
- [使用 Ollama 辅助 CTF 比赛的详细指南（cn-sec）](https://www.cn-sec.com/)：本地模型（Ollama）辅助 CTF 的落地路径，适合无 GPU/离线场景参考。
- 国外学术/工程对照：EnIGMA（[ICML 2025](https://mlanthology.org/icml/2025/abramovich2025icml-enigma/)：交互式工具显著提升 LM agent 找漏洞）、[AI Is Solving CTF Challenges in Minutes](https://www.libhunt.com/posts/1530590-ai-is-solving-ctf-challenges-in-minutes)（即 verialabs ctf-agent 的报道）。

---

## 5. 对「自研 Jeopardy 式 CTF 打靶 agent」最有借鉴价值的 5 条经验

> 结合国内网络环境、可用模型（DeepSeek / GLM / Qwen）、Kali 远程执行等实际条件。

1. **骨架照抄「协调者 + 求解 swarm 多模型并行竞速」**（verialabs/ctf-agent），但用 OpenAI 兼容 API 统一接国产模型：一个协调者 LLM 拉取 CTFd 题目/分配优先级，每道题并发跑多个「求解器」，每个求解器绑定一个不同模型（DeepSeek-R1/V3、GLM-4、Qwen3 等），谁先出 flag 谁赢。Jeopardy 是「题目独立、可并行」的天然并行场景，多模型竞速比单模型串行收益最大。CTFd 交互建议直接参考 ctf-agent/Eruditus 的 poller + token 方式。

2. **环境隔离用「Kali SSH 远程执行 / Docker 沙箱」，而非在 agent 主机上裸跑**：预装全套工具（pwntools、angr、z3、SageMath、zsteg、volatility3、binwalk 等），本地只留一个 AST 沙箱。国内注意镜像源（pip/apt 换清华/阿里源）、以及 `child_process` 捕获子进程输出在受限沙箱可能被 EPERM 拦截的问题——执行用「继承 stdio」或直接走 SSH/Docker API，不要用管道捕获。

3. **「Less is More」：别做精细多 Agent 分工，别堆通用 RAG/Skill**（Cairn 血泪结论）。国产方案（BUUCTF_Agent、LLM-CTF-Solver）已经验证：一个 ReAct 主循环 + 阶段感知（recon→exploit）+ 若干「确定性工具」（题型分类、编码解码、flag 提交、checksec 等），比几十个互相低效通信的子 agent 更鲁棒、更省 token。知识库只保留「具体 PoC / 已做题目 writeup」级别的精炼检索（ChromaDB 自动沉淀），通识类 Skill 反而锁死上限。

4. **把「长任务鲁棒性」当工程主线**（LLM-CTF-Solver 的精华）：多层解析回退（原生 tool_calls 失败→JSON 回退→文本回退）、**僵局检测**（重复动作/无进展即换策略）、**断点续跑 + 检查点 + 三层记忆**、**成本熔断**（监控 token/费用，超预算自动降级到便宜模型或停手）。Jeopardy 单题往往要几十上百轮，没有这些机制，agent 会在 pwn 里空转烧钱。

5. **适配国内比赛的特殊规则**：西湖论剑 AI 赛道（game.gcsis.cn）**只开 API、题量超大、决赛代码审查**，所以你的 agent 必须：① 原生适配 CTFd/竞赛平台 API 而非浏览器自动化；② 支持「人工注入意图/改优先级」的人机协同接口（Cairn 就是靠第四天人工复盘日志才 AK）；③ 架构干净可讲解（评委要 code review）。同时备好「本地可用模型链路」：DeepSeek API 为主力推理，GLM/Qwen 做便宜的分类/摘要/并行试探，Ollama + 本地小模型做离线兜底（参考 Ollama 辅助 CTF 指南）。

---

## 附：关键链接索引

| 主题 | 链接 |
|---|---|
| 西湖论剑 AI Agent 赛道（官方） | https://www.dbappsecurity.com.cn/content/details6004_174117.html |
| DASCTF @ CTFtime | https://ctftime.org/ctf/1133 |
| TCH 智能渗透挑战赛官方 | https://zc.tencent.com/hackathon |
| TCH 唯一 AK 战队复盘 | http://cn-sec.com/archives/5181921.html |
| Cairn 拟开源 | https://github.com/oritera/Cairn |
| verialabs/ctf-agent | https://github.com/verialabs/ctf-agent |
| MuWinds/BUUCTF_Agent | https://github.com/MuWinds/BUUCTF_Agent |
| gehewu/LLM-CTF-Solver | https://github.com/gehewu/LLM-CTF-Solver |
| DataCon2024 AI 安全赛道 WP | https://www.ctfiot.com/224443.html |
| DataCon 百科 | https://baike.baidu.com/item/DataCon大数据安全分析竞赛 |
| 之江铸网大模型靶场 | https://www.sohu.com/a/920715198_122212240 |
| 护航丝路 AI 安全大赛 | http://www.gxzf.gov.cn/zt/jd/qmybrgznsd_231227/sssl/ywjjj_231458/t25894255.shtml |
