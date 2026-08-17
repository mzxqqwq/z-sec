# CTF Agent 架构说明（团队版 · 定版）

> 目标赛事：第九届西湖论剑「AI Agent 解题夺旗」（game.gcsis.cn，8/18 测试赛 / 8/21 初赛 3h）
> 代码：D:\ctf-agent（git）| 本文档 = 架构全景 + 定版决策记录 + 瓶颈与分工（合并自 TEAM-GUIDE / 系统架构说明 / ARCHITECTURE v1 / 定版方案）
> 配套阅读：`USER-MANUAL.md`（操作）、`RACEDAY.md`（比赛日）、`BENCHMARK.md`（评测与隔离）、`INSTALL.md`、`SECRETS-CHECKLIST.md`

---

## 1. 一句话定位

**一个"人定战略、AI 打执行"的 CTF 自动解题流水线**：Python 编排器（自研）指挥多个 pi 解题员（开源 agent 运行时 + DeepSeek），在 Kali 上干活，向比赛平台交 flag。

```
比赛平台(API) ←拉题/交卷→ 编排器(指挥官) →派题→ pi worker ×N(解题员) →SSH→ Kali(工具全家桶)
                              ↑
                    人（网页看板 8088）：盯盘、写 hint、复核 flag
```

- 自研：编排器（调度/状态机/监督/看板）、kali.ts 桥、平台适配层、UI；
- 复用：pi 运行时（MIT，腾讯黑客松 AK 战队同款底座）、Kali 工具链、DeepSeek 模型。

## 2. 全景图（谁在哪、谁跟谁说话）

```
你的 Windows 机器（大脑）
┌────────────────────────────────────────────────────────────┐
│ ctf_orchestrator.py   指挥官（状态机/一强一弱竞速/续跑/交卷）   │
│   ├─ platform.py      平台抽象（拉题/交卷一个接口，换平台只换实现）│
│   │   ├─ DasctfPlatform        真考场（game.gcsis.cn，8/18 抓端点）│
│   │   ├─ MockHttpPlatform      演练假考场（7788）              │
│   │   ├─ CtftinyPlatform       评测题库（CTFTiny/NYU）         │
│   │   ├─ CybenchPlatform       评测题库（Cybench 40 题）       │
│   │   └─ DasctfEvalPlatform    评测题库（DASCTF 2025 真题）    │
│   ├─ planning.py      总体思路（强模型，无门禁）               │
│   ├─ supervisor.py    观察者会话驱动：6轮审查/看板/提醒          │
│   │   + pi-ext/observer.ts（独立 pi 会话，看板经工具落地）      │
│   ├─ message_bus.py   同题双 worker 共享发现                  │
│   ├─ bench_admin.py   Benchmark 模块（跑分/归档/续跑）         │
│   ├─ dashboard.py     人看板（http://127.0.0.1:8088/ui/）    │
│   └─ eval_run.py      评测入口（跑 benchmark 出成绩单）        │
│        │ 派工：node cli.js --provider deepseek --mode rpc     │
│        ▼                                                    │
│ pi worker × N（每题 1 强 deepseek-v4-pro + 1 弱 v4-flash 竞速）│
│   ├─ kali.ts：bash/read/write/edit → SSH → Kali；             │
│   │   submit_flag/get_hint/check_findings → worker-api:8089； │
│   │   kb_search → KB:8099                                    │
│   └─ loop-detect.ts：循环软警告/阻止                          │
└────────┼───────────────────────────────────────────────────┘
         ▼ SSH（root，secrets/kali.json）
Kali Linux（手）
  /root/ctf/<cid>/w<idx>/   每 worker 独立工作区（附件同步到这）
  工具链：pwntools/angr/z3/sympy/fpylll/blutter/stegseek/jadx/
         SageMath 10.9（podman 容器包装）+ pwndbg + gdb 17
  podman：benchmark 靶机容器（127.0.0.1 高端口）+ worker 隔离容器（见 BENCHMARK.md）
  ⚠️ benchmark 真值只存 Windows（评测隔离铁律）
```

