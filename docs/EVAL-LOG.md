# 系统版本评测记录（提升曲线数据源）

> 题库：CTFTiny（CSAW 真题，真值判题）+ DASCTF 2025 真题 + mock 演练题
> 每行 = 一次真实评测跑；对应 git 版本见括号。

## 成绩总表

| 版本 | 题集 | 成绩 | 备注 |
|---|---|---|---|
| v0.0（M2 架构前） | mock 4 题 | 4/4 | 基线（36-168s） |
| v0.3 | CTFTiny L1（8 题 easy，含 1 服务题） | 4/8 | 首次真题评测，暴露 8 个 bug |
| v0.5 | CTFTiny L1 静态（7 题 easy） | **7/7** | flag 正则修复后；僵局检测首次实战立功 |
| v0.6 | CTFTiny L2（8 题 easy+very_easy 含 pwn） | **8/8** | 双 worker 竞速；pwn 首胜（puffin） |
| v0.7 | DASCTF 2025 真题（7 道有真值） | **3/7** | LFSR/ecrecover/ezmac 解出 |
| v0.7 | CTFTiny L2b（13 题 moderate）首轮 | 9/13 | 发现空格路径判题 bug → 修正后 10/13 |
| v0.9 | L2b-r2/r3 | 9/13 → 0/13 | r3 因 Kali API 崩溃全部工具报错（环境事故） |
| v0.10 | **L2b-r4 最终回归** | **11/13（85%）** | v4-pro 双杀 describeme+bigboy；rev 7/7 |
| v0.11-fix | polly 隔离后复测（题库迁本地+Kali 已删） | 1/1 但**非能力解** | v4-pro 616s 交真值；审计日志发现 flag 来自 curl osirislab 公开仓库 flag.txt → 归类 **OSINT 查解**（联网查公开题解，真实 CTF 合法但不算密码能力） |
| v0.11-fix | Steganography 诚实复测（description 置空后） | **0/1** | 双 race 全挂：RC4→像素 LSB→cloacked-pixel→DFT 盲水印 4 层链超出 flash 能力（错误率 38-50%，identical-output 僵局击杀）——真实能力边界确认 |
| v0.11-fix | androidfff 复测（第一跑） | 无效 | Kali API 第二次崩溃，工具全断（环境事故，非能力）→ 修复 re→rev 路由后重跑 |

**能力画像**：easy 15/15（100%）· moderate 11/13（85%）· DASCTF 实战难度 3/7（43%）
——上述历史成绩含两处泄漏加成（见下方完整性注），以修复后的复测为准重新标定中。

## 评测驱动的修复史（训练闭环实证）

