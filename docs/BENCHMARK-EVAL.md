# Benchmark 评测体系设计（对齐比赛考察点）

> 目的：架构实现后用它找问题、针对性优化，并在决赛展示"训练"过程。
> 位置：D:\ctf-agent\docs\BENCHMARK-EVAL.md | 2026-08-15

---

## 一、选型结论

| Benchmark | 用途 | 理由 | 状态 |
|---|---|---|---|
| **CTFTiny**（主评测床） | 冒烟回归 + 全量能力评测 + 按题型找短板 | CSAW 真题 6 类 60+ 题；**难度分级**（Very Easy→Hard）；静态文件为主；`challenge.json` 含 **flag 真值**+附件清单+官方题源；题源和 DASCTF 同为北美主流 CTF 风格 | ✅ 已克隆到 Kali（含 Windows 无法落盘的 `?` 目录名题） |
| **NYU CTF-Bench**（深评测） | 全量 200 题 26 类、与 DASCTF 类别最重合 | 学术标准评测床（NeurIPS 2024）；harness MIT；题数据在独立仓库 | ✅ harness 已克隆（Windows） |
| **Cybench**（高难上限） | 40 道专业级题 + subtask 细分 | ICLR 2025 Oral；测高难题真实上限 | ⏳ 克隆中（Kali） |
| 方法论参考 | 人机协作评测框架 | 论文 2602.20446（41 人实地 CTF 人机协作首研）：人+AI vs 纯 AI 对比——正是赛制考察点 | ✅ 已读摘要 |
| 校准参考 | 饱和警告 | 论文 2604.24184：Jeopardy 基准趋于饱和 → 我们比的是**速度/成本/协作**，不是"能不能解" | ✅ 已读摘要 |
| **DASCTF 真题附件**（补充，赛味最正） | 13 道 2025 年真 DASCTF 题（crypto 3/misc 3/pwn 3/re 4，含 lost_LFSR_key/Serration/stegh/rcms 等） | GitHub BK-Sec/2025DASCTF；与 CTFTiny 混合成"赛味配比"题集 | ✅ 已克隆到 benchmarks/dasctf-2025-attachments |
| **DASCTF 官方出题指南** | 平台家族风格参考（love.dasctf.com 出题平台文档 + 各类 Docker 模板） | DASCTF-Offical/Docs | ✅ 已克隆 |

许可证：CTFTiny GPLv2（内部评测不分发，合规）；llm_ctf_automation MIT；cybench 无 LICENSE（仅内部评测）。

---

## 二、三层评测体系

```
L1 冒烟回归（每次架构改动后，<30 分钟）
   CTFTiny Very Easy + Easy 10 题 → 全部自动跑
   目的：改动没破坏已验证能力

L2 全量能力（每个大版本 v2.1/v2.2/...）
   CTFTiny 全量 60+ 题（按题型/难度分组）
   目的：产出"短板报告"（哪类题、哪个难度区间失分）

L3 模拟赛（初赛前，8/20）
   按 DASCTF 题型配比抽题 + 3 小时倒计时 + 人工盯盘（人只写 hints）
   目的：全流程实战压力测试
```

---

## 三、评测指标（逐条对齐官方考察点）

| 官方考察点 | 指标 | 采集方式 |
|---|---|---|
| 设计/调度 | 解题率（按题型×难度） | EvalPlatform 判题统计 |
| 有限时间 | 单题耗时分布、3h 模拟总分 | 计时日志 |
| 调度 | 并行利用率（并发槽占用率） | orchestrator 日志 |
| 优化 | 提交效率（错交率、冷却触发次数） | 提交日志 |
| **人机协作（人类价值）** | **人工介入率**（多少题靠 hints 才解出）、hint 响应延迟 | hints 记录 |
| **训练** | **版本提升曲线**（v1→v2→v3 的解题率/耗时对比表） | 历次 L2 报告 |
| 信息理解 | 题型分类准确率（triage 后与真值对比） | triage 日志 |
| 路径规划 | 规划采纳率（worker 是否按 plan 走、plan 有效性） | planning 日志 |
| 成本 | 单题/全量 token 与费用 | 成本统计 |

---

## 四、集成设计

1. **EvalPlatform 适配器**（实现我们 v2.1 的 `BasePlatform` 接口）：
   - `list_challenges()`：扫 Kali `/root/ctftiny/ctftiny.json` → NormalizedChallenge（含难度/题型/附件）
   - `download_files()`：从 Kali 取附件（base64 通道，复用编排器上传逻辑）
   - `submit_flag()`：比对 `challenge.json["flag"]`，返回 SubmitResult
   - 服务类题（web/nc）：适配器负责在 Kali 上起服务进程（无 Docker 时手动起；有 Docker 后切容器模式），题目描述注入连接信息
2. **评测脚本** `eval_run.py`：读配置（题集范围/模拟时长/并发）→ 起 EvalPlatform → 跑编排器 → 出 JSON + Markdown 报告（指标表 + 短板排行）
3. **训练闭环接入**：L2 报告 → 失败题复盘（worker 日志）→ 更新技能包/提示词/路由（版本化）→ 重跑 L1/L2 验证 → 生成提升曲线

---

## 五、评测-优化闭环（对应"训练/优化"考点，决赛展示材料）

```
实现/改动 → L1 冒烟（防退化）→ L2 全量（找短板）
   ↑                                      ↓
技能包/提示词/路由更新 ← 复盘（失败模式）← 短板报告
   ↓
重跑 L2 → 提升曲线表（version, solve_rate, avg_time, cost, hint_rate）
```

决赛答辩可展示：版本迭代记录 + 成绩曲线 + 关键改动与指标因果（"我们怎么训练这个 agent 的"）。

---

## 六、执行时机

- 现在：材料已就绪（CTFTiny 在 Kali；harness 在 Windows）
- 架构 v2.1 实现后（P0-P2 完成）：先 L1 冒烟 → 首轮 L2 全量 → 短板报告
- 每轮优化后：L1 + 针对性 L2
- 8/20：L3 模拟赛
- 许可证注意：GPLv2 仅内部评测使用，不对外分发衍生内容

---

## 附：待办

- [x] DASCTF 2025 真题附件（BK-Sec/2025DASCTF）已克隆
- [x] DASCTF 官方出题文档（DASCTF-Offical/Docs）已克隆
- [x] 平台 API 探测脚本 probe_platform.py 完成（已对 mock 验证）
- [x] M3 测试赛行动手册完成（docs/M3-TESTMATCH-PLAYBOOK.md）
- [ ] cybench 克隆到 Kali 完成（进行中）
- [ ] Kali 装 Docker（可选，服务类题容器化）
- [ ] 实现 EvalPlatform 适配器（架构 v2.1 的 BasePlatform 之后）
- [ ] eval_run.py + 报告模板
- [ ] CTFTiny 按难度抽题的配比表（模拟赛用）
- [ ] DASCTF 附件题的 flag 真值整理（部分题需从 writeup 补全）
