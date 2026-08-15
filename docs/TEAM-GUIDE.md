# CTF Agent 系统说明（团队版）

> 目标赛事：第九届西湖论剑「AI Agent 解题夺旗」（game.gcsis.cn，仅开 API，8/18 测试赛，8/21 初赛 3 小时）
> 代码：D:\ctf-agent（git 版本化）| 本文档随版本更新 | 当前版本：v0.3（7e50d89）

---

## 一、这个系统是什么（30 秒版）

一个"**人定战略、AI 打执行**"的 CTF 自动解题流水线：

```
比赛平台(API) ←拉题/交卷→ 指挥官(编排器) →派题→ 解题员(pi+DeepSeek) →调工具→ Kali(安全工具全家桶)
                                    ↑                                              
                            人（网页驾驶舱）：盯盘、写提示、复核 flag
```

比赛考的不是"人解题多快"，而是"谁能把安全能力沉淀成 Agent 的系统能力"——
本系统就是这套沉淀的载体：**调度、规划、解题、复盘、训练全部闭环**。

## 二、架构总览（给全队的图）

### 四层 + 一人机回路

| 层 | 组件 | 位置 | 职责 |
|---|---|---|---|
| 👤 人 | 驾驶舱 dashboard.py（网页） | 本机 8088 | 盯盘、写提示（hints）、复核提交 |
| ① 指挥官 | ctf_orchestrator.py + state/submit/workers/stuck/planning/platform 模块 | Windows | 黑板状态机、调度（并行+竞速+先易后难）、僵局检测、提交纪律 |
| ② 解题员 | pi（MIT 开源 agent 运行时）+ DeepSeek + 5 个题型技能包 | Windows 进程 | 每题独立 worker：思考→调工具→看结果→迭代 |
| ③ 工具箱 | Kali Linux（REST API 通道） | 10.174.153.128 | pwntools/angr/z3/sympy/gdb/nmap/sqlmap/binwalk/john/hashcat/radare2… |
| ④ 考场 | DASCTF 平台 API（dasctf_client + 平台适配层） | 外部 | 拉题/附件/交卷/查分 |

### 关键设计（为什么这么做）

1. **黑板状态机**：每道题一条记录（new→queued→solving→solved/dead/needs_hint），断点续跑、全程可审计（决赛代码审查友好）
2. **同题多模型竞速**：一道题同时派多个 worker（不同模型/思考档位），先出 flag 者胜，其余杀掉（冠军方案验证过的打法）
3. **规划器**：派 worker 前先用便宜模型生成 3-6 步解题计划注入提示词（提升路径规划能力）
4. **僵局检测**：实时读 worker 事件流，重复调用/错误率高/空转 → 杀掉重派并附警告（3 小时赛的省钱关键）
5. **提交纪律**：flag 去重、错误提交递增冷却 [0,15,60,180]s、每题材 3 错——防平台惩罚
6. **人机回路**：网页写提示 → 编排器下一轮自动注入；候选 flag 人工确认后提交（对应官方"人类四价值"考察）
7. **可替换性**：平台（BasePlatform 接口：mock/DASCTF/评测床）、模型（DeepSeek/百炼/任何 OpenAI 兼容）、执行后端（Kali/本机）都可换——比赛封什么都不慌

## 三、一次解题的完整过程（以 pwn 题为例）

1. 指挥官轮询平台 → 发现新题 → 黑板建记录 → triage（题型/难度/分值）
2. 按"先易后难"排序调度；规划器生成解题计划
3. 派 worker（如 pwn 路由 = v4-pro 高思考 + v4-flash 低思考 竞速）
4. worker 读题 → 在 Kali 上跑 checksec/file/strings → 分析漏洞 → 写 pwntools exp → 本地打通
5. 输出 "FLAG: xxx" → 指挥官抽取 flag → 提交纪律校验 → 交卷 → 黑板标记 solved
6. 若 3 分钟无进展/重复循环 → 僵局检测杀掉 → 带"换思路"警告重派

## 四、优点（答辩与信心）

| 优点 | 说明 |
|---|---|
| 冠军思想 + 自研代码 | 架构思想有 Cairn/verialabs/LLM-CTF-Solver 源码级出处，代码全部自研（无 AGPL 风险） |
| 全程可审计 | 状态机 + 每次尝试 JSON 记录 + 版本化（git）——决赛代码审查的底牌 |
| 真实题型验证 | 已自动解出：RSA 小指数、PNG 隐写、SSTI 漏洞利用、栈溢出 exp（mock 演练 4/4） |
| 可评测可训练 | CTFTiny（CSAW 真题）+ DASCTF 2025 真题双题库，版本-成绩对应记录（训练闭环） |
| 容错 | 崩溃不丢状态、孤儿进程自动清理、网络抖动重试、模型输出解析三层回退 |
| 成本可控 | 便宜模型规划/侦察 + 强模型攻坚的路由 + 僵局早杀 + 提交冷却 |

## 五、缺点与当前瓶颈（诚实版）

