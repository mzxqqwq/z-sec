# PLATFORM-API.md —— 平台对接工作表（8/18 测试赛当天填写）

> 用途：8/18 09:00 开赛后，把平台 API 对接需要的全部信息确认并记录在这里。
> 信息源（按优先级）：① 平台文档中心《API 接入说明》《大模型网关接入》；
> ② 【运行环境】页配置项；③ `probe_platform.py` 探测报告；④ 浏览器抓包。
> 硬门禁：**大模型网关验证通过前，不试打任何题目**。

---

## 0. 信息源记录

| 项 | 值 |
|---|---|
| 平台地址 | `https://gcsis.dasctf.com`（见 config/secrets.json dasctf 段） |
| 文档中心入口 | （登录后找） |
| 《API 接入说明》导出时间 | |
| 《大模型网关接入》导出时间 | |
| probe 报告路径 | `workspace/probe/probe-*.json` |
| 我方 accesskey | （【运行环境】页同步后记录位置，不写值） |

## 1. 认证形态（最重要，决定 dasctf_client 会话层实现）

| 问题 | 答案 | 证据 |
|---|---|---|
| 登录接口路径与参数（username/password？是否加密？） | | |
| 登录后用什么维持会话（cookie / JWT / accesskey） | | |
| 每队是否只有一个 Agent 凭据（accesskey 一人一队？） | | |
| 是否需要验证码/风控（captcha 类型） | | |
| 会话有效期与刷新机制 | | |

→ 结论落到 `src/dasctf_client/dasctf_client.py`（会话层已有 cookie jar；若 token 则加 header）。

## 2. API 端点（填真实路径，替换 dasctf_client.EP 占位）

| 功能 | 占位（当前） | 真实路径（8/18 填） | 请求/响应要点 |
|---|---|---|---|
| 平台配置探测 | `/api/config` | | 是否要 captcha / 加密 |
| 登录 | `/api/login` | | |
| 当前用户 | `/api/user` | | |
| 题目列表 | `/api/challenges` | | 返回结构（challenges/data/list?） |
| 题目详情 | `/api/challenges/{id}` | | 含附件/连接信息？ |
| 交 flag | `/api/challenges/{id}/submit` | | payload 形态（见 §4） |
| 查分/排行榜 | `/api/scoreboard` | | |
| 附件下载 | `/api/challenges/{id}/attachment` | | |
| 动态靶机开启/关闭 | （未知） | | 见 §5 |

## 3. 大模型网关接入（硬门禁）

| 问题 | 答案 | 证据 |
|---|---|---|
| 网关地址（BASEURL 白名单 URL） | | |
| 接入方式（OpenAI 兼容 / 独立 SDK / 代理） | | |
| key/accesskey 获取与放置位置 | | |
| 可用模型清单（能否跑 deepseek-v4-pro / deepseek-v4-flash） | | |
| 与官方端点（api.deepseek.com）关系：强制 or 可选 | | |
| 限流/计费行为 | | |

→ 落到 `config/agent.json` 的 `dasctf-gateway` provider（替换占位 URL）+ secrets.json 对应 key；
  同步 `~/.pi/agent/models.json`（`agent_config.sync_pi_models()`）。
→ **验证动作**：用网关成功完成一次 LLM 调用（chat/completions 最小请求）并记录。

## 4. flag 提交协议（剥壳 + 限频）

| 问题 | 答案 | 证据 |
|---|---|---|
| flag 形态（`DASCTF{...}` / `flag{...}`） | | |
| 提交 payload：整个 flag 还是仅 `{}` 内内容 | | |
| 响应结构（correct 字段？message？） | | |
| 每题提交上限（官方 50 次）实测确认 | | |
| 错误提交是否计冷却/封禁 | | |
| 重复提交相同 flag 的行为 | | |

→ 结论落到 `dasctf_client.submit()`（剥壳函数 + min_submit_interval + 错误预算）。

## 5. 动态靶机协议（若有）

| 问题 | 答案 | 证据 |
|---|---|---|
| 是否有动态靶机题（nc/HTTP 端口） | | |
| 开启靶机的 API/入口 | | |
| 关闭靶机的 API/入口 | | |
| 靶机地址格式（IP:port / 域名）与有效期 | | |
| 并发限制（能同时开几个靶机） | | |

→ worker 提示词连接信息注入已就绪（`_sync` 里 connection 字段）；有开关 API 则补进 dasctf_client。

## 6. 平台行为清单（试打期间持续记录）

| 行为 | 观察 | 处置 |
|---|---|---|
| 提交冷却/限频阈值 | | 调 min_submit_interval / 错误预算 |
| 错误提交惩罚 | | |
| 题目更新/新题出现节奏 | | |
| 排行榜更新延迟 | | |
| 封禁信号（疑似） | | 立即停手人工确认 |

## 7. 闭环验收（全部 ✅ 才算平台对接完成）

- [ ] §1 认证形态确认并实现
- [ ] §2 端点全填并验证 login/challenges/detail/submit/attachment
- [ ] §3 网关验证通过（一次真实 LLM 调用成功）
- [ ] §4 flag 剥壳实测（提交 `{}` 内内容被接受）
- [ ] §5 动态靶机开关协议确认（有则实现）
- [ ] preflight 全绿 + 试打至少 1 题全自动解出并交卷
