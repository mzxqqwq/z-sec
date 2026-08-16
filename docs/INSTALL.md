# CTF Agent 依赖安装文档（INSTALL）

> 用途：让队友在**全新机器**上从零把系统跑起来。双机架构：**Windows 编排机**（跑编排器/看板/pi worker）+ **Kali 执行机**（跑解题工具链、SSH 直连执行）。
> 本文所有命令约定在仓库根目录执行；`<REPO>` 表示仓库根（本文档所在机器为 `D:\ctf-agent`，队友机器路径不同请自行替换，**不要硬编码**）。
> 配套：`docs/SECRETS-CHECKLIST.md`（发布前清理与密钥清单）、`docs/使用手册-完整版.md`（操作指南）。

---

## A. Windows 主机（编排机）

### A.1 Python 3.10+ 与第三方依赖

代码使用了 PEP 604 联合类型语法（`Path | None`）与 `from __future__ import annotations`，**必须 Python 3.10 及以上**。

仓库**没有** `requirements.txt`。逐个 grep `src/ctf_orchestrator/*.py` 的 import 后，第三方依赖只有 3 个（其余全部是标准库）：

| 包 | 用途 | 出现位置（import 证据） |
|---|---|---|
| `flask` | 看板 dashboard.py / 演练平台 mock_platform.py | `dashboard.py:24` `from flask import ...` |
| `requests` | 调 Kali REST、裸调 LLM、平台 HTTP | `planning.py:15`、`workers.py:16`、`preflight.py:15`、`digest.py:16`、`platform.py:106`、`eval_run.py:103`、`revival.py:62`、`dashboard.py:257` |
| `psutil` | 清理孤儿 worker 进程（仅 Windows 编排机） | `workers.py:186` `import psutil`（延迟导入） |

可直接复制的安装命令：

```powershell
pip install flask requests psutil
```

建议在仓库根放一个 `requirements.txt`（内容即上面三行）：

```
flask
requests
psutil
```

然后 `pip install -r requirements.txt` 即可。若日后引入新依赖，请同步更新此文件。

### A.2 Node.js + pnpm

- **Node.js** ≥ 22.19.0（pi-mono 运行时的 `engines` 硬性要求；本机实测 v24.15.0，自带 npm 11.12.1）。下载：https://nodejs.org/
- **pnpm**：前端 `ui/` 用 pnpm 管理（`ui/pnpm-lock.yaml`，lockfileVersion `'9.0'`）。本机实测 pnpm 11.1.2。安装：

```powershell
npm install -g pnpm
# 或使用 corepack： corepack enable && corepack prepare pnpm@latest --activate
```

> 注意：`pi-mono/` 是 **npm workspaces**（自带 `package-lock.json`，无 pnpm-lock），**不要用 pnpm 去装 pi-mono**，用 npm（见 A.3）。

### A.3 pi-mono 运行时构建 + src/pi-ext 扩展依赖

`pi-mono/` **不在仓库里**（已 gitignore），它是第三方 **pi 运行时**（MIT），源码仓库为 `github.com/earendil-works/pi`。编排器通过 `node pi-mono/packages/coding-agent/dist/cli.js` 启动 worker，所以必须先取得并构建它：

```powershell
# 1) 在仓库根克隆 pi 运行时到 pi-mono/ 目录
git clone https://github.com/earendil-works/pi.git pi-mono

# 2) 安装依赖并构建（root 的 build 脚本会链式 build 各 package）
cd pi-mono
npm install
npm run build
cd ..
```

- 构建产物：`pi-mono/packages/coding-agent/dist/cli.js`（`preflight.py` 第 2 项检查的就是这个文件）。
- 本机 `pi-mono` 的 `.npmrc` 有 `save-exact=true`，用 npm 保持一致。

`src/pi-ext/` 是 pi 的扩展（kali.ts SSH 执行、loop-detect.ts 循环检测），它需要 `ssh2` 与 `typebox` 两个依赖（`src/pi-ext/package.json` 里只有这两个）：

```powershell
cd src\pi-ext
npm install        # 安装 ssh2 / typebox 到 src/pi-ext/node_modules
cd ..\..
```

### A.4 ui 构建

