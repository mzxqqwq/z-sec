# CTF Agent · 西湖论剑 AI 解题夺旗系统

> 一套「AI Agent 自动打 CTF」的完整系统：Windows 上的 Python 编排器（指挥官）指挥多个
> pi 解题 Agent（开源 Node 运行时）在 Kali 工具机上执行命令，向比赛平台直接提交 flag；
> 附带 React Web 看板、四套本地 benchmark 题库与完整性审计，全程可观测、可纠偏、可复跑。

## ✨ 亮点

- **双模型竞速**：每道题同时派 1 强（`deepseek-v4-pro`）+ 1 弱（`deepseek-v4-flash`）两个 worker，
  谁先解出用谁的，兼顾质量与速度。
- **Supervisor / Observer 看板**：独立 pi 观察者会话旁路监督，维护 Idea/Memory 双层看板，
  纠偏提醒经工具落地（无 JSON 解析），注入下一轮提示词。
- **ralph-loop 续跑**：`agent_end` 未解出即强制续跑，模型无权宣布放弃——永远把当前题解出来。
- **服务题容器化靶机**：把 CSAW 公开 docker 镜像拉到 Kali，用 podman 起容器当靶机，重建早已下线的服务题。
- **完整性审计**：扫 worker 动作流，把每题分三档 `cheat / osint / clean`，防真值泄漏、单列「开卷解」。
- **断点续跑**：跑分进程重启不丢、可从归档快照恢复黑板继续，已解题不重复花 token。

## 🏗️ 架构

```
你的 Windows 机器（大脑）
┌──────────────────────────────────────────────────────────────┐
│ ctf_orchestrator.py   指挥官（状态机 / 一强一弱竞速 / 续跑 / 交卷）│
│   ├─ platform.py       平台抽象：拉题 / 交卷一个接口，换平台只换实现│
│   │    ├─ MockHttpPlatform   演练假考场（:7788）                 │
│   │    ├─ DasctfPlatform     真考场（8/18 探测端点后填）           │
│   │    └─ Ctftiny/NYU/Cybench/DASCTF 本地题库适配器（评测）       │
│   ├─ planning.py       总体思路（强模型，无门禁）                  │
│   ├─ supervisor.py     Observer 观察者会话：6 轮审查 / 看板 / 提醒 │
│   ├─ message_bus.py    同题双 worker 共享发现                    │
│   ├─ bench_admin.py    Benchmark 模块（跑分 / 归档 / 续跑）        │
│   ├─ revival.py        服务题容器运行（podman 起容器当靶机）        │
│   ├─ dashboard.py      人看板（Flask :8088 → http://127.0.0.1:8088/ui/）│
│   └─ eval_run.py       评测入口（跑 benchmark 出成绩单）           │
│        │ 派工：node cli.js --mode rpc                           │
│        ▼                                                       │
│ pi worker × N（解题员，每题 1 强 + 1 弱）                         │
│   ├─ kali.ts 扩展：bash/读写文件 → SSH → Kali；                  │
│   │   submit_flag / get_hint → worker-api(:8089)；              │
│   │   kb_search → KB 服务(:8099)                                │
│   └─ loop-detect.ts 扩展：循环软警告 / 阻止                      │
└───────────────────┼───────────────────────────────────────────┘
                    ▼ SSH（凭据在 secrets/kali.json，不入库）
Kali Linux（手，root 权限）
  /root/ctf/<cid>/w<idx>/   每 worker 独立工作区（附件同步到这）
  工具链：pwntools / angr / z3 / sympy / fpylll / blutter / stegseek / jadx…
  SageMath 10.9（podman 容器包装）+ pwndbg + gdb 17
  podman：benchmark 服务题起容器当靶机（worker 连 127.0.0.1 解题）
  ⚠️ benchmark 真值只存 Windows（评测隔离）
```

