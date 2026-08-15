# UI 改版完整方案（v1 供评审，未实施）

> 依据：拆解 Dest1ny-Sec/dhunter 前端（Vue3 设计系统，1172 行 main.css + 12 个 UI 原语，
> 设计语言 "stargaze"：夜空蓝黑底 + 多色星点 + 玻璃模块 + 克制动效）。
> 对照：我们现有 ui/（React+TS，极简暗色表格+卡片，无视觉体系）。
> 本文先出方案，用户确认后再改代码。

---

## 一、现状问题诊断（为什么不满意）

1. **无设计体系**：平铺表格 + 灰色卡片，只有功能没有"形状"——信息没有层级，盯盘 3 小时会疲劳；
2. **无品牌感**：打开像内部调试页，不像一个"比赛驾驶舱"；
3. **信息密度低但视觉噪音高**：表格 9 列挤在一起，状态只用文字徽标，看不到"现在该关注什么"；
4. **详情页缺"活"的感觉**：日志是静态折叠列表，没有流式滚动、没有错误高亮节奏；
5. **无全局态势**：没有总览条（已解几题/花了多少钱/还剩多少时间）。

## 二、设计语言（直接采用 dhunter 的 stargaze，适配"夺旗"主题）

### 主题概念：「星图夺旗」

> 每题一颗星：灰=未触及、蓝=解题中（星光流转）、绿=已夺取、琥珀=待提示（微光求救）。
> 比赛 = 在星图上点亮旗帜。品牌名沿用「CTF Agent 驾驶舱」→ 建议改为「星图 · CTF Agent」。

### 设计令牌（全部从 dhunter 提取，纯 CSS 变量可移植）

| 组 | 令牌 | 值 |
|---|---|---|
| 背景 | --bg / --bg-mid / --bg-elev | #060a1a / #0a1124 / rgba(14,22,50,.7) |
| 边框 | --border / --border-bright | rgba(125,146,232,.16) / rgba(167,184,255,.34) |
| 文字 | --text / --text-dim / --text-faint | #e6ecfa / #8a96bc / #5b6685 |
| 主色 | --stellar（恒星蓝） | #7d92e8（bright #a3b4ff / dim #4f63b8） |
| 辅色 | --nebula（星云紫）/ --aurora（极光青）/ --star-amber（星琥珀） | #9b8ce8 / #5fc8d4 / #e8c879 |
| 语义 | --ok / --warn / --danger | #5fc89a / #d9a861 / #e26472 |
| 状态 | solving=stellar-bright / solved=--ok / needs_hint=--star-amber / new=--text-faint | 见色彩语义表 |
| 形状 | --radius 10px / sm 6px / lg 14px | |
| 光晕 | --shadow-glow | 0 4px 24px rgba(125,146,232,.18) |
| 字体 | display Space Grotesk / body Inter / mono JetBrains Mono | 离线环境降级：本机系统栈近似 + 可选内嵌字体文件 |

### 背景与质感（dhunter 的招牌，CSS 可完整移植）

- **星空场**：body::after 多层 radial-gradient 星点（冷白/冷蓝/青色三色调，30+ 颗）+ 银河带渐变 + 星云柔光斑；
- **噪点纹理**：body::before SVG feTurbulence 噪声（opacity .05, mix-blend overlay）；
- **玻璃模块**：面板用半透明 elevated 色 + 1px 冷蓝边框 + hover 微光（panel::before 顶部高光线）；
- **克制动效**：卡片进入 stagger fade+rise（80-120ms）、hover 边框变亮 + 光晕、解题中状态呼吸光点；无大范围动画。

## 三、信息架构（页面结构）

