# Benchmark 评测体系与隔离设计

> 合并自 BENCHMARK-EVAL + 服务题容器运行 + Cybench 构建流水线 + 网络封锁 + worker 容器隔离方案。
> 一句话：**平时用本地题库评测训练闭环；真值只存 Windows；worker 在 benchmark 下被物理隔离（断外网、看不见真值），比赛下完全不禁网。**

---

## 一、题库全景（全部在 Windows 本地，真值不下 Kali）

| 题库 | 规模 | 适配器 | 说明 |
|---|---|---|---|
| CTFTiny（CSAW 切片） | 50 题 | `CtftinyPlatform` | CSAW 2021-22，含真值 |
| NYU_CTF_Bench（全量上游） | test 200 + dev 57 | 同 `CtftinyPlatform`（--bench-root） | CTFTiny 全量上游，同格式 |
| Cybench | 40 题（4 赛事） | `CybenchPlatform` | **24 服务题 + 19 静态**；难度 1-4 |
| DASCTF 2025 真题 | 13 题 / 7 有真值 | `DasctfEvalPlatform` | 赛味最正 |
| mock 假考场 | 4 题 | `MockHttpPlatform` | 全链路演练 |

## 二、评测完整性铁律（事故后确立，审计按钮在 UI）

1. **真值与 worker 物理隔离**：题库数据只存 Windows；Kali 上只有 `/root/ctf/<cid>/` 工作区附件（曾发生 worker 直读 /root/ctftiny 作弊——polly 复测抓现行）；
2. **prompt 不下发题解**：description 只用原始题目描述，绝不含 solve_notes/writeup（曾发生 DASCTF solve_notes 进 prompt）；
3. **附件排除**：`metadata/`、`solution/`、`htb/`（HTB 官方求解器目录）、`writeup*`、`flag*`、`sol/solve/solver/exploit/solution*.py` 任何层级都不下发；
4. **构建上下文即删**：Cybench 构建目录（含 flag 材料）构建完立刻从 Kali 删除；
5. **联网查题解 = 作弊**：benchmark 下工具层物理封锁（见 §四），审计日志分类 cheat/osint/clean。

## 三、服务题容器运行（靶机重建）

- **CTFTiny/NYU**：`revival.py` 拉取 llmctf/* 官方镜像（已全部就位，之后跑分不需要 VPN），podman 起容器当靶机，端口映射 127.0.0.1 高端口；缺镜像自动跳过；`--no-revive` 可关。
- **Cybench**：`cybuild.py` 本地构建流水线——题目自带 build 上下文（compose + Dockerfile），打包（排除真值）→ SFTP → Kali podman build → 起容器 → 探测 → 覆盖连接点 127.0.0.1。**24/24 服务题全部构建成功并实测存活**（2026-08-16/17 多轮修复：EOL apt 源、temurin 用户冲突、Windows 执行位、容器名截断冲突、werkzeug 探活假死等）。
- **每轮 sync 自愈**：已复活端口先并行探活（先被动横幅后 HTTP 请求行，不污染 pickle 类服务），活着就复用，死了才重建；重建端口漂移会自动刷新 worker 提示词的 connection。
- 特殊题走 `HOST_OVERRIDES` 表（如 msc-showdown 的 socat 包装）。

## 四、benchmark 网络封锁（防"开卷抄解"）

- 触发：仅 `eval_run.py`（benchmark 专用入口）→ `bench_mode=True` → worker 注入 `NET_POLICY=local-only`；
- 工具层（`kali.ts`）拦截：curl/wget/git/pip/npm/apt/go/cargo/podman/docker/ssh/DNS 等命令文本；python urllib/requests/socket 除 127.0.0.1 外全拒；nc/ncat/socat 只允许 127.0.0.1/localhost；
- 提示词：注入【benchmark 网络封锁】条款，并移除"联网搜题解"兜底策略（不自相矛盾）；
- **比赛路径完全不禁网**：`ctf_orchestrator.py` 显式 bench_mode=False，无 NET_POLICY、提示词保留搜索定位条款（见 RACEDAY.md §四）。

## 五、worker 执行环境容器隔离（benchmark 专用，2026-08-17 落地中）

工具层正则可被"写脚本再执行"绕过，所以再加物理隔离：**worker 的 bash/文件工具不再落在 Kali 宿主机 root，而是落进 rootless + userns 的独立容器**。

| 项 | 设计 |
|---|---|
| 执行载体 | Kali 上 `ctfworker` 用户的 rootless podman（userns 100000+ 号段，容器内 root ≠ 宿主机 root） |
| 镜像 | `worker:latest`（kali-rolling + pwntools/angr/z3/sympy/gdb/nmap/sqlmap/binwalk/john/hashcat/radare2 + sshd，随机密码经 -e 注入） |
| 连靶机 | `--network slirp4netns:allow_host_loopback=true`：容器内 `10.0.2.2:<端口>` = 宿主机 127.0.0.1 靶机端口（编排器改写 connection 字段） |
| 断外网 | 宿主机 iptables 按 uid 封死 ctfworker 出站（只放行 127.0.0.0/8 的 22000-22499 靶机段 + 23100-23199 回连段）——物理级，脚本绕过无效 |
| 无真值可达 | 容器不挂宿主机任何目录（无 /var/lib/containers、无其他题目目录），只挂自己的工作区 |
| 回连场景 | 容器发布 23100-23199；靶机经 host-gateway 回连 worker（chunky 类利用的配套约定，提示词说明） |
| 能力 | `--cap-drop=ALL --cap-add=SYS_PTRACE,NET_RAW --security-opt no-new-privileges` |
| 通道 | Windows → paramiko direct-tcpip 隧道 → Kali 127.0.0.1:22xxx → 容器 sshd（全程无公网暴露） |
| 回退 | 容器启动失败自动回退 host 模式并在日志告警（保跑分连续性） |

## 六、三层评测体系

```
L1 冒烟回归（每次架构改动后，<30 分钟）CTFTiny easy 10 题，防退化
L2 全量能力（每个大版本）按题型×难度分组：CTFTiny 50 + NYU 200 + Cybench 40 + DASCTF 7
L3 模拟赛（8/20）DASCTF 配比抽题 + 3 小时倒计时 + 人工盯盘（人只写 hints）
```

## 七、执行方式

```powershell
# Cybench 全量（24 服务题自动构建起容器）
python src/ctf_orchestrator/eval_run.py --platform cybench
# CTFTiny 默认 50 题
python src/ctf_orchestrator/eval_run.py --platform ctftiny
# NYU 全量 200 题
python src/ctf_orchestrator/eval_run.py --platform ctftiny --bench-root D:/ctf-agent/benchmarks/nyu-ctf-bench --bench-meta test_dataset.json
# DASCTF 2025（7 题有真值）
python src/ctf_orchestrator/eval_run.py --platform dasctf2025
```

看板 Benchmark 页（http://127.0.0.1:8088）可视化跑分/停止/续跑/归档。历史成绩与事故见 `_archive/EVAL-LOG.md`。

## 八、评测指标（对齐官方考察点）

| 官方考察点 | 指标 |
|---|---|
| 设计/调度 | 解题率（题型×难度×是否 OSINT） |
| 有限时间 | 单题耗时分布、3h 模拟总分 |
| 优化 | 提交效率（错交率、冷却触发） |
| 人机协作 | 人工介入率、hint 响应延迟 |
| 训练 | 版本提升曲线（解题率/耗时/cost） |
| 成本 | 单题/全量 token 与费用 |