| 部件 | 是什么 | 谁做的 |
|---|---|---|
| 编排器 | 拉题 → 派工 → 监督 → 抽 flag → 交卷 → 续跑 | 自研（Python） |
| pi worker | 解题员：LLM「思考 → 调工具 → 看结果」循环 | 开源 pi（MIT）+ 自研 kali.ts/loop-detect.ts/observer.ts |
| Kali | 工具箱：所有命令 / 脚本 / exp 在这里执行 | 现成机器 + 工具链 |
| 平台适配器 | 考场抽象：拉题 / 交卷一个接口 | 自研（BasePlatform） |
| 看板 | 人机回路：看状态 / 写 hint / 复核提交 | 自研（Flask + React） |

## 📁 目录结构

```
ctf-agent/
├─ src/ctf_orchestrator/  指挥官全部代码（见下表逐模块）
├─ src/pi-ext/            pi 扩展：kali.ts / loop-detect.ts / observer.ts
├─ src/mock_platform/     假考场（Flask，4 道演练题）
├─ src/dasctf_client/     真平台客户端 + 端点探测脚本
├─ src/tools/             服务题镜像提取/构建/拉取脚本、冒烟回归
├─ ui/                    React 看板（Vite + TS，build 产出 dist）
├─ benchmarks/            题库：ctftiny(50) / nyu-ctf-bench(test 200 + dev 57) / cybench(40) / dasctf-2025(13)
├─ config/                统一配置中心：agent.json（模型/开关/providers）
├─ docs/                  全部文档（索引见文末）
├─ secrets/               API key、Kali SSH 凭据（已 gitignore，永不上库）
├─ workspace/             比赛运行时数据（state.json / hints / challenges）
├─ eval-workspace*/       benchmark 评测运行时数据（含归档与续跑快照）
├─ pi-mono/               pi 运行时（MIT 第三方，gitignored，需克隆构建）
└─ *-ref/                 参考实现源码 checkout（gitignored，只读研究用，见「致谢」）
```

### `src/ctf_orchestrator/` 各模块一句话

| 文件 | 职责 |
|---|---|
| `ctf_orchestrator.py` | 指挥官主程序：状态机 / 竞速 / 续跑 / conclude / worker-api / KB 懒启动 |
| `state.py` | 黑板：每 cid 状态机 + Idea/Memory 看板持久化 |
| `workers.py` | worker 进程管理：rpc 启动 / 环境注入 / 组杀 / 命令发送 |
| `planning.py` | Planner：强模型出总体思路 |
| `supervisor.py` | Observer：6 轮审查 / 看板维护 / 效率提醒（驱动独立观察者会话） |
| `message_bus.py` | 同题双 worker 共享发现（文件版总线） |
| `platform.py` | BasePlatform + Mock / Dasctf 适配器 |
| `eval_platform.py` | CTFTiny / NYU 适配器（本地数据源） |
| `cybench_platform.py` | Cybench 适配器 |
| `dasctf_eval_platform.py` | DASCTF 2025 真题适配器 |
| `eval_run.py` | 评测入口（`--platform/--only/--no-revive/--config…`） |
| `bench_admin.py` | Benchmark 模块：题库清单 / 跑分进程管理 / 归档 / 断点续跑 |
| `revival.py` | 服务题容器运行：podman 起容器当靶机 + `HOST_OVERRIDES` 特殊题 |
| `session_archive.py` | 比赛 workspace 会话归档（看板「归档」按钮） |
| `dashboard.py` | 看板 Flask + 全部 JSON API + `/ui` 静态 |
| `digest.py` | worker 日志 → 3 行中文摘要 |
| `kb_server.py` | 本地 KB 检索服务（:8099，可选） |
| `tracing.py` | 用量 / 成本聚合 |
| `preflight.py` | 赛前体检（含 SageMath / pwndbg / podman） |
| `postmortem.py` | 复盘报告（测试赛后用） |
| `audit.py` | benchmark 完整性审计（cheat / osint / clean 三档） |
| `agent_config.py` | 统一配置中心：读写 `config/agent.json` |

## 🚀 快速开始

### 环境要求

| 项 | 要求 |
|---|---|
| 编排机 | Windows（跑编排器 / worker / 看板） |
| 工具机 | 可达的 Kali Linux（SSH :22，装 pwntools/angr/z3 等） |
| Node | ≥ 22.19.0（`pi-mono` 运行时要求） |
| Python | ≥ 3.10（`flask` / `requests` / `psutil`） |
| pnpm | UI 构建用（`ui/`） |