## 3. 一次解题的 10 步（跟一遍就懂）

1. **拉题**：`platform.list_challenges()` → NormalizedChallenge（题名/分类/描述/附件/连接），存进黑板；
2. **入黑板**：`workspace/state.json` 记录，状态 = new；
3. **planning**：planner（强模型）出总体思路，注入 worker 提示词；
4. **派工**：每题 1 强 + 1 弱双 worker **竞速**（谁先出 flag 谁赢），并发上限 3 题；
5. **worker 干活**：pi 循环"思考→调工具"；附件同步到 Kali `/root/ctf/<cid>/w<idx>/attachments/`；
6. **监督**：supervisor 每 6 轮审查一次（10 轮窗口）——起独立 pi 观察者会话（observer.ts，不加载 kali.ts），观察者经看板工具（board_list/idea_add/…/send_efficiency_reminder）直接维护 Idea/Memory 看板；编排器只读回 board.json 合并进黑板，**不解析模型输出**（对齐 BreachWeave，杜绝推理模型空输出事故）；效率提醒带冷却去重，纠偏注入下一轮提示词；
7. **抽 flag**：worker 输出正则抽候选 flag（黑名单滤占位符）；
8. **提交**：worker 经 submit_flag 工具提交（worker-api:8089 回调），编排器统一判对错；
9. **未解续跑**：agent_end 未解 → 注入"继续推进"强制续跑（模型无权宣布放弃）；deadline-90s 才 conclude 收尾；
10. **记成绩**：对 → solved；错/卡 → 下一轮自动续派，或 needs_hint 等人工提示。

## 4. 状态机（定版：无 triage / 无僵局击杀 / 无放弃）

```
new → queued → solving（一强一弱竞速）→ solved
    ↘ needs_hint（人工提示或下一轮自动续派）←── 未解自动续派（预算内）
```

五个状态，没有 dead/放弃：**永远把当前题解出来**（AK 导向，中心原则）。

## 5. 数据都放在哪（比赛时找东西看这里）

| 路径 | 内容 |
|---|---|
| `workspace/state.json` | 黑板：每题状态/尝试记录/Idea·Memory 看板（唯一真相源） |
| `workspace/challenges/<cid>/` | 附件 + worker_*.log（解题全过程事件流） |
| `workspace/hints/<cid>.md` | 人工提示：写文件即注入下一轮 worker 提示词 |
| `workspace/requests/confirm/<cid>.json` | 人工复核：写 {"flag": "..."} 由编排器代交 |
| Kali `/root/ctf/<cid>/w<idx>/` | 每 worker 远程执行现场（附件、脚本、exp） |
| `eval-workspace-bench/` | Benchmark 专用黑板（看板 Benchmark 页跑分） |
| `eval-workspace-bench/runs/<id>/` | 每轮跑分归档（可回看、可续跑） |

## 6. 定版关键决策记录（与源码证据对应，答辩用）

| 决策 | 依据 |
|---|---|
| Observer 全套参数抄 BreachWeave | observer-agent.ts（判断三步闭环/三连自问/提醒四前提/防刷屏）；2026-08-16 升级为全架构对齐：独立 pi 会话 + 工具落动作（无 JSON 解析），修复推理模型空输出事故 |
| ralph-loop 续跑抄 BreachWeave | ralph-loop.ts：平台判定完成，模型无权宣布结束 |
| Idea/Memory 双层看板 | memory.ts：Observer 写 / Solver 只读 / 编排器落盘 |
| 一强一弱竞速 | verialabs FIRST_COMPLETED（swarm.py）+ 成本压缩为 2 路 |
| planning 只出总体思路、用强模型 | Koshary plan 复用求解模型 + 用户决策 |
| SSH 通道 | LLM-CTF-Solver ssh_client 同构 + 交互式工具刚需 |
| 删 triage/提交纪律/僵局击杀/路由/fallback | 用户决策（AK 导向、平台规则未定、模型可达） |
| UI 强可观测弱干预 + 人工 hint 通道 | BreachWeave ui-web 形态 + 官方考察点"人类价值" |
| benchmark 网络封锁 + worker 容器隔离 | 公开题库防"开卷抄解"；比赛不禁网（OSINT 允许） |