前端是 React + Vite + TS，`dashboard.py` 会自动 serve `ui/dist` 静态文件（`/ui/` 路径），所以必须先构建：

```powershell
cd ui
pnpm install
pnpm build        # 等价于 tsc -b && vite build，产出 ui/dist
cd ..
```

> 改前端后需重新 `pnpm build`；没有 `ui/dist` 时看板 `/ui/` 会 404/空白（见使用手册 FAQ）。

### A.5 各服务启动命令与端口表

| 端口 | 服务 | 启动命令 | 谁起 |
|---|---|---|---|
| 7788 | mock 假考场 | `python src/mock_platform/mock_platform.py --port 7788` | 手动（7777 被 Windows 保留，勿用） |
| 8088 | 看板/UI + API | `python src/ctf_orchestrator/dashboard.py --workspace D:/ctf-agent/workspace --port 8088` | 手动 |
| 8089 | worker-api（提交/取提示回调） | 编排器启动时自动拉起，无需手动 | 自动 |
| 8099 | 本地 KB 检索（可选） | `python src/ctf_orchestrator/kb_server.py` | 手动/`kb_enabled` 时自动 |
| 22 | Kali SSH | Kali 自带 | — |

```powershell
# 演练（假考场）三终端：
# 终端 A：mock 假考场
python src/mock_platform/mock_platform.py --port 7788
# 终端 B：编排器（--loop 60 每 60 秒一轮；--once 用于单轮调试）
$env:DASCTF_BASE_URL = "http://127.0.0.1:7788"
python src/ctf_orchestrator/ctf_orchestrator.py --loop 60 --platform mock --workspace D:/ctf-agent/workspace --model-config src/ctf_orchestrator/l2-config.json
# 终端 C：看板（浏览器开 http://127.0.0.1:8088/ui/）
python src/ctf_orchestrator/dashboard.py --workspace D:/ctf-agent/workspace --port 8088
```

```powershell
# benchmark 评测（无 UI）：CTFTiny 50 题
python src/ctf_orchestrator/eval_run.py --platform ctftiny --config src/ctf_orchestrator/l2-config.json
# 其余题库（NYU/Cybench/DASCTF2025）见使用手册 §4.1
```

---

## B. Kali 侧（执行机）

### B.1 SSH 与凭据文件 secrets/kali.json

编排机的 pi 扩展（`src/pi-ext/kali.ts`）用 `ssh2` 直连 Kali 执行命令。SSH 凭据读自 `secrets/kali.json`，字段（**字段名，不含真值**）：

```jsonc
// <REPO>/secrets/kali.json —— Kali SSH 凭据（已 gitignore，队友需自建，见 SECRETS-CHECKLIST §4）
{
  "host": "<KALI_IP>",        // 例："10.174.153.128"
  "port": 22,
  "username": "kali",          // 用 kali 用户登录，命令经 sudo 提权到 root
  "password": "<密码>",
  "sudo": true                 // username=root 或 sudo=false 时直接执行，不 sudo
}
```

字段名也可用环境变量覆盖（缺省时读文件）：`KALI_HOST` / `KALI_PORT` / `KALI_USER` / `KALI_PASSWORD` / `KALI_SUDO`，配置文件路径可用 `KALI_SSH_CONFIG` 覆盖。

### B.2 CTF 工具链

pip 工具链（Kali 用 `--break-system-packages` 规避 PEP 668，或建 venv）：

```bash
pip3 config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple   # 可选：国内加速
pip3 install --break-system-packages pwntools z3-solver sympy angr fpylll blutter
# 常用加装（bootstrap 脚本同款）：
pip3 install --break-system-packages pycryptodome gmpy2 scapy requests beautifulsoup4 lxml flask \
    capstone unicorn ropper ROPgadget volatility3
```

apt 工具（`jadx`、`stegseek` 是二进制包，不是 pip 包）：

```bash
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    gdb gcc g++ make binutils file strings ltrace strace \
    binwalk foremost zsteg exiftool john hashcat hydra radare2 \
    nmap netcat-openbsd socat dnsutils whois sqlmap ffuf gobuster \
    tcpdump tshark jadx apktool
sudo apt-get install -y stegseek   # 注意：stegseek/steghide 可能与 php 相关包依赖冲突，冲突时先修 apt 源或用 steghide 替代
```