```
┌ Shell ─────────────────────────────────────────────────────┐
│ ◆ 星图 · CTF Agent        [比赛倒计时 02:41:12] [⚑3/8] [¥0.42] [● Kali 在线] │
├────────────────────────────────────────────────────────────┤
│ 页面1 星图总览（列表页）                                      │
│  Hero 统计行：已夺旗 N / 解题中 N / 待提示 N / 累计成本+spark │
│  挑战星卡网格（2-3 列，每卡）：                              │
│    状态星徽(呼吸光) 题名 分类徽章 分数                        │
│    摘要首行 · 耗时 · token · 成本                            │
│    状态条（solving 时动态光带）                              │
│ 页面2 题目详情（左右栏）                                     │
│  左主列：摘要卡 → 工具调用流(EventStream：流式+折叠+自动滚动) │
│  右副列：状态卡 · 人工纠偏(hint)卡 · 方向看板卡 · 复核卡      │
└────────────────────────────────────────────────────────────┘
```

## 四、逐页详细设计

### 4.1 Shell（顶栏，常驻）

- 左：星图 Logo（内联 SVG：旗 + 星）+ 系统名；副标题小字"西湖论剑 AI 夺旗"。
- 右（等宽数字，mono）：比赛倒计时（loop 模式下可注入）、已夺旗 ⚑N/总数、累计成本 ¥、
  Kali 连接指示灯（● 绿/红，ping worker-api/Kali health）。
- 高度 56px，玻璃底 + 底部 1px 冷蓝边框；比赛倒计时进入最后一小时变琥珀、最后 10 分钟变红呼吸。

### 4.2 星图总览（列表页重设计，替代现有表格）

**Hero 统计行**（4 张 stat 卡，参照 dhunter StatCard）：
- 已夺旗：大数字 + 旗图标，绿色光晕；foot=占总数百分比；
- 解题中：stellar 蓝 + 呼吸光点；foot=当前并发数；
- 待提示：琥珀；foot=需要你关注的题数（**这是你盯盘时最该先看的一张卡**）；
- 累计成本：¥ + 24h sparkline（自绘 SVG，token 采样自 tracing 缓存）。

**挑战星卡网格**（每卡 ≈ dhunter panel-card）：
- 顶部行：状态星徽（圆形光点：new 灰 / solving 蓝呼吸 / solved 绿 / needs_hint 琥珀呼吸）+ 题名（Space Grotesk 15px）+ 分类徽章（小圆角、分类色：crypto=星云紫、pwn=极光青、web=恒星蓝、rev=琥珀、misc=灰蓝）；
- 摘要首行（digest 缓存，muted 单行截断）；
- 底部行（mono 等宽）：耗时 · tokens · ¥成本；
- 整卡可点击进详情；solved 卡整体降饱和（星已到手，不再抢视线）；needs_hint 卡边框琥珀光晕（求救信号）。

### 4.3 题目详情（左右栏 2:1，参照 dhunter RunDetail 的 panel-card 布局）

**左主列**：
1. **摘要卡**：digest 三行中文，恒星蓝左侧竖线引注；卡住时整卡琥珀边框；
2. **工具调用流（EventStream 重写）**：参照 dhunter EventStream——每行=时间戳+事件类型图标+文本；
   - ▶ bash/read/write 调用（蓝）、↳ 结果（默认折叠，>120 字可展开 JSON）、✗ 错误（红底行）；
   - **自动滚动开关** + 悬停暂停 + 滚动到顶部加载更多；
   - 空态：星空插图 + "等待 worker 第一次动作"。

**右副列**（自上而下 sticky）：
1. **状态卡**：状态星徽 + 耗时/成本/token（mono）+ 复核开关（toggle 样式按钮，替代现在的文字按钮）；
2. **人工纠偏卡**：hint textarea（玻璃输入框，focus 时边框变亮）+ 主按钮"写入提示"（stellar 渐变）+ 写入成功 toast（右上角滑入，参照 UiToast）；
3. **方向看板卡**：Ideas 列表（状态点色：pending 灰/testing 蓝/verified 绿/failed 红/skipped 灰）+ Memory 列表（kind 徽章：fact 蓝/evidence 青/failure 红/hint 琥珀）；空态小字"Supervisor 尚未产出"；
4. **复核卡**：待确认 flag 行（mono + 琥珀警示边框）+ "确认提交"按钮（绿）。