### 依赖安装

完整步骤见 `docs/INSTALL.md`（含 Kali 工具链 / SageMath / pwndbg / podman 的一次性脚本），此处概括：

- **Python**：`pip install flask requests psutil`（看板 / HTTP 调用 / 清理孤儿 worker）。
- **Node + pi 运行时**：`pi-mono/` 是第三方 pi 运行时（gitignored，需先克隆构建）——
  `git clone https://github.com/earendil-works/pi.git pi-mono`，再 `cd pi-mono && npm install && npm run build`；
  之后 `cd src/pi-ext && npm install`（`ssh2` + `typebox`）。
- **UI**：`cd ui && pnpm install && pnpm build`（产出 `dist/`，看板静态页依赖它）。
- **Kali**：SSH 可达，凭据在 `secrets/kali.json`（不入库）；工具链 / 镜像一次性脚本见 `docs/INSTALL.md`。
- **体检**：`python src/ctf_orchestrator/preflight.py`（13 项全绿才开赛）。

### 配置（secrets 永不上库）

- 模型角色、运行时开关、providers 集中在 `config/agent.json`；真实 API key 存
  `config/secrets.json`（已 gitignore）。两者都可在 Web UI「配置」页在线编辑，无需改代码：
  ```jsonc
  // config/agent.json（节选）——key 用环境变量占位符，不落明文
  {
    "llm": {
      "strong":  { "model": "deepseek-v4-pro",  "thinking": "medium" },
      "weak":    { "model": "deepseek-v4-flash", "thinking": "low" }
    },
    "providers": [
      { "id": "deepseek", "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "models": ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat"] }
    ]
  }
  ```
- pi 运行时另有一份 provider 注册表 `%USERPROFILE%\.pi\agent\models.json`（加 GPT/Claude 时改这里）。
- 兼容旧配置：`secrets/deepseek.key` 仍作 deepseek key 兜底；`src/ctf_orchestrator/l2-config.json`
  是旧评测配置（现仅作 `--config` 显式覆盖时用，默认已走 `config/agent.json`）。

### 三条启动命令

```powershell
cd D:\ctf-agent

# ① 看板 + API（浏览器 http://127.0.0.1:8088/ui/）
python src/ctf_orchestrator/dashboard.py --workspace D:/ctf-agent/workspace --port 8088

# ② benchmark 评测（CTFTiny 50 题；模型配置默认走 config/agent.json，--config 可显式覆盖）
python src/ctf_orchestrator/eval_run.py --platform ctftiny

# ③ 比赛编排器（先起 mock 假考场演练；真平台换 --platform dasctf）
python src/mock_platform/mock_platform.py --port 7788
$env:DASCTF_BASE_URL = "http://127.0.0.1:7788"
python src/ctf_orchestrator/ctf_orchestrator.py --loop 60 --platform mock ^
     --workspace D:/ctf-agent/workspace
```

打开 UI：**http://127.0.0.1:8088/ui/**（旧模板页在 http://127.0.0.1:8088/）。

### 端口总表

| 端口 | 服务 | 谁起 |
|---|---|---|
| 7788 | mock 假考场 | `python src/mock_platform/mock_platform.py --port 7788` |
| 8088 | 看板 / UI + API | `dashboard.py` |
| 8089 | worker-api（提交 / 提示回调） | 编排器启动时自动 |
| 8099 | 本地 KB 检索（可选） | `kb_enabled` 时自动拉起，或手工 `python src/ctf_orchestrator/kb_server.py` |
| 22 | Kali SSH | Kali 自带 |

## 📊 Benchmark 评测

### 题库（真值隔离，只存 Windows）

| 题库 | 规模 | 可用 | 适配器 |
|---|---|---|---|
| **CTFTiny**（CSAW 切片） | 50 题 | ~35 静态 | `CtftinyPlatform` |
| **NYU_CTF_Bench**（全量上游） | test 200 + dev 57 | 静态为主 | 同 `CtftinyPlatform`（`--bench-root` 切换） |
| **Cybench** | 40 题 | 19 静态 | `CybenchPlatform` |
| **DASCTF 2025 真题** | 13 题 | 7 有真值 | `DasctfEvalPlatform` |