> 一键脚本参考：`src/kali-setup/bootstrap-kali.sh`（幂等，含 apt 镜像源切换 + 上述工具 + 自检）。
> 备注：`blutter`（Flutter 逆向）首次运行会联网下载 Flutter 引擎；`fpylll` 是格基约简库（crypto 题用）。

### B.3 SageMath 10.9（podman 容器包装，`/usr/local/bin/sage`）

Kali apt 无 SageMath 包、pip 无 py3.13 wheel，改用 **podman 跑官方镜像 + 包装脚本**，让 worker 无感知地直接敲 `sage xxx.sage`。

```bash
# 1) 拉官方镜像（一次性）
sudo podman pull docker.io/sagemath/sagemath:latest

# 2) 写包装脚本 /usr/local/bin/sage（注意必须 --user 0：容器内以 root 跑，才能写挂载的 /root/ctf）
sudo tee /usr/local/bin/sage >/dev/null <<'EOF'
#!/bin/bash
# SageMath 容器包装：worker 直接敲 sage xxx.sage，无需知道容器存在
DIR=$(pwd)
MOUNTS="-v /root/ctf:/root/ctf:rw"
case "$DIR" in
  /root/ctf*) ;;
  *) MOUNTS="$MOUNTS -v $DIR:$DIR:rw" ;;
esac
exec podman run --rm -i --user 0 $MOUNTS -w "$DIR" -e HOME=/root docker.io/sagemath/sagemath:latest sage "$@"
EOF

# 3) 赋予执行权限并自测
sudo chmod +x /usr/local/bin/sage
mkdir -p /root/ctf/preflight && cd /root/ctf/preflight
printf 'print(factor(15))\n' > t.sage
sage t.sage          # 期望输出 3 * 5
```

### B.4 pwndbg

```bash
sudo git clone https://github.com/pwndbg/pwndbg /opt/pwndbg
cd /opt/pwndbg
sudo ./setup.sh
echo "source /opt/pwndbg/gdbinit.py" >> ~/.gdbinit
# 自测：gdb -q -batch -ex 'quit' 2>&1 | grep -ci pwndbg  （>0 即装好）
```

> 本机为 gdb 17.2 + pwndbg（`/opt/pwndbg`），`preflight.py` 第 4c 项按上面这条自测命令判绿。

### B.5 podman 与 podman.socket

```bash
sudo apt-get install -y podman
# docker-in-docker 类题需要宿主管道式套接字：
sudo systemctl enable --now podman.socket   # 提供 /run/podman/podman.sock
podman --version   # 自测
```

### B.6 benchmark 服务题镜像拉取

服务题的 box 是 CSAW 当年的公网靶机（早下线），做法是把官方镜像拉到 Kali 本地，`podman run` 起容器当靶机（详见 `docs/服务题容器运行.md`）。拉取脚本在 `src/tools/`：

```bash
# 先把脚本传到 Kali /root/（或用 src/tools/regen-ctftiny-pull.py / regen-pull-script.py 重新生成）
sudo bash /root/pull-ctftiny.sh          # CTFTiny 50 题需 27 个镜像，约 10-13GB
sudo bash /root/pull-service-images.sh   # 全量 165 个唯一镜像，约 65-90GB；失败可加重试次数： bash pull-service-images.sh 3
```

- 拉过后镜像留在 podman 本地存储，之后跑 benchmark **不需要 VPN**；
- 缺镜像的题自动跳过（日志 `image_missing`），跑分不受影响；跑分结束自动清理容器。

### B.7 REST :5000 健康闸门说明

Kali 上还跑着一个 Flask REST（MCP-Kali-Server），`:5000/health` 返回 `healthy`。它只作**健康闸门**：`preflight.py`（第 3 项 Kali API）、`eval_run.py`、`dashboard.py` 的 Kali 状态用它探测。**实际的工具调用已全部走 SSH（kali.ts）**——所以 Kali API(5000) 挂了不影响解题，只影响 preflight/eval 的健康闸门判定（见使用手册 FAQ）。

---

## C. 模型配置

系统有**两份**模型配置，别搞混：

