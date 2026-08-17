# 🏁 比赛日操作手册（8/18 测试赛 + 8/21 初赛）

> 人员：1 人（用户）+ 开发代理（DSH）。
> 配套：`docs/ROADMAP.md`（四阶段计划）、`docs/PLATFORM-API.md`（8/18 平台对接工作表）。
> 账号与密码一律只放 `config/secrets.json` 的 `dasctf` 段（gitignore，不入库）。

---

## 一、赛前（8/17 已完成 ✅ + 8/18 早晨复核）

- [x] 平台账号写入 `config/secrets.json` dasctf 段（base_url / username / password；env 变量只是兜底）
- [x] `dasctf_client` / `preflight` / 比赛入口 `ctf_orchestrator.py` / `probe_platform.py` 全部读配置文件
- [x] 非白名单中转站已移除，LLM 端点全部在白名单内（api.deepseek.com 等）
- [x] `config/match.json` 比赛模式独立配置（`--model-config` 使用；host 模式、不禁网）
- [x] `config/agent.json` 预置 dasctf-gateway 占位（8/18 拿到平台文档后填真实白名单 URL）
- [ ] 8/18 早晨：`python src\ctf_orchestrator\preflight.py` → **13/13 全绿**
- [ ] 8/18 早晨：确认平台文档中心可访问（《API 接入说明》《大模型网关接入》）
- [ ] 8/18 早晨：**大模型网关验证通过前，禁止任何试打（硬门禁）**

## 二、测试赛时间线（8/18 09:00 开赛，至 8/19 17:00）

| 时间 | 人 | 代理（我） |
|---|---|---|
| 09:00 | 浏览器登录 `gcsis.dasctf.com`，确认 AI 赛道入口 | 跑探测：`python src\dasctf_client\probe_platform.py --out workspace/probe` |
| 09:00-09:20 | 文档中心导出《API 接入说明》《大模型网关接入》 | 读 probe 报告 + 文档 → 填 `dasctf_client` 的 EP 端点与认证形态 |
| 09:20-09:40 | 【运行环境】页配 BASEURL 白名单 URL + 同步平台 accesskey | 网关接入（agent.json dasctf-gateway 填真实地址）→ 网关闭环验证（LLM 调用成功）|
| 09:40-10:00 | 确认 flag 提交格式（仅 `{}` 内内容）与提交上限 | 平台闭环：login / challenges / detail / submit 打通，flag 剥壳实测 |
| 10:00 | 看板观战（http://127.0.0.1:8088） | 起编排器试打（命令见下） |
| 10:00-17:00 | 盯盘：写 hints、定优先级，不手工解题 | 全自动解题循环；记录平台行为（冷却/限频/flag 格式/题量题型）|
| 8/19 | 问题清单 → 优先级 | 复盘、修问题，初赛配置定稿 |

### 测试赛验收标准（全过才算平台闭环）

- [ ] 平台登录成功（cookie/token 持久化）
- [ ] 拉题 / 附件 / 交卷 / 查分四端点打通
- [ ] 大模型网关验证通过（LLM 走白名单端点）
- [ ] flag 剥壳规则实测记录（`DASCTF{...}` → 提交 `{}` 内内容）
- [ ] 至少 1 道题由编排器全自动解出并成功交卷
- [ ] 平台行为清单记录在案（限频 / 冷却 / 每题 50 次提交上限实测）

## 三、初赛（8/21 14:00-17:00，3 小时）

- **启动命令**（比赛模式，bench_mode=False、允许联网搜索、host 模式不禁网）：

  ```powershell
  python src\ctf_orchestrator\ctf_orchestrator.py --loop 10800 `
      --model-config config/match.json --workspace D:/ctf-agent/workspace
  ```

  - `--loop` 单位秒（10800 = 3 小时）；中途可 Ctrl+C 重启，状态在 `workspace/state.json` 持久化
  - 模型路由：`config/match.json`（strong/weak/observer + 并行 3 + host 沙箱关闭）；
    planner/supervisor 由统一配置中心 `config/agent.json` 提供
- 先易后难自动调度；人只做：写 hints、复核 flag
- **14:30 后不再开新难题**（时间策略）
- 提交纪律：每题 50 次上限，只交有把握的完整 flag，禁爆破
- 赛后：`postmortem.py` 复盘 → 生成解题报告（决赛答辩材料）

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
| 大模型网关不通/被限 | 回退白名单内官方端点（api.deepseek.com 等）；网关验证不过不试打 |
| 提交数接近 50 次上限 | 停手，人工复核 flag 再交（客户端有错误预算，上限可调） |

## 六、成绩与复盘

- 赛后：`postmortem.py` 生成短板报告 → 修 → 再战
- 全部记录自动进 `workspace/state.json` + git（决赛答辩材料）
- 赛后按平台要求在线提交解题报告（网络流量 + 平台日志三方核对）

## 附录：平台信息速查

- 平台地址：`gcsis.dasctf.com`（8/17 官方手册确认；旧 `game.gcsis.cn` 仅为历史侦察记录）
- 账号：`config/secrets.json` dasctf 段（中文姓名 + 手机号后四位；密码 `Das#身份证后四位`）
- flag 提交仅需 `{}` 内内容；每题 50 次提交上限；每队仅一个 Agent 接入平台
- LLM 白名单端点：DeepSeek `api.deepseek.com`（`/chat/completions`、`/v1/chat/completions`、`/responses`、`/anthropic/v1/messages`）及各厂商表列端点；平台大模型网关接入方式见《大模型网关接入》文档
- 早期侦察记录（2026-08-15）：`game.gcsis.cn` → 腾讯云 CDN（cdn.dnsv1.com.cn，DNSPod 调度）；无 api./test./ai./admin. 子域 → API 大概率同域 `/api/*`（注意 CDN 的 WAF/限频行为）
