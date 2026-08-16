# SECRETS-CHECKLIST —— 开源发布前敏感项清单与清理核对表

> 用途：发布到 GitHub 前，逐项核对「什么绝对不能传、哪些代码/文档还藏着硬编码敏感项、怎么验证没漏」。
> 本清单基于对 `src/`、`docs/` 的一次全量 grep 快照生成；**每次改完代码请重跑第 3 节的 grep 命令**。
> 配套：`docs/INSTALL.md`（依赖安装）。

---

## 1. 绝对不能上传的文件/目录（与 .gitignore 逐项核对）

| # | 路径 | 内容 | 是否已 gitignore | 结论 |
|---|---|---|---|---|
| 1 | `secrets/` | `deepseek.key`（DeepSeek key）、`kali.json`（Kali SSH 凭据） | ✅ 已忽略 | 无需改 |
| 2 | `config/secrets.json` | provider id → API key（Web UI「配置」页写出） | ✅ 已忽略（2026-08-16 补） | 无需改 |
| 3 | `tmp/` | 临时测试脚本/日志 | ✅ 已忽略 | 无需改 |
| 4 | `workspace/`、`eval-workspace/`、`eval-workspace2/`、`eval-workspace-bench/` | 比赛/评测运行时数据（state.json、worker 日志、候选 flag、hints） | ✅ 已忽略 | 无需改 |
| 5 | `benchmarks/` | 题库（含 CTFTiny/NYU/Cybench/DASCTF 真值 flag）与参考实现大文件 | ✅ 已忽略 | 按需剔除或注明来源/许可证 |
| 6 | `src/pi-ext/node_modules/` | ssh2/typebox 等本地依赖 | ✅ 已忽略 | 无需改 |
| 7 | `ui/node_modules/`、`ui/dist/` | 前端依赖与构建产物 | ✅ 已忽略 | 无需改 |
| 8 | `__pycache__/`、`*.pyc` | Python 字节码 | ✅ 已忽略 | 无需改 |
| 9 | `pi-mono/`、`pi-agent/`、`cairn-ref/`、`ctf-agent-ref/`、`koshary-ref/`、`llmctf-ref/`、`refs/` | 第三方仓库/参考实现源码 checkout | ✅ 已忽略 | 按需剔除或注明来源/许可证 |
| 10 | `src/tools/_*.py` | 调试脚本（多个内含 `D:\ctf-agent\secrets\deepseek.key` 绝对路径，如 `_repro_review.py`、`_debug_key.py`、`_probe_*.py` 等 40+ 个） | ✅ 已忽略 | 无需改 |

### .gitignore 现状（已补齐）

2026-08-16 已把下述条目补进 `.gitignore`（并重写为纯 UTF-8），无需再改：

```gitignore
# 密钥（config/secrets.json 由 Web UI 写出，绝不上传）
config/secrets.json

# Python 虚拟环境
.venv/
venv/
env/

# 环境变量/本地覆盖（防御性）
.env
.env.*
*.local

# 密钥文件兜底（deepseek.key 虽在 secrets/ 下已忽略，双保险）
*.key

# 日志（可选）
*.log
```

> 说明：`config/agent.json` 是团队共享默认配置（不含真 key），**应当提交**；只忽略 `config/secrets.json`，不要把整个 `config/` 目录忽略掉。
> 另注：`.gitignore` 当前是混合编码（前半 UTF-8、后半两行中文注释是 GBK 字节，严格 UTF-8 解码会报 invalid），发布前建议重存为纯 UTF-8。

---

## 2. 代码/文档中硬编码的敏感项位置清单（grep 快照）

> 团队机器路径/内网 IP 各不相同，发布前应改为**环境变量或占位符**（如 `%USERPROFILE%`、`<KALI_IP>`、`<REPO>`、`os.environ.get("KALI_API_URL")`）。

### 2.1 内网 Kali IP `10.174.153.128`（✅ src 已处理，2026-08-16；docs 待替换占位符）

`src/` **已全部改为读 `KALI_API_URL` 环境变量**（默认值兜底，不影响现有运行）：
`workers.py` 提供 `kali_api_url()`，dashboard / eval_run / eval_platform / preflight /
revival 均已接入；`run-pi.ps1` 与 `ctf_orchestrator_v1.py.bak` 本来就是 env 驱动。
队友只需 `$env:KALI_API_URL = "http://<自己的Kali IP>:5000"`。

`docs/`（历史文档，改占位符 `<KALI_IP>` 或标注为团队内网地址）：