| # | 瓶颈 | 现状 | 影响 |
|---|---|---|---|
| 1 | v4-pro 强推理模型输出不稳（已定位 json 模式修复，待全量验证） | 部分场景输出为空 | 难题攻坚能力打折 |
| 2 | Kali 桥是"一次性命令"REST 通道，无交互式 pty | gdb/nc 交互调试做不了 | pwn/web 难题上限（学术结论：交互式工具是胜负手） |
| 3 | web/pwn 远程题需要起服务（CTFTiny 题是 docker 形式） | Kali 无 docker | L2 全量评测暂缺服务类题 |
| 4 | 单编排器进程、单机调度 | 无多机横向扩展 | 并发上限 ~4-6 worker |
| 5 | 规划器/僵局参数是拍脑袋初值 | 未经大规模调参 | 效果上限未知 |
| 6 | 平台 API 真实形态未知（8/18 才见） | 客户端是占位端点 | 测试赛当天有适配风险（有预案） |
| 7 | 无跨题经验共享（消息总线未实现） | 同类型题的发现不互通 | 重复踩坑浪费 token |
| 8 | 技能包 v1 内容偏薄 | 每类只有一页方法论 | 难题指导不足 |

## 六、可优化方向 → 队员任务分配（建议）

> 每个任务 = 明确产出 + 验收标准 + 对应瓶颈编号。按优先级排序。

| 优先级 | 任务 | 负责角色 | 产出/验收 | 对应瓶颈 |
|---|---|---|---|---|
| P0 | 平台 API 适配（8/18 测试赛） | 平台/运营 1 人 | 用 probe 脚本抓真实 API → 填 dasctf_client 端点 → 真实拉题/交卷闭环 | #6 |
| P0 | 测试赛盯盘 + 问题记录 | 全队轮值 | 问题清单进 M4 | - |
| P1 | v4-pro json 模式全量验证 + 模型路由调优 | 架构 1 人 | 用 CTFTiny 难题对比 flash/v4-pro 解题率与耗时，定路由表 | #1 |
| P1 | Kali 桥 SSH pty 通道 | 环境/工具 1 人 | 提供 Kali SSH 凭据后实现交互式 bash 工具，gdb 断点调试可用 | #2 |
| P1 | 服务类题支撑（Docker on Kali 或手动起服务） | 环境/工具 1 人 | CTFTiny web/pwn 远程题可跑起来进 L2 | #3 |
| P2 | 消息总线（跨题共享发现） | 架构 1 人 | 落盘版 message bus，同题型发现互通，验证减少重复踩坑 | #7 |
| P2 | 技能包 v2（按 L2 短板补） | 题型专家（每类 1 人） | 根据 L2 短板报告补方法论，重测有提升 | #8 |
| P2 | 僵局/规划参数调优 | 架构 1 人 | 用 L1/L2 数据调阈值，出"调参前/后"对比 | #5 |
| P3 | 多机横向扩展 | 架构 1 人（赛后） | 编排器支持远程 worker 池 | #4 |
| P3 | 看板增强（花费/实时日志流） | 架构 1 人（赛后） | 驾驶舱加成本面板 | - |

## 七、评测与训练（怎么持续变强）

- **题库**：CTFTiny（CSAW 真题 50 道，含 flag 真值，难度分级）+ DASCTF 2025 真题 13 道 + mock 演练题 4 道
- **评测流程**：L1 冒烟（easy 10 道）→ L2 全量 → 短板报告（postmortem.py）→ 针对性优化 → 重测
- **记录**：docs/EVAL-LOG.md（版本-成绩对照）+ git 提交记录
- **评测成绩**：（L1/L2 完成后填此表）

| 版本 | 题集 | 解题率 | 平均耗时 | 备注 |
|---|---|---|---|---|
| v0.3 | CTFTiny L1（8 道 easy，含 1 服务题） | 4/8 → 定位 8 个 bug | 1174s | 首次真题评测，暴露 csawctf 前缀/空格 flag/占位符/冷却等问题 |
| v0.5 | CTFTiny L1 静态（7 道 easy） | **7/7 全解** | 808s（平均 115s/题） | crypto 2/2、misc 1/1、rev 4/4；僵局检测实战立功（whataxor kill+重派后解出） |
| v0.5 | CTFTiny L2（easy+very_easy 全类，13 道，含 pwn） | 评测中 | 评测中 | 首次引入 pwn 真题与 v4-pro 竞速 |
| ... | DASCTF 2025 真题（13 道，7 有真值） | 待测 | 待测 | 清单已就绪 |

## 八、速查

- 启 mock 演练：`python src/mock_platform/mock_platform.py --port 7788`
- 跑编排器：`python src/ctf_orchestrator/ctf_orchestrator.py --once --workspace D:\ctf-agent\workspace`
- 看板：`python src/ctf_orchestrator/dashboard.py --port 8088` → 浏览器 http://127.0.0.1:8088
- 评测：`python src/ctf_orchestrator/eval_run.py --difficulty very_easy,easy --categories crypto,misc,rev`
- 复盘：`python src/ctf_orchestrator/postmortem.py`
- 测试赛探测：`python src/dasctf_client/probe_platform.py --base-url <URL>`