### 命令行跑分

```powershell
# CTFTiny 50 题（模型配置默认走 config/agent.json，无需 --config）
python src/ctf_orchestrator/eval_run.py --platform ctftiny
# NYU test 集 200 题
python src/ctf_orchestrator/eval_run.py --platform ctftiny --bench-root D:/ctf-agent/benchmarks/nyu-ctf-bench --bench-meta test_dataset.json
# Cybench 静态 19 题 / DASCTF 2025（7 题）
python src/ctf_orchestrator/eval_run.py --platform cybench
python src/ctf_orchestrator/eval_run.py --platform dasctf2025
```

常用参数：`--only <cid>`（先单题试）、`--difficulty easy,moderate`、`--categories crypto,rev`、
`--exclude <cid>`、`--max-rounds 4 --max-attempts 3`、`--workspace <dir>`、`--no-revive`（关闭容器运行）。

### 在 UI 里跑分（推荐）

看板左侧栏「◈ Benchmark 跑分」页，**不用开第二个终端**：

1. 起看板（命令 ①），打开 http://127.0.0.1:8088/ui/ → 左侧栏点「Benchmark 跑分」；
2. 点选一张题库卡（显示题数 / 真值数 / 分类分布）→ 可加难度、题型、指定/排除 cid 过滤；
3. 点「开始跑分」→ 面板实时显示状态 / 耗时 / 成绩 / 成本，可随时「停止」；
4. 每轮跑分自动归档到 `eval-workspace-bench/runs/<run_id>/`，历史表可只读回看。

### 服务题容器运行（默认开启）

服务题的 box 是 CSAW 当年公网靶机（已下线）。做法：把官方镜像拉到 Kali，`podman run`
起容器，worker 连 `127.0.0.1:<端口>` 解题。**默认开启，无需开关**；缺镜像的题自动跳过
（日志 `image_missing`），跑分结束自动清理容器。紧急关闭加 `--no-revive`。一次性拉镜像：

```bash
# Kali 上（一次性，之后跑 benchmark 不需要 VPN）
sudo bash /root/pull-ctftiny.sh
```

### 完整性审计

跑分历史表每题有「审计」按钮：扫 worker 动作流，把每题分三档

- **cheat**：疑似真值直读（`challenge.json` / flag 真值文件 / 题库残留路径 / podman 读容器）；
- **osint**：联网查公开题解（curl/wget/git 到 github / 搜索引擎 / writeup）——真实比赛合法，但单列、不算能力解；
- **clean**：以上皆无。

也可命令行：`python src/ctf_orchestrator/audit.py --run <run_id>`。

### 断点续跑

- 看板重启**只杀看板进程本身**，跑分子进程继续跑，重启后按 `run.pid` 自动收养；
- 跑分中途死掉 → 历史表该行出现「续跑」按钮：从归档快照恢复黑板，已解题保持 solved，
  未解的从断点继续，不重复花 token（新记录标记 `resumed_from`）。

## 🏁 比赛模式（8/18 测试赛 / 8/21 初赛）

```powershell
# 演练（假考场）：终端 A 起 mock
python src/mock_platform/mock_platform.py --port 7788

# 终端 B 编排器（--loop 60 每 60 秒一轮；--once 用于调试）
$env:DASCTF_BASE_URL = "http://127.0.0.1:7788"
python src/ctf_orchestrator/ctf_orchestrator.py --loop 60 --platform mock ^
     --workspace D:/ctf-agent/workspace

# 终端 C 看板
python src/ctf_orchestrator/dashboard.py --workspace D:/ctf-agent/workspace

# 真平台：8/18 探测端点后填 src/dasctf_client/dasctf_client.py 的 EP 数据类，
# 然后 DASCTF_BASE_URL 指向官方地址 + --platform dasctf，其余不变。
```

**盯盘（人机回路）**：详情页看 3 行中文摘要 → 卡住的题（橙色「待提示」）写 **hint 纠偏**
（下一轮自动注入）→ 想人工把关时开「复核模式」，候选 flag 由你点「确认提交」后才由编排器代交。
hint 写法：一次一个方向、指向具体线索（如「看 PNG 文件尾部的 base64」）。

