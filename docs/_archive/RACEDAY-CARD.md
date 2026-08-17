# 🏁 开赛当天操作卡（8/18 测试赛）

> 一张纸版本：每一步谁做什么。详细版见 docs/M3-TESTMATCH-PLAYBOOK.md。

## 赛前（8/17 晚）
- [ ] 人：确认比赛账号能登录 game.gcsis.cn（网页）
- [ ] 人：设置环境变量（系统属性 → 环境变量，或告诉我，我来写）：
  - `DASCTF_BASE_URL`（真实比赛地址，官方通知）
  - `DASCTF_USERNAME` / `DASCTF_PASSWORD`
- [ ] 代理（我）：`python src\ctf_orchestrator\preflight.py` → 要求 10/10

## 开赛（8/18 09:00）
| 时间 | 人 | 代理（我） |
|---|---|---|
| 09:00 | 浏览器登录平台，确认 AI 赛道入口 | 跑 `probe_platform.py --base-url <地址>` 抓 API |
| 09:05-09:20 | 等结果/提供登录方式（cookie 或账号密码） | 读 probe 报告 → 填 `dasctf_client` 端点 → 验证 login/challenges |
| 09:20-09:40 | 看板观战（http://127.0.0.1:8088） | 起编排器 `--loop 60`，先打简单题试水 |
| 09:40 起 | 盯盘：写 hints（看板网页）、定优先级 | 全自动解题循环；僵局/提交纪律自动处理 |
| 全天 | 记录平台行为（冷却/限频/flag 格式） | 修问题、调配置 |

## 紧急情况
| 症状 | 处理 |
|---|---|
| Kali 挂了 | 人重启 Kali API 服务；我跑 preflight 确认 |
| 平台 captcha/加密 | 客户端有钩子；按 probe 的 config 结果适配 |
| 大量 429 限频 | 我已内置退避；调 `--loop` 间隔 |
| 题目 API 与猜想不符 | probe 报告是第一证据；端点集中改 1 小时可完成 |

## 初赛（8/21 14:00-17:00，3 小时）
- 配置用 l2-config.json（题型路由 + 双 worker 竞速）
- 先易后难自动排序；人只做：写 hints、复核 flag（如开 verify）
- 14:30 后不再开新难题（时间策略）

## 成绩与复盘
- 赛后：`postmortem.py` 生成短板报告 → 8/20 修 → 8/21 再战
- 全部记录自动进 `workspace/state.json` + git（决赛答辩材料）