1. **pi 运行时注册表** `%USERPROFILE%\.pi\agent\models.json` —— pi CLI 自己读，决定「有哪些 provider/模型、key 从哪个环境变量取」；
2. **统一 LLM 配置** `<REPO>/config/agent.json` + `<REPO>/config/secrets.json` —— 编排器/看板读，决定「哪个角色用哪个模型、并发/开关」。

### C.1 pi 模型注册表 `%USERPROFILE%\.pi\agent\models.json`

`preflight.py` 第 5 项还检查 `~/.pi/agent/skills`（技能包 ≥5 个），该目录与 models.json 同在一个 `.pi/agent` 树下。

```jsonc
// %USERPROFILE%\.pi\agent\models.json —— pi 运行时模型注册表
{
  "providers": {
    "deepseek-direct": {
      "baseUrl": "https://api.deepseek.com",
      "api": "openai-completions",          // 协议：openai-completions / anthropic-messages
      "apiKey": "$DEEPSEEK_API_KEY",        // $VAR 占位符：从同名环境变量读，不写真值
      "models": [
        { "id": "deepseek-chat",      "reasoning": false, "contextWindow": 128000, "maxTokens": 8192 },
        { "id": "deepseek-reasoner",  "reasoning": true,  "contextWindow": 128000, "maxTokens": 16384 }
      ]
    },
    "openai": {
      "baseUrl": "https://api.openai.com/v1",
      "api": "openai-completions",
      "apiKey": "$OPENAI_API_KEY",
      "models": [ { "id": "gpt-4o", "reasoning": false, "contextWindow": 128000, "maxTokens": 16384 } ]
    }
  }
}
```

要点：`apiKey` 用 `$环境变量名` 占位符，key 本体不进文件；`config/agent.json` 里 `llm.*.model` 引用的模型 id 必须出现在这里某个 provider 的 `models` 里，否则 pi 运行时找不到模型。

### C.2 config/agent.json + config/secrets.json

`<REPO>/config/agent.json` 是统一 LLM 配置（**已随仓库提交**，含团队默认；缺失时
`agent_config.py` 的 `DEFAULT_CONFIG` 兜底，Web UI「⚙ 配置」页保存时写出/合并）。
`config/secrets.json` 存 API key，**已 gitignore，永不提交**。结构：

```jsonc
// <REPO>/config/agent.json —— 统一 LLM 配置（可提交，含团队默认）
{
  "llm": {
    "strong":   { "model": "deepseek-v4-pro",  "thinking": "medium" },  // 强 worker
    "weak":     { "model": "deepseek-v4-flash", "thinking": "low" },    // 弱 worker（竞速）
    "planner":  { "model": "deepseek-v4-pro" },                         // 出总体思路
    "observer": { "model": "deepseek-v4-pro",  "thinking": "medium" },  // Supervisor
    "digest":   { "model": "deepseek-chat" }                            // 日志摘要
  },
  "runtime": {
    "max_parallel_challenges": 3,
    "planning_enabled": true,
    "supervisor_enabled": true,
    "kb_enabled": false
  },
  "providers": [
    { "id": "deepseek", "label": "DeepSeek",
      "base_url": "https://api.deepseek.com",
      "api_key_env": "DEEPSEEK_API_KEY",
      "models": ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat"] },
    { "id": "openai", "label": "OpenAI",
      "base_url": "https://api.openai.com/v1",
      "api_key_env": "OPENAI_API_KEY",
      "models": ["gpt-4o"] },
    { "id": "anthropic", "label": "Anthropic",
      "base_url": "https://api.anthropic.com",
      "api_key_env": "ANTHROPIC_API_KEY",
      "models": ["claude-sonnet-4-20250514"] }
  ]
}
```

`<REPO>/config/secrets.json` 是 provider 密钥（**gitignore，绝不上传**，字段为 `provider id → key`）：

```jsonc
// <REPO>/config/secrets.json —— provider id → API key（gitignore）
{
  "deepseek": "sk-你的真实key",
  "openai": "sk-...",
  "anthropic": "sk-ant-..."
}
```

DeepSeek key 的读取顺序（`agent_config.py`）：`config/secrets.json["deepseek"]` → 环境变量 `DEEPSEEK_API_KEY` → 旧 `secrets/deepseek.key`（历史兜底）。

### C.3 Web UI 配置页设 key

