# 🏁 比赛日操作手册（8/18 测试赛 + 8/21 初赛）

> 合并自 RACEDAY-CARD + M3-TESTMATCH-PLAYBOOK；附比赛模式提示词纪律条款说明。
> 人员：1 人（用户）+ 开发代理（DSH）。

---

## 一、赛前（8/17 晚）

- [ ] 人：确认比赛账号能登录 game.gcsis.cn（网页）
- [ ] 人：设置环境变量：`DASCTF_BASE_URL`（官方通知的真实地址）、`DASCTF_USERNAME` / `DASCTF_PASSWORD`
- [ ] 代理（我）：`python src\ctf_orchestrator\preflight.py` → 要求 10/10 全绿
- [ ] 代理：确认 benchmark 容器/隔离改造不影响比赛路径（比赛走 host 模式、不禁网）

## 二、开赛时间线（8/18 09:00）

| 时间 | 人 | 代理（我） |
|---|---|---|
| 09:00 | 浏览器登录平台，确认 AI 赛道入口 | 跑 `probe_platform.py --base-url <地址>` 抓 API |
| 09:05-09:20 | 等结果/提供登录方式（cookie 或账号密码） | 读 probe 报告 → 填 `dasctf_client` 端点 → 验证 login/challenges |
| 09:20-09:40 | 看板观战（http://127.0.0.1:8088） | 起编排器 `--loop 60`，先打简单题试水 |
| 09:40-90 分钟 | 盯盘：写 hints、定优先级，不手工解题 | 全自动解题循环 |
| 第 40-90 分钟 | 记录平台行为（冷却/限频/flag 格式/题量题型） | 修问题、调配置 |
| 8/18 晚 | 问题清单 → 优先级 | 复盘、8/19 白天修 |

### 测试赛验收标准（M3 done 判据）
- [ ] 真实平台登录成功（cookie 持久化）
- [ ] 拉题/附件/交卷/查分四端点全部打通
- [ ] 至少 1 道题由编排器全自动解出并成功交卷
- [ ] 平台行为清单（冷却/限频/flag 格式/计分）记录在案
- [ ] 问题清单进入 M4

## 三、初赛（8/21 14:00-17:00，3 小时）

- 配置用 `config/agent.json`（统一配置中心）；先易后难自动调度，人只做：写 hints、复核 flag
- 14:30 后不再开新难题（时间策略）

## 四、比赛模式提示词纪律（已内置，2026-08-17）

比赛路径（`ctf_orchestrator.py`，bench_mode=False）自动向每个 worker 注入【比赛纪律】条款：

1. **目标边界**：只与题目信息给出的目标交互；严禁扫描/探测/攻击比赛平台本身（登录/后台/非题目页面），严禁对平台爆破——违反会连累整队封号；
2. **提交纪律**：只提交有把握的完整 flag（优先 submit_flag 工具）；平台有提交冷却与封禁机制，禁止爆破式连续乱猜；
3. **搜索定位**：全新题目网上没有现成 wp；联网搜索只用于查标准技术资料（工具文档、已知 CVE、公开库用法、协议规范），不搜题解。

同时比赛模式**不启用**任何网络封锁（NET_POLICY 不注入、工具层不拦截）——OSINT 联网为允许策略。
客户端侧兜底：429 读 Retry-After 指数退避 + `min_submit_interval` 最小提交间隔（`dasctf_client.py`）。

## 五、紧急情况

| 症状 | 处理 |
|---|---|
| Kali 挂了 | 跑 preflight 确认；SSH 通道有自动重连；必要时重启 Kali 服务 |
| 平台 captcha/加密 | 客户端有钩子（HashPow/Turnstile/图形码）；按 probe 的 config 结果适配 |
| 大量 429 限频 | 客户端内置退避；调 `--loop` 间隔 |
| 题目 API 与猜想不符 | probe 报告是第一证据；端点集中改（dasctf_client 的 EP 类），1 小时可完成 |
| 题目带动态靶机（nc/HTTP 端口） | 题目描述里的连接信息已注入 worker 提示词；worker 经 Kali 直连靶机 |

## 六、成绩与复盘

- 赛后：`postmortem.py` 生成短板报告 → 修 → 再战
- 全部记录自动进 `workspace/state.json` + git（决赛答辩材料）

## 附录：赛前基础设施侦察（2026-08-15）

- game.gcsis.cn → 腾讯云 CDN（cdn.dnsv1.com.cn，DNSPod 调度）；www.gcsis.cn → 阿里云 CDN
- 无 api./test./ai./admin. 子域 → API 大概率同域 /api/*（注意 CDN 的 WAF/限频行为）
- 前端 SPA 仍是报名页 bundle，比赛界面预计测试赛前才部署
- 探测脚本：`python probe_platform.py --base-url <真实地址> --out workspace/probe`