## 7. 优点（答辩与信心）

- **冠军思想 + 自研代码**：架构思想有 Cairn/verialabs/BreachWeave/LLM-CTF-Solver 源码级出处，代码全部自研（无 AGPL 风险）；
- **全程可审计**：状态机 + 每次尝试 JSON 记录 + git 版本化——决赛代码审查的底牌；
- **可评测可训练**：CTFTiny(50) + NYU(257) + Cybench(40) + DASCTF 2025(13) 四题库，版本-成绩对应（训练闭环见 BENCHMARK.md）；
- **容错**：崩溃不丢状态、孤儿进程自动清理、SSH 自动重连、模型输出解析多层回退；
- **双可替换**：平台（BasePlatform）与模型（config/agent.json 统一配置中心）都可换。

## 8. 当前瓶颈（诚实版）

| # | 瓶颈 | 现状 | 影响 |
|---|---|---|---|
| 1 | 强模型输出稳定性 | 推理模型偶发空输出（已用"工具落动作不解析 JSON"规避） | 难题攻坚 |
| 2 | Kali 单机 3.8G 内存 | 24 靶机容器 + 6 worker 需 swap 支撑 | 并发上限 |
| 3 | 平台 API 真实形态未知（8/18 才见） | 客户端端点占位，测试赛当天适配 | 有预案（RACEDAY.md） |
| 4 | 无跨题经验共享（message bus 仅同题双 worker） | 同题型发现不互通 | 重复踩坑 |
| 5 | 技能包 v1 内容偏薄 | 每类一页方法论 | 难题指导不足 |

> 历史已修复：v4-pro 空输出、flag 前缀截断/空格/占位符误交、僵局击杀烧预算、Kali 单点故障白跑（健康闸门）——详见 `_archive/EVAL-LOG.md`。

## 9. 可优化方向 → 队员任务分配（建议）

| 优先级 | 任务 | 负责角色 | 产出/验收 | 对应瓶颈 |
|---|---|---|---|---|
| P0 | 平台 API 适配（8/18 测试赛） | 平台/运营 | probe 抓真实 API → 填 dasctf_client 端点 → 真实闭环 | #3 |
| P0 | 测试赛盯盘 + 问题记录 | 全队轮值 | 问题清单进 M4 | - |
| P1 | 模型路由调优（v4-pro vs flash 对比） | 架构 | 用 CTFTiny 难题对比解题率与耗时 | #1 |
| P1 | SSH pty 交互通道（gdb/nc） | 环境/工具 | 交互式 bash 工具，断点调试可用 | - |
| P2 | 消息总线跨题共享 | 架构 | 同题型发现互通，减少重复踩坑 | #4 |
| P2 | 技能包 v2（按 L2 短板补） | 题型专家 | 按短板报告补方法论，重测有提升 | #5 |

## 10. 速查

- 跑编排器（比赛）：`python src/ctf_orchestrator/ctf_orchestrator.py --once --workspace D:\ctf-agent\workspace`
- 看板：`python src/ctf_orchestrator/dashboard.py --port 8088` → http://127.0.0.1:8088/ui/
- 评测：`python src/ctf_orchestrator/eval_run.py --platform cybench`
- 赛前体检：`python src/ctf_orchestrator/preflight.py`（要求全绿）
- 模型/并发配置：`config/agent.json`（Web UI「⚙ 配置」页可视化改）

---

*调研报告与第三方源码拆解（Cairn/verialabs/Koshary/pi/BreachWeave）在本地 `docs/_archive/`，不进 GitHub。*