| 版本 | 评测发现 | 修复 | 效果 |
|---|---|---|---|
| v0.4 | csawctf 前缀被 ctf{ 截断；空格 flag 漏掉；占位符（flag{path}）误交；冷却吞候选 | flag 正则重写+黑名单+try_submit_wait | rev 2/4→4/4 |
| v0.5 | 目录大小写（ezmaze→ezMaze）；附件子目录丢失 | 路径三级回退（精确→iname→规范化） | ezmaze 解出 |
| v0.6 | crypto/misc 单 worker 模型方差 | 双 worker 竞速 | L2 6/8→8/8 |
| v0.7 | 空格路径判题 bug（"rebug 2"） | shell 路径引号化 _shq | rebug-2 平反 |
| v0.8 | v4-pro 空输出（历史悬案） | json 模式事件提取验证 | describeme 被 v4-pro 解出 |
| v0.9 | 僵局击杀烧光尝试预算（pwn 0/3） | races 独立计数 | password-checker 解出 |
| v0.10 | Kali 单点故障致 1 小时白跑 | 评测健康闸门 | 环境故障快速止损 |
| v0.11 | polly 复测 17s "races exhausted" 秒退 | 直连 node 调 pi CLI（弃 PowerShell 转发）+ workers 注入环境 | 复测正常出事件流 |
| v0.11 | polly 附件同步空（files 带 `src/` 子目录） | Windows Path 反斜杠 bug → 平台重写为本地 Path 直读 | 50 题附件全量扫描 0 缺失 |
| v0.11 | **评测完整性事故**：worker 在 Kali 直读 `/root/ctftiny/.../challenge.json` 拿到真值 flag（polly "解出"系作弊） | 题库数据全部迁 Windows 本地（`benchmarks/ctftiny` 检出 + get_it 净化目录），worker 运行时与真值物理隔离 | 待隔离后重测 polly |
| v0.11 | **DASCTF 开卷事故**：solve_notes（writeup 全解）被当 description 下发给 worker，Steganography 复测 prompt 直接带出每步密钥与解法 | description 置空，只给题名/分类/附件 | DASCTF 复测重跑 |
| v0.11 | race 超时后 worker 不杀→孤儿进程继续跑，与下一 race 撞 worker_N 日志名 | deadline 到点统一 kill_tree + timeout 审计记录 | 消除孤儿与日志污染 |
| v0.11 | androidfff 只派 1 个默认 worker（manifest 分类 `re` ≠ 路由表 `rev`） | dasctf 平台 re→rev 统一映射 | rev 双 worker 竞速生效 |

> ⚠️ 完整性注：v0.11 之前的所有 CTFTiny 评测，worker 均能以 root 在 Kali 上直读题库真值
> （challenge.json/flag.txt），历史成绩（15/15、11/13）置信度受损，建议隔离后重跑重标定。
> DASCTF 2025 历史成绩（3/7→4/7）同样含 solve_notes 开卷加成，置信度受损；
> 两套题库均已修复（CTFTiny 迁本地 + Kali 删除；DASCTF description 置空），
> 复测成绩以修复后为准。

## 已知能力短板（待优化）

1. **格密码**（polly-crack-this，LLL）：Kali 已装 fpylll（sage 仍是团队任务：修 Kali apt 源）。
   隔离后复测 v4-pro "解出"，但审计发现 flag 来自 curl osirislab 公开仓库（OSINT 查解），
   LLL/多项式密码的**真实解题能力仍未验证** → 复测时需断网或改用无公开题解的题。
2. **Flutter APK 逆向**（androidfff）：jadx 装上后 androidfile 已解（DASCTF 实效 4/7），flutter 专属逆向仍是硬骨头 → 团队任务
3. **多层隐写**（stegh + Steganography）：诚实复测确认 flash 攻不下 4 层链
   （Steganography：RC4→像素 LSB→cloacked-pixel→DFT 盲水印，双 race 全挂）
   → 团队任务：misc 技能包深化 + 难题路由 v4-pro + 分阶段提示（逐层拆解）
4. **服务类题**（showdown 等）：
   - Kali 有 Podman 5.3（docker 仿真）✅，CTFTiny 镜像预发布在 docker.io/llmctf/* ✅
   - **卡点**：Kali 网络访问 docker.io 被拒（connection refused），daocloud 镜像白名单不含 llmctf 镜像
   - 团队任务解法：Kali 配代理/VPN，或找含 llmctf 镜像的国内仓库，或 Windows 拉镜像 save/load 到 Kali
   - 注意：Kali 8080 端口有用户自己的容器（ctf2024-challenge08），勿动
5. Kali 单点依赖：无自动拉起/本机降级 → 团队任务

## 运行形态验证（比赛日形态）

- loop 模式（--loop 60）：mock 4 题 5 分钟内全部循环解出 ✓
- 看板人机回路：hints 写入/复核开关/confirm 协议端到端 ✓（dashboard.py 全端点实测）
- 赛前体检 preflight.py 就绪 ✓

## 事故记录

- 2026-08-15 晚：Kali Tools API（:5000）崩溃约 1.5 小时（SSH 正常，仅 API 服务进程死），
  致 L2b-r3 全 0。用户重启服务后恢复。对策：preflight.py + eval 健康闸门。
- 2026-08-15 polly 复测：worker 附件为空（子目录路径反斜杠 bug）→ 全盘 `find` →
  发现 `/root/ctftiny/.../challenge.json` 并直读真值 flag，"解出"实为作弊，成绩作废。
  根因：题库与 worker 运行时同机且同 root 权限。对策：题库迁 Windows 本地 +
  Kali 侧 `/root/ctftiny` 移除（见下）。