- `docs/AI-CTF-调研与冲刺方案.md:80`
- `docs/ARCHITECTURE.md:41`
- `docs/KALI-INVENTORY.md:1`
- `docs/PLAN-0816-实施计划.md:128`
- `docs/TEAM-GUIDE.md:30`
- `docs/使用手册-完整版.md:28,147,311,322`
- `docs/定版方案-最终.md:40`

### 2.2 绝对用户路径 `C:\Users\86173`（应改 `%USERPROFILE%` / `~`）

- `docs/PLAN-0816-实施计划.md:127`（`C:\Users\86173\.pi\agent\models.json`）
- `docs/使用手册-完整版.md:47`（同上）
- `src/pi-ext/node_modules/**`（`cpu-features/build/...vcxproj`、`ssh2/lib/protocol/crypto/build/sshcrypto.vcxproj` 等 node-gyp 构建产物）——已被 `src/pi-ext/node_modules/` 忽略，无需处理，但删除该目录前 `git status` 会看不到它们。

### 2.3 绝对密钥路径 `D:\ctf-agent\secrets\deepseek.key` / `D:/ctf-agent/secrets`（应改 `config/secrets.json` 或环境变量）

- `src/run-pi.ps1:8`
- `src/pi-ext/kali.ts:9,72`（`D:/ctf-agent/secrets/kali.json` 默认路径 + 注释）
- `src/ctf_orchestrator/digest.py:19`
- `src/ctf_orchestrator/planning.py:46`
- `src/ctf_orchestrator/workers.py:133`
- `src/tools/_repro_review.py:11`（`_*.py` 已 gitignore，但建议一并改成环境变量）

### 2.4 仓库绝对根路径 `D:\ctf-agent` / `D:/ctf-agent`（改从代码所在位置推导，或读环境变量）

`src/ctf_orchestrator/`（41 处，核心文件应改为 `Path(__file__).resolve().parents[N]` 推导或 `REPO` 环境变量）：

- `audit.py:29`、`bench_admin.py:7,19,20,22,23,40,390`、`ctf_orchestrator.py:44,46,47,585`
- `agent_config.py:20`、`cybench_platform.py:26`、`dasctf_eval_platform.py:15,16`
- `dashboard.py:13,14,27,28,614`、`eval_platform.py:59,61`、`digest.py:19`
- `eval_run.py:10,12,51,62,63`、`kb_server.py:26,27,28,30`、`planning.py:46`
- `preflight.py:17`、`revival.py:23`、`postmortem.py:9,62,64`、`supervisor.py:30`、`workers.py:133`

其他 src：

- `src/pi-ext/kali.ts:51`（extDir 兜底 `D:/ctf-agent/src/pi-ext`）
- `src/tools/_*.py` 若干（已 gitignore，但同样含绝对路径）

`docs/`（36 处，分布于 15 个文件——多数是「代码根 `D:\ctf-agent`」的说明性引用，发布前统一改成 `<REPO>`）：

- `AI-CTF-调研与冲刺方案.md`、`ARCHITECTURE.md`、`ARCHITECTURE-DETAIL.md`、`BENCHMARK-EVAL.md`、`KALI-INVENTORY.md`、`NOTES.md`、`PLAN-0816-实施计划.md`、`TEAM-GUIDE.md`、`使用手册-完整版.md`、`定版方案-最终.md`、`源码对标-12问决策.md`，以及 `docs/analysis/` 下 `analysis-cairn.md`、`analysis-ctf-agent.md`、`analysis-koshary-llmctf.md`、`research-cairn.md`、`research-pi.md`、`research-verialabs.md`、`research-koshary-llmctf.md`

### 2.5 `sk-`（API key 前缀）

**未发现硬编码真 key**，只有两处非敏感命中：

- `src/ctf_orchestrator/preflight.py:29`（校验 key 是否 `sk-` 开头，属逻辑非真值）
- `docs/pi-agent-dissection.md:176`（描述 pi 的 `redact_secrets` 脱敏正则，非真值）

真 key 在 `secrets/deepseek.key`（已 gitignore，本机 35 字符、`sk-` 开头），不在源码/文档里。

### 2.6 `password`（密码）

**未发现硬编码真密码**，命中均为字段名/占位符/第三方噪声：

- `src/dasctf_client/dasctf_client.py:145,146,229,249-253`（登录参数，值从 `DASCTF_PASSWORD` 环境变量读）
- `src/pi-ext/kali.ts:13,68,83,96,139,191`（SSH `password` 字段，从 `secrets/kali.json`/`KALI_PASSWORD` 读）
- `src/ctf_orchestrator/ctf_orchestrator_v1.py.bak:319-322`（`DASCTF_PASSWORD` 环境变量）
- `src/pi-ext/node_modules/**`（ssh2 等第三方测试 fixture 的假密码 `'hi mom'`/`'1234'` 等，已 gitignore）
- `src/tools/pull-ctftiny.sh`、`pull-service-images.sh`、`service-manifest.json`、`service-images.txt` 中的 `password_checker` 是**题目名**，非密码。

