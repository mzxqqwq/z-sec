# DASCTF 自动打靶系统 —— 详细设计（团队版 v2）

> 代码根 D:\ctf-agent | 读者：参赛团队全体 | 2026-08-15

## 目录
1. 组件与代码布局
2. 数据模型（黑板）
3. 核心流程（含伪代码）
4. 关键机制（交卷纪律/成本/失败处理）
5. 优势分析（对比三种替代方案）
6. 运行时全景
7. 实现里程碑（倒排到 8/18）
8. 团队协作与分工

---

## 1. 组件与代码布局

```
D:\ctf-agent\
├── src\
│   ├── ctf_orchestrator\ctf_orchestrator.py   # 指挥官：黑板+调度+交卷（自研）
│   ├── dasctf_client\dasctf_client.py          # 考场接口：平台 API 客户端（自研）
│   ├── pi-ext\kali.ts                          # Kali 桥：pi 工具转发扩展（自研）
│   ├── run-pi.ps1                              # 手动调试用启动器（勿用于编排；见下警示）
│   ├── mock_platform\mock_platform.py          # 假考场（演练用）
│   └── skills\                                 # 待建：web/pwn/re/crypto/misc 技能包
├── workspace\                                  # 运行时数据（黑板）
│   ├── state.json                              # 题目状态机
│   ├── hints\<cid>.md                          # 人工提示（写文件即注入）
│   └── challenges\<cid>\                       # 每题工作目录（附件+解题现场）
├── pi-mono\                                    # pi 运行时（已构建，MIT）
└── docs\                                       # 调研/拆解/本设计
```