## 🔧 关键机制

- **Supervisor（Observer）**：对齐 BreachWeave——起一个**独立 pi 观察者会话**
  （`src/pi-ext/observer.ts`，不加载 kali.ts），模型通过看板工具（`board_list/idea_add/…/
  memory_delete/send_efficiency_reminder`）直接维护 Idea/Memory 看板；编排器只读回
  `board.json` 合并进黑板，**不解析模型输出**（杜绝推理模型空输出事故）；效率提醒带冷却去重，
  纠偏注入下一轮提示词。
- **完整性审计**：三档 `cheat / osint / clean`，铁律 = 真值与 worker 物理隔离、
  prompt 不下发题解、联网查公开题解单列 OSINT（见「Benchmark 评测」）。
- **服务题容器运行**：`revival.py` 用 Kali podman 起容器当靶机；多服务题按 compose 顺序起，
  `HOST_OVERRIDES` 表处理 docker-in-docker 特殊题（如 msc-showdown）。
- **断点续跑**：ralph-loop（模型无权宣布放弃）+ 跑分快照归档 / 收养 / 续跑（见「Benchmark 评测」）。

## ⚠️ 已知限制

- **46 道中央 runner 托管题**（目录无 Dockerfile/compose，当年跑在 CSAW 统一 checker 上）
  无法起容器，清单见 `src/tools/_check_blind.py`。
- 服务题镜像拉取需一次性走 VPN/代理；docker-in-docker 题行为可能降级。
- 评测完整性历史：早期版本曾在 Kali 直读真值、DASCTF solve_notes 开卷（已修复，见 `docs/EVAL-LOG.md`）。
- Kali 是单点依赖（无自动拉起 / 本机降级）；SSH pty 交互（gdb/nc）视赛题形态待定。
- 真平台端点需 8/18 探测后填写；提交纪律等平台规则回来后再启用（`submit.py` 已 parked）。

## 📚 文档索引

| 文档 | 看什么 |
|---|---|
| `docs/INSTALL.md` | 双机从零安装（Windows 编排机 + Kali 执行机） |
| `docs/SECRETS-CHECKLIST.md` | 发布前清理与密钥清单 |
| `docs/使用手册-完整版.md` | 系统全景 / 端口 / 目录 / 操作 / FAQ（README 素材来源） |
| `docs/系统架构说明.md` | 新手向：全景图 + 一次解题 10 步 |
| `docs/定版方案-最终.md` | 最终设计决策与源码证据 |
| `docs/服务题容器运行.md` | benchmark 服务题起容器（含 showdown 特殊题） |
| `docs/BENCHMARK-EVAL.md` | 题库全景与评测方法 |
| `docs/EVAL-LOG.md` | 历次评测成绩与事故 |
| `docs/KALI-INVENTORY.md` | Kali 资产清单 |
| `docs/TEAM-GUIDE.md` / `RACEDAY-CARD.md` / `M3-TESTMATCH-PLAYBOOK.md` | 团队 / 比赛日操作 |
| `docs/analysis/` | 五仓库源码拆解报告（含 BreachWeave） |

## 📄 许可证与致谢

- **pi 运行时**（`pi-mono/`）：第三方开源项目 [earendil-works/pi](https://github.com/earendil-works/pi)，
  **MIT 许可证**，本仓库仅引用其运行时（gitignored，需克隆构建）。
- **参考研究**（逐行源码证据见 `docs/analysis/`）：
  - BreachWeave —— 腾讯黑客松第二期冠军方案（Observer 旁路监督 + ralph-loop + Idea/Memory 看板）；
  - [verialabs/ctf-agent](https://github.com/verialabs/ctf-agent) —— 一强一弱竞速（FIRST_COMPLETED）；
  - [ahmedreda38/Koshary](https://github.com/ahmedreda38/Koshary) —— planning 复用求解模型、BasePlatform 抽象；
  - [gehewu/LLM-CTF-Solver](https://github.com/gehewu/LLM-CTF-Solver) —— SSH 通道、三层解析回退、checkpoint 断点续跑。
- 其余编排器 / 扩展 / 看板为自研代码。