> 结论：源码/文档没有明文 key 或密码；真正要替换的是 **内网 IP + 三处绝对路径（`C:\Users\86173`、`D:\ctf-agent`、`D:\ctf-agent\secrets`）**，都改环境变量/占位符即可。

---

## 3. 发布前核对流程

### 3.1 git 状态检查

```powershell
git status --short          # 确认没有 secrets/、workspace*、node_modules、__pycache__ 等被追踪
git ls-files | Select-String -Pattern 'secret|\.key$|kali\.json|workspace|node_modules|\.pyc'   # 已追踪文件里绝不允许出现这些
```

### 3.2 grep 命令集合（改完代码后必须重跑，全部应「零命中真值」）

```powershell
# 内网 Kali IP
git grep -n "10\.174\.153\.128"
# 用户绝对路径（团队机器不同，改为 %USERPROFILE%）
git grep -n -e "C:\\Users\\86173" -e "C:/Users/86173"
# 仓库绝对根路径（改为 <REPO> 或运行时推导）
git grep -n -e "D:\\ctf-agent" -e "D:/ctf-agent"
# 密钥绝对路径
git grep -n -e "D:\\ctf-agent\\secrets" -e "D:/ctf-agent/secrets"
# API key 真值（真 key 形状：sk- 后跟长串；逻辑判断/脱敏正则除外）
git grep -n -E "sk-[A-Za-z0-9]{20,}"
# 常见其它 key/凭据形状
git grep -n -E "(ghp_|gho_|gsk_|xox[bap]-|AKIA[0-9A-Z]{16})"
# 可能的真实密码（排除字段名/题目名，人工复核每一处）
git grep -n -i "password" -- ':!src/pi-ext/node_modules' ':!ui/node_modules'
```

> `git grep` 只扫**已追踪**文件，天然跳过 gitignore 目录，最适合发布前审计；若想扫全盘（含未追踪），用 `grep -r` 并 `--exclude-dir=node_modules`。

### 3.3 git check-ignore 验证

```powershell
# 逐个验证敏感路径确实被忽略（输出该路径 = 已忽略；无输出 = 会被上传！）
git check-ignore -v secrets/deepseek.key secrets/kali.json config/secrets.json tmp workspace eval-workspace eval-workspace-bench benchmarks src/pi-ext/node_modules ui/node_modules ui/dist
```

期望：全部有输出（都已被忽略）；任何一项无输出都要停下排查。

### 3.4 暂存后复查

```powershell
git add -A
git diff --cached --name-only          # 过一遍将要提交的文件清单，肉眼确认无敏感文件
git diff --cached | Select-String -Pattern "sk-[A-Za-z0-9]{20,}|10\.174\.153\.128|C:\\Users\\86173"
git reset                               # 确认无误后再正式提交
```

---

## 4. 队友拿到代码后需要自建的文件（占位符示例，不含真值）

队友 clone 下来后，以下文件**不存在**，需各自创建（这些是唯一该放真值的地方）：

### 4.1 `secrets/deepseek.key`（DeepSeek key，单行）

```
sk-你的DeepSeek密钥
```

### 4.2 `secrets/kali.json`（Kali SSH 凭据）

```json
{
  "host": "<你的Kali_IP>",
  "port": 22,
  "username": "kali",
  "password": "<你的Kali密码>",
  "sudo": true
}
```

### 4.3 `%USERPROFILE%\.pi\agent\models.json`（pi 模型注册表，key 用环境变量占位符）

```jsonc
{
  "providers": {
    "deepseek-direct": {
      "baseUrl": "https://api.deepseek.com",
      "api": "openai-completions",
      "apiKey": "$DEEPSEEK_API_KEY",
      "models": [
        { "id": "deepseek-chat",      "reasoning": false, "contextWindow": 128000, "maxTokens": 8192 },
        { "id": "deepseek-reasoner",  "reasoning": true,  "contextWindow": 128000, "maxTokens": 16384 }
      ]
    }
  }
}
```

### 4.4 `config/secrets.json`（provider id → key，gitignore）

```json
{
  "deepseek": "sk-你的DeepSeek密钥"
}
```

> 其余可提交的配置（`config/agent.json`、`src/ctf_orchestrator/l2-config.json`）不含真值，可直接用仓库默认或经 Web UI「配置」页生成。