职责边界（团队协作的关键）：
- **改平台适配** → 只动 dasctf_client.py 的 `EP` 数据类
- **改调度/交卷逻辑** → 只动 ctf_orchestrator.py
- **改 Kali 工具接入** → 只动 kali.ts
- **沉淀解题方法论** → 只写 skills/*/SKILL.md
- **换模型/加模型** → 改 ~/.pi/agent/models.json

---

## 2. 数据模型（黑板）

```json
// workspace/state.json —— 整场比赛的唯一真相源
{
  "challenges": [
    {
      "cid": "1",
      "raw": { "id": "1", "name": "...", "category": "pwn", "description": "..." },
      "status": "open",            // open → solving → solved；open → dead（放弃）
      "wrong_submits": 0,          // 错误提交计数（≤3，防封号）
      "attempts": [                // 每次派工的完整审计轨迹
        { "at": 1755230400, "elapsed": 312.5,
          "output_tail": "...worker 输出尾部...", "flags": ["DASCTF{...}"] }
      ]
    }
  ]
}
```

配套的三种"原语"（对齐冠军 Cairn 设计）：
- **Fact（事实）** = state.json 里每题的状态与尝试记录（客观、可审计）
- **Intent（意图）** = 每道 status=open 的题 = 一个待执行目标
- **Hint（提示）** = hints/<cid>.md，人随时写入；下一轮派工自动注入提示词

---

## 3. 核心流程（伪代码）

```
loop（每 N 秒一轮）:
  new = 平台拉题(challenges())            # ① sync
  对每个 status==open 的题 cid:           # ② dispatch（可并行化）
    下载附件 → challenges/<cid>/attachments/
    prompt = 模板(题目JSON, 附件路径, hints/<cid>.md 若存在)
    output = 启动 pi worker(prompt)        # ③ solve，超时 25 分钟
    记录 attempt{at, elapsed, output_tail}
    # 注：worker 由编排器直接 node 调 pi CLI（DEFAULT_PI_CMD + workers._worker_env 注入
    # key/KALI_API_URL）。切勿经 PowerShell 转发 prompt：PS5.1 会把含双引号的参数
    # 拆成多个 argv，以 - 开头的片段触发 pi "Unknown option" 秒退（事故见 EVAL-LOG）。
    flags = 正则抽取(output)               # ④ 交卷
    对 flags[:3] 依次:
      若错误提交数 < 3: r = 平台提交(cid, flag)
        r.correct → status=solved, break
        否则 wrong_submits += 1（提交间强制 5s 冷却，遇 429 指数退避）
    未解 → status=open（等人工 hint 或下一轮）
  写回 state.json
```

worker 提示词模板（最核心的"软件资产"，团队重点打磨对象）：

```
你是一名 CTF 选手，正在参加 DASCTF 竞赛。请独立解出这道题并给出 flag。
题目信息：{题目JSON}
附件已下载到 attachments/（如无附件则忽略）。
你的 bash/文件工具运行在一台装好 pwntools/angr/z3/sympy/nmap/sqlmap/...
的 Kali Linux 上。可自由写脚本、跑工具、连网络服务。
解出后以 "FLAG: <内容>" 一行输出最终答案。
{人工提示：hints 内容}
```

---

## 4. 关键机制

### 4.1 交卷纪律（决定名次的"脏活"）
- flag 抽取：多正则（flag{...}/DASCTF{...}/CTF{...}/32位hex 兜底）+ 去重保序
- 错误提交预算：每题 ≤3 次错误 → 超过即冻结该题（等人写 hint 再解冻）
- 冷却与退避：提交间隔 ≥5s；HTTP 429 按 Retry-After 指数退避

### 4.2 失败处理
- worker 超时 25 分钟强杀；输出尾部 4000 字节进审计轨迹
- 平台 401/403 立即停并通知人（可能账号掉线）
- 附件下载失败不阻塞解题（提示词里注明）

### 4.3 成本控制（规划中）
- 按题型/难度选模型：侦察 misc 用 flash，pwn/web 用 v4-pro
- 单题 token/费用熔断，超限降级或标记 dead
- 全队预算看板

---

## 5. 优势分析（团队答辩用）

| 对比对象 | 我们的优势 |
|---|---|
| **vs 纯人工** | 题量超人工上限，API-only 赛制下人海战术无效；我们 24h 并行 + 批量迭代 |
| **vs 直接套现成开源 CTF agent**（LLM-CTF-Solver 等） | 决赛要代码审查：用别人 12-star 项目=讲不清；我们是"冠军验证的 pi 底座 + 自研编排/桥/平台层"，每个组件可解释可答辩 |
| **vs 从零写 agent 循环** | pi 是多年迭代的工程（事件流/重试/流式/会话持久化/40+ provider），自写 3 天只能得到脆弱原型 |
| **独特护城河** | ① 人机 Hint 回路（冠军 Cairn 的胜负手：第四天靠人工注入翻盘）② 模型与执行后端双可替换（封模型/换靶场不慌）③ 题型技能包（把团队知识资产化）④ 交卷纪律自动化 |

---

## 6. 运行时全景（比赛当天）

```
Windows 本机:
  ctf_orchestrator.py  ── 每 120s 一轮调度
  ├─ 并行 N 个 pi worker 进程（每题一个，可同题多模型竞速）
  └─ 平台 API 交互（cookie 会话）
Kali:
  Kali Tools API(:5000) ── worker 的每次工具调用（命令式，root）
人:
  DSH 网页驾驶舱 ── 看日志/写 hints/改优先级/做决策
```

一次典型攻击节奏（pwn 题）：拉题+附件 → worker 跑 checksec/file/strings → 分析漏洞
→ 写 exp 脚本（Kali 上 pwntools）→ 本地打通 → 拿 flag → 编排器提交 → solved。

---

## 7. 实现里程碑（倒排）

| 里程碑 | 时间 | 内容 | 验收标准 |
|---|---|---|---|
| M0 | 8/15 今晚 | 修 Kali 桥；mock 全链路 | mock 平台 2 题自动解出并提交成功 |
| M1 | 8/15-16 | 并行 worker + 题型→模型路由 + 同题多模型竞速 | 3 题并发跑通；路由配置生效 |
| M2 | 8/17 | 往届 DASCTF 真题演练 3-5 道 | 至少 2 道全自动解出；沉淀技能包 v1 |
| M3 | 8/18-19 测试赛 | 抓真实 API→改 EP 端点→试打 | 平台真实闭环（拉题/交卷/查分）跑通 |
| M4 | 8/20 | 修测试赛暴露的问题；成本熔断 | 问题清单清零 |
| M5 | 8/21 初赛 | 实战 | 按赛况迭代 |
| M6 | 9月 决赛前 | SSH pty 交互工具 + 看板 + 答辩材料 | 代码审查演练通过 |

---

## 8. 团队协作与分工

| 角色 | 负责 | 产出 |
|---|---|---|
| 架构/编排（建议 1 人） | orchestrator、状态机、调度 | src/ctf_orchestrator/ |
| 题型专家（每类 1 人） | SKILL.md 方法论、解题验证、往届题 | src/skills/、演练报告 |
| 环境/工具（1 人） | Kali 工具链、桥、靶机连通 | kali.ts、Kali 清单 |
| 平台/运营（1 人） | 平台 API 适配、账号、盯盘 | dasctf_client.py、比赛日志 |

AI 开发代理（DSH，即本会话）承担：实现、测试、修 bug、集成团队产出、比赛日实时支援。