看板左侧栏「⚙ 统一配置」页可直接读写上面两个文件（`dashboard.py` 的 `/api/config`）：

- **API Key 区**：按 provider 填 key，保存即写入 `config/secrets.json` 并注入当前进程环境，新开的 worker/评测进程自动继承；
- **角色模型区**：改 strong/weak/planner/observer/digest 的 model 与 thinking（low/medium/high）；
- **运行时开关区**：`max_parallel_challenges`(1-8)、`planning_enabled`、`supervisor_enabled`、`kb_enabled`。

页面上也注明：模型注册表 `%USERPROFILE%\.pi\agent\models.json` 是 pi 运行时所需的**另一份**配置（见本文 C.1）。

### C.4 环境变量汇总

| 变量 | 用途 | 默认/说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek key | 由 secrets.json 或 secrets/deepseek.key 注入，也可手工设 |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | 其他 provider key | 对应 agent.json 里 provider 的 `api_key_env` |
| `PI_CODING_AGENT_DIR` | pi 的 agent 目录 | 默认 `%USERPROFILE%\.pi\agent`（workers.py `setdefault`） |
| `KALI_API_URL` | Kali REST 健康闸门地址 | 默认 `http://<KALI_IP>:5000` |
| `KALI_HOST`/`KALI_PORT`/`KALI_USER`/`KALI_PASSWORD`/`KALI_SUDO` | Kali SSH 凭据覆盖 | 缺省读 secrets/kali.json |
| `KALI_SSH_CONFIG` | kali.json 路径覆盖 | 默认 `secrets/kali.json` |
| `WORKER_API_URL` | worker-api 回调地址 | 默认 `http://127.0.0.1:8089` |
| `DASCTF_BASE_URL` / `DASCTF_USERNAME` / `DASCTF_PASSWORD` | 真平台凭证 | 测试赛/初赛当天设置 |

---

## D. 验证

### D.1 赛前体检（preflight 全绿清单）

```powershell
python src/ctf_orchestrator/preflight.py
```

共 **13 项**，全部 `[OK]` 才能开赛；末尾打印 `N/13 通过`，全绿即 `全绿，可以开赛 ✅`：

| # | 检查项 | 通过条件 |
|---|---|---|
| 1 | DeepSeek key | `secrets/deepseek.key` 存在且以 `sk-` 开头 |
| 2 | pi CLI 已构建 | `pi-mono/packages/coding-agent/dist/cli.js` 存在 |
| 3 | Kali API | `http://<KALI_IP>:5000/health` 返回 200 且含 `healthy` |
| 4 | Kali CTF 工具链 | Kali 上 `import pwn,z3,angr,sympy` 输出 ok |
| 4b | Kali SageMath | `sage t.sage`（factor(15)）输出 `3 * 5` |
| 4c | Kali pwndbg | `gdb -batch` 输出含 pwndbg |
| 4d | Kali podman | `podman --version` 含 `podman version` |
| 5 | 技能包 | `~/.pi/agent/skills` 下目录数 ≥ 5 |
| 6 | 平台客户端可导入 | `import dasctf_client` 成功 |
| 7 | 编排器可导入 | `import ctf_orchestrator` 成功 |
| 8 | 无孤儿 worker | 无 `node.exe ... cli.js` 进程 |
| 9 | 平台凭证 env | `DASCTF_BASE_URL`/`DASCTF_USERNAME`/`DASCTF_PASSWORD` 均设置（测试赛当天） |
| 10 | workspace 可写 | `workspace/` 可创建并删除 `.write-test` |

### D.2 常见失败速查

| 失败项 | 处理 |
|---|---|
| DeepSeek key | 建 `secrets/deepseek.key`（单行 `sk-...`），见 SECRETS-CHECKLIST §4 |
| pi CLI 已构建 | 回到 A.3 完成 `pi-mono` 的 `npm install && npm run build` |
| Kali API / 工具链 / SageMath / pwndbg / podman | Kali 上按 B.2–B.5 补齐；确认 `:5000/health` 在线 |
| 技能包 | 装好 pi 的 skills（`~/.pi/agent/skills` 下 ≥5 个目录） |
| 平台凭证 env | 测试赛当天 `$env:DASCTF_BASE_URL=...` 等三个变量 |
