# ROADMAP.md —— 第九届西湖论剑「AI Agent 解题夺旗」作战路线图

> 定稿 2026-08-17，用户已确认。凭据一律只放 `config/secrets.json`（gitignore，不提交），
> 本文档不出现任何账号密码原文。

## 一、目标与边界

- 赛事：西湖论剑 AI 赛道 @ `gcsis.dasctf.com`；测试赛 8/18 09:00–8/19 17:00；初赛 8/21 14:00–17:00（3h）；决赛 = 答辩。
- 每队仅一个 Agent 接入平台；flag 提交仅需 `{}` 内内容；每题 50 次提交上限，禁爆破。
- 赛后：在线交解题报告 + 网络流量 + 平台日志三方核对（平台已部署流量监控审计）。
- 联网搜索：**明确允许**（流量审计只约束大模型端点）。
- LLM 调用：**必须走平台大模型网关白名单端点**，网关验证通过前不试打（硬门禁）。

## 二、架构原则（AK，全中文输出）

- 每题 1 强（deepseek-v4-pro / thinking medium）+ 1 弱（deepseek-v4-flash / low）双 worker。
- `max_parallel_challenges = 3`；只解当前题，绝不切题；直接提交；无 triage / 僵局击杀 / fallback。
- worker 思考/回复 + observer 看板全中文。

## 三、环境边界

- **benchmark 回归（bench_mode=True，仅 eval_run.py）**：worker 容器化（rootless podman + userns + 断网 iptables 锁），worker 禁止抓取 flag/writeup；真值只存 Windows。
- **比赛（bench_mode=False）**：无容器、无 iptables、无 NET_POLICY；worker 跑 Kali host（root），全网络允许搜索。
- 白名单端点：DeepSeek `api.deepseek.com`（`/chat/completions`、`/v1/chat/completions`、`/responses`、`/anthropic/v1/messages`）及各厂商表列端点。
- **非白名单中转站已移除**（不在白名单，流量审计会取消成绩）——阶段 0 完成引用零命中闭环。

## 四、阶段 0（8/17 晚）——环境定稿

- [x] 四阶段计划落地为本文档
- [x] 平台账号写入 `config/secrets.json` 的 `dasctf` 段（base_url / username / password）
- [x] `dasctf_client.py` 与 `preflight.py` 改为从配置文件读取账号（env 兜底）
- [x] 移除中转站 provider：`config/agent.json` + `~/.pi/agent/models.json` + `config/secrets.json` + `src/tools` 残留脚本，引用零命中
- [x] `config/agent.json` 预置「大模型网关」provider 占位位（8/18 拿到平台文档后替换真实白名单 URL）
- [ ] benchmark 回归冒烟：iptables 9 行/桥段 2 条 ✅、24/24 靶机存活 ✅；沙箱内 10.0.2.2 探测脚本 bug（bash -c 单引号吞变量）已修，待用户需要时重跑
- [x] `preflight.py` 全绿 13/13（2026-08-17）
- [x] preflight 孤儿 worker 判定收紧为 coding-agent（npx/@playwright/mcp 误报修复）

## 四·补（8/17 用户追加：比赛文件准备，已提交 05bff57）

- [x] `config/match.json` 比赛模式独立配置（host 沙箱关闭/并行 3/--model-config 用）
- [x] 比赛入口 `ctf_orchestrator.py` base_url 改读配置文件（env 兜底，默认白名单域）
- [x] `probe_platform.py` --base-url 默认读配置文件
- [x] `docs/PLATFORM-API.md` 8/18 平台对接工作表（认证/端点/网关/flag 剥壳/动态靶机）
- [x] `docs/RACEDAY.md` 全面更新（启动命令、网关硬门禁、提交纪律、时间策略、速查）
- [x] `docs/SECRETS-CHECKLIST.md` 补 dasctf 账号段说明

## 五、阶段 1（8/18 09:00）——平台对接 ✅ 2026-08-19 完成

- [x] 登录 `gcsis.dasctf.com`，拿到 Agent AccessKey 与网关 URL（控制台「环境配置」页）
- [x] 文档中心《AI Agent API 文档》《大模型网关接入》已落地（`dasctf_client.py` v0.2 重写 + `PLATFORM-API.md` 回填）
- [x] `config/secrets.json` dasctf 段：access_key + gateway_url 已配置
- [x] 白名单原始 URL 仅 4 个 DeepSeek 端点（/chat/completions 等）——网关 URL 须 POST 根路径

## 六、阶段 2——平台闭环验证 ✅ 2026-08-19 完成

- [x] API 闭环：exercise-list→exercise→build-env→answer 全通（X-Agent-AccessKey 认证）
- [x] 大模型网关闭环：POST 网关根 200（deepseek-chat/v4-pro/v4-flash），经本地代理 8787
- [x] flag 剥壳实测：提交 `DASCTF{...}` 完整格式判错，`{}` 内内容才正确（内置 `_strip_flag`）
- [x] 靶机协议：每队最多 3 台（40409），`isProxy` 代理连接（proxyIps:port），solved 自动回收
- [x] 附件实测：单对象 `{url,name,extension}` 结构（非文档的 files 数组），已兼容

## 七、阶段 3（8/18–19）——小规模试打 ✅ 进行中

- [x] 编排器连真实平台端到端：7 题拉取、附件下载、worker 经网关解题（10661 已 solved）
- [x] **UI「比赛 Agent」页**：启动/停止/状态/日志（`/api/match/*` + match_admin）
- [x] LLM 网关合规：`DASCTF_LLM_BASE_URL` env 覆盖（比赛走 8787 代理，benchmark 直连）
- [ ] 初赛配置定稿（观察试打表现后微调模型/并行/超时）

## 八、阶段 4（8/21）——初赛

- 14:00–17:00 初赛；**14:30 后不开新难题**（时间策略）。
- 赛后生成解题报告（决赛答辩材料）。
