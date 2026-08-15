# Benchmark 评测体系设计（对齐比赛考察点）

> 目的：架构实现后用它找问题、针对性优化，并在决赛展示"训练"过程。
> 位置：D:\ctf-agent\docs\BENCHMARK-EVAL.md | 2026-08-15 更新（题库扩容 + 完整性整改后）

---

## 一、题库全景（全部在 Windows 本地，真值不下 Kali）

| 题库 | 规模 | 可用 | 适配器 | 说明 |
|---|---|---|---|---|
| **CTFTiny**（CSAW 切片） | 50 题 | ~35 静态 | `CtftinyPlatform`（eval_platform.py） | CSAW 2021-22，`ctftiny.json` + 各题 challenge.json 含真值 |
| **NYU_CTF_Bench**（全量上游） | **test 200 + dev 57**（2013-2023） | 静态为主，服务题跳过 | 同 `CtftinyPlatform`（`--bench-root nyu-ctf-bench --bench-meta test_dataset.json`） | CTFTiny 的全量上游，同格式；`get_it?` 非法路径已净化（get_it_q） |
| **Cybench** | 40 题（4 赛事） | **19 静态**（21 服务题待容器） | `CybenchPlatform`（cybench_platform.py） | 难度 1-4 + subtasks；真值取 subtasks answer；easy/hard_prompt 作描述 |
| **DASCTF 2025 真题** | 13 题 / 7 有真值 | 7 | `DasctfEvalPlatform` | 赛味最正；description 已置空（防 writeup 开卷） |
| mock 假考场 | 4 题 | 4 | `MockHttpPlatform` | 全链路演练 |

**总可用题量：~50（CTFTiny 静态）+ ~200（NYU 静态）+ 19（Cybench 静态）+ 7（DASCTF）≈ 270 题**
——比原来 50+7 大 5 倍，且难度跨度 2013-2023。

## 二、评测完整性铁律（2026-08-15 三条事故后确立）

1. **真值与 worker 物理隔离**：题库数据只存 Windows，Kali 上只有 `/root/ctf/<cid>/` 工作区附件。
   曾发生：worker 在 Kali 直读 `/root/ctftiny` challenge.json 作弊（polly 复测抓现行）。
2. **prompt 不下发题解**：description 只用原始题目描述，绝不含 solve_notes/writeup 内容。
   曾发生：DASCTF solve_notes 整段进 prompt（开卷答题）。
3. **联网查公开题解 = OSINT 解，单独归类**：公开题库的题解/flag 在网上存在，worker 联网查到
   属真实 CTF 的合法手段，但**不计入能力分**。审计方法：worker 日志里出现 curl/wget/github
   → 标记 OSINT，成绩表单独一列。
4. 所有适配器 `to_prompt_json()` 走白名单（id/name/category/description/files/connection），
   raw 数据（flag/题解路径）永不进 prompt。

---

## 三、三层评测体系

```
L1 冒烟回归（每次架构改动后，<30 分钟）
   CTFTiny easy 10 题 → 自动跑，防退化

L2 全量能力（每个大版本）
   按题型×难度分组跑：CTFTiny 50 + NYU 200（静态子集）+ Cybench 19 + DASCTF 7
   产出：短板报告（哪类题、哪个难度区间失分）+ OSINT 解比例

L3 模拟赛（8/20）
   DASCTF 配比抽题 + 3 小时倒计时 + 人工盯盘（人只写 hints）
```

---

## 四、评测指标（逐条对齐官方考察点）

| 官方考察点 | 指标 | 采集方式 |
|---|---|---|
| 设计/调度 | 解题率（按题型×难度×是否 OSINT） | EvalPlatform 判题统计 |
| 有限时间 | 单题耗时分布、3h 模拟总分 | 计时日志 |
| 调度 | 并行利用率 | orchestrator 日志 |
| 优化 | 提交效率（错交率、冷却触发） | 提交日志 |
| 人机协作 | 人工介入率、hint 响应延迟 | hints 记录 |
| 训练 | 版本提升曲线（v1→v2→v3 解题率/耗时） | 历次 L2 报告 |
| 信息理解 | 题型分类准确率 | triage 日志 |
| 路径规划 | 规划采纳率 | planning 日志 |
| 成本 | 单题/全量 token 与费用 | 成本统计 |

---

## 五、评测-优化闭环（"训练/优化"考点）

```
实现/改动 → L1 冒烟（防退化）→ L2 全量（找短板）
   ↑                                      ↓
技能包/提示词/路由更新 ← 复盘（失败模式）← 短板报告
   ↓
重跑 L2 → 提升曲线表（version, solve_rate, avg_time, cost, hint_rate, osint_rate）
```

---

## 六、执行方式

```powershell
# CTFTiny 默认 50 题
python src/ctf_orchestrator/eval_run.py --platform ctftiny --config src/ctf_orchestrator/l2-config.json
# NYU 全量 200 题（test 集）
python src/ctf_orchestrator/eval_run.py --platform ctftiny --bench-root D:/ctf-agent/benchmarks/nyu-ctf-bench --bench-meta test_dataset.json --config src/ctf_orchestrator/l2-config.json
# Cybench 静态 19 题
python src/ctf_orchestrator/eval_run.py --platform cybench --config src/ctf_orchestrator/l2-config.json
# DASCTF 2025（7 题有真值）
python src/ctf_orchestrator/eval_run.py --platform dasctf2025 --config src/ctf_orchestrator/l2-config.json
```

历史成绩与事故记录见 `EVAL-LOG.md`（含 v0.11 完整性整改前后对比）。

---

## 附：待办

- [x] CTFTiny / NYU_CTF_Bench / Cybench 三库本地化（Windows，真值隔离）
- [x] 三个平台适配器（Ctftiny/NYU 共用、Cybench、DASCTF-eval）+ eval_run 接入
- [ ] 全量诚实重标定（270 题规模，需 Kali API 稳定后分批跑）
- [ ] 服务类题容器路线（Kali podman 镜像仓库被墙：代理/镜像源/Windows 拉取搬运，见 EVAL-LOG 短板#4）
- [ ] 按 DASCTF 题型配比的抽题表（L3 模拟赛用）
- [ ] OSINT 解自动标记（worker 日志 curl/github 关键词 → 成绩单单独列）