### 4.4 空态 / 加载 / 错误

- 空态：星空 + 一句文案（参照 UiEmpty）；加载：卡片骨架屏（UiSkeleton 微光扫过）；错误：顶部 banner（红底玻璃，5s 自动消失的 toast 化）。

## 五、色彩语义映射（我们的状态 → stargaze）

| 我们状态 | 色 | 视觉 |
|---|---|---|
| new | --text-faint 灰 | 暗星 |
| solving | --stellar-bright | 蓝星呼吸光 |
| solved | --ok 绿 | 绿星（已夺取，卡片降饱和） |
| needs_hint | --star-amber | 琥珀星呼吸 + 卡片琥珀光晕 |
| 分类 | crypto=nebula 紫 / pwn=aurora 青 / web=stellar 蓝 / rev=amber / misc=灰蓝 | 小徽章 |
| 错误 | --danger | 红底行 + 红边框卡 |

## 六、技术实现方案

1. **框架不变**：React+TS+Vite 保持（功能层 api.ts/App.tsx 逻辑不动，重写视觉层）；
2. **设计系统落地**：`ui/src/theme.css`（上述全部令牌 + 星空背景 + 玻璃面板 + 徽章/按钮/输入/toast/骨架 全套基类，单文件 ≈ 移植 dhunter main.css 的 60%），组件拆 `ui/src/components/`（StarBadge/StatCard/GlassCard/EventStream/Toast/Empty/Sparkline）；
3. **图表**：自绘 SVG Sparkline（≤60 行，参照 dhunter Sparkline.vue 思路），不引重依赖（离线环境友好）；
4. **字体**：默认走本机栈（Segoe UI/SF Pro/JetBrains Mono 本地有则用）；Space Grotesk/Inter 若网络可达再按需加载，失败静默降级——**离线优先**；
5. **数据**：全部复用现有 API（/api/state 已带 tokens/cost，digest/logs/board/hints/confirm 不动）；新增两个轻量后端点：`/api/summary`（全局统计：已解/解题中/待提示/总成本，dashboard 一次算好）+ `/api/kali-status`（ping Kali /health 与 worker-api）；
6. **动效预算**：仅 CSS transition/keyframes（进入 stagger、呼吸光、hover 光晕、toast 滑入），无 JS 动画库。

## 七、文件改动清单与工作量（预估 6-8h，一天内可完）

| 产出 | 内容 | 估时 |
|---|---|---|
| ui/src/theme.css | 令牌/星空背景/玻璃/徽章/按钮/输入/toast/骨架 | 2.5h |
| ui/src/components/* | StarBadge/StatCard/GlassCard/EventStream/Toast/Empty/Sparkline 7 个组件 | 2.5h |
| ui/src/App.tsx 重写 | Shell + 星图列表 + 详情左右栏（功能逻辑保留） | 2h |
| dashboard.py | +/api/summary +/api/kali-status | 0.5h |
| 构建+联调+截图自查 | npm run build + 起看板走查 | 0.5h |

## 八、实施顺序（确认后执行）

P1 令牌+Shell+星图列表（先看整体气质）→ P2 详情页+EventStream → P3 空态/骨架/动效/联调。

## 九、需要你拍板的 3 个决策点

1. **主题**：直接沿用 dhunter 的 stargaze 星空调性（推荐，你已认可它），还是想要别的方向（如"赛博终端绿"、"旗舰黑金"）？
2. **列表形态**：挑战星卡网格（推荐，与 stargaze 最搭）还是保留增强版表格？
3. **品牌名**：「星图 · CTF Agent」或保持「CTF Agent 驾驶舱」或你另起一个？
