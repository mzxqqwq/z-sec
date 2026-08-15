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

**能力画像**：easy 15/15（100%）· moderate 11/13（85%）· DASCTF 实战难度 3/7（43%）

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

## 已知能力短板（待优化）

1. **格密码**（polly-crack-this，LLL）：Kali 无 SageMath（源缺包）→ 团队任务
2. **APK 逆向**（androidfile/androidfff）：jadx/apktool 已装，待技能包+复测
3. **多层隐写**（stegh）：僵局检测已放宽多阶段保护，待复测
4. **服务类题**（showdown 等）：需 Docker 或手动环境搭建 → 团队任务
5. Kali 单点依赖：无自动拉起/本机降级 → 团队任务

## 事故记录

- 2026-08-15 晚：Kali Tools API（:5000）崩溃约 1.5 小时（SSH 正常，仅 API 服务进程死），
  致 L2b-r3 全 0。用户重启服务后恢复。对策：preflight.py + eval 健康闸门。
