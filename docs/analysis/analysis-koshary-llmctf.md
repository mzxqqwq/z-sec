# Koshary 与 LLM-CTF-Solver 源码级架构拆解 + 西湖论剑 AI Agent 解题夺旗适配分析

> 目标比赛约束（适配输入）：
> - 平台仅开放 API（真实端点未知，可能类 CTFd），Jeopardy（web/pwn/re/crypto/misc）
> - 线上初赛仅 3 小时；有人机交互（人写提示纠偏）；决赛代码审查
> - 执行环境：Windows 主机（编排器+worker）+ Kali 仅 REST（`http://<host>:5000/api/command`，无 Docker 无 SSH）
> - 模型：DeepSeek（OpenAI 兼容）；预算有限
> - 已有：Python 编排器（state.json 黑板 + hints + 并行 + 同题竞速）+ pi worker + kali.ts 桥 + dasctf_client 骨架 + 5 个题型技能包

---

# 第一部分：项目 A —— Koshary（D:\ctf-agent\koshary-ref）

## A1. 模块结构（文件 + 职责）

| 文件 | 职责 |
|---|---|
| `orchestrator.py`（1386 行） | 唯一入口：主循环、solver worker、StateDB、execution engine、并行调度 |
| `core/flag_extractor.py`（109 行） | flag 正则抽取 + placeholder 过滤 + `FINAL_ANSWER_CANDIDATE` 解析 |
| `core/downloads.py`（168 行） | 附件下载 + 压缩包解压（带密码回退） |
| `core/instance_manager.py`（186 行） | HTB 实例生命周期 + 可达性探测 + `target.json`/`instance.json` 持久化 |
| `core/logging_utils.py`（86 行） | 日志 + 密钥脱敏（redact/register_secret） |
| `core/mcp_client.py`（236 行） | 极简 MCP Streamable-HTTP 客户端（initialize/list_tools/call_tool） |
| `core/htb_cookie_client.py`（432 行） | HTB 浏览器 API 客户端（cookie/bearer，Burp headers 导入） |
| `platforms/base.py`（126 行） | `BasePlatform` 抽象 + `NormalizedChallenge`/`SubmitResult` 数据结构 |
| `platforms/ctfd.py`（199 行） | CTFd 适配器（CSRF nonce + 提交判定） |
| `platforms/htb_cookie.py`（244 行） | HTB cookie 模式适配器 |
| `platforms/htb_ctf_mcp.py`（526 行） | HTB MCP 适配器（工具名启发式解析 + 参数映射） |
| `platforms/__init__.py` | 工厂 `get_platform()` |
| `prompts/*.txt` | 按题型分类的 prompt 模板（web/pwn/rev/crypto/misc/forensics/mobile） |
| `runners/run_{codex,gemini,claude}.sh` | 模型 CLI 封装（主模型 + fallback 切换） |
| `first_blood.py` | CTFd 开赛抢首血（关键词匹配 + brute-first） |
| `clean_workspace.py` | 赛后清理 + 密钥擦除 |

## A2. 关键机制代码级拆解

### A2.1 agent 主循环（solver worker = `process_challenge`）

`orchestrator.py:754-927` 是每题一个 worker。核心流程：

1. **按类别路由模型**：`choose_route`（`orchestrator.py:214-219`）用子串匹配把 category 映射到 `config["routing"]` 里的 route：
```python
def choose_route(category: str, routing: Dict[str, str]) -> Optional[str]:
    c = category.lower().strip()
    for key, route in routing.items():
        if key in c:
            return route
    return None
```
再经 `resolve_model_key`（`:270-284`）选具体模型 key（如 codex 路由下 pwn→`codex_pwn`、rev→`codex_rev`，缺 key 时走 fallback 列表）。

2. **每题工作区**：`challenge_workspace`（`:714-728`）按平台拼路径；`chdir/agent_rounds`、`chdir/files` 两子目录（`:785-788`）。用 `chdir/.lock` 文件锁防重复（`:790-792`，`acquire_lock` 是 `O_CREAT|O_EXCL` 原子创建，`:661-667`）。

3. **plan 阶段（可选）**：`generate_challenge_plan`（`:677-708`）单独跑一次模型，输出 `plan.md`，仅提示“不要写代码”，产物作为后续每轮 prompt 的 `{plan}` 变量。

4. **附件下载**：`platform.download_files(chall, files_dir)`（`:799-803`）委托给平台适配器。

5. **主循环（每轮）**：`orchestrator.py:850-918`
```python
for round_no in range(1, max_rounds + 1):   # max_rounds 默认 8
    workspace_text = collect_workspace_text(chdir)      # :338-371，最多 40 文件/每文件 12k 字符
    history_text = collect_history_text(chdir, round_no) # :374-397，round_{n}.out/exec/commands
    # 僵局检测：workspace 不变即 idle
    workspace_hash = sha1_text(workspace_text)          # :862
    if workspace_hash == last_workspace_hash:
        idle_rounds += 1
        if idle_rounds >= max_idle_rounds: break        # 默认 3 轮不变即终止
    ...
    prompt = render_prompt(...)                          # :871
    rc, output = run_model(runner_cmd, prompt_file, chdir)  # 调 CLI，默认 300s
    lang, code = extract_first_code_block(output)        # :880，正则 ```lang\n...```
    run_commands = extract_run_commands(output)         # :881，行首 RUN: 前缀
    # RUN: 命令执行（最多 4 条，命令超时 90s）
    cmd_results = run_model_commands(run_commands, chdir, ...)  # :891
    # 代码块落盘为 artifact 并执行（py/sh/js/rb/pl）
    artifact_path = save_generated_artifact(chdir, round_no, lang, code)  # :898
    exec_rc, exec_output = run_generated_artifact(artifact_path, ...)    # :899
    # flag 检测与提交
    candidates = extract_flags(combined_text, config["ctf"]["flag_patterns"])  # :905
    if _solve_with_candidates(chall, candidates, platform, db, chdir, submit_mode, max_wrong):
        solved = True; break
```

### A2.2 执行引擎（本地 subprocess，非 Docker）

`run_subprocess`（`:595-611`）、`run_generated_artifact`（`:640-647`）都是 `subprocess.run(capture_output, timeout)` 直接在本机执行，**无任何沙箱/黑名单**。`ensure_executable`（`:582-584`）给 `.sh` 加执行位。

### A2.3 flag 抽取器

`core/flag_extractor.py:45-64`：多 pattern 循环 `re.findall(IGNORECASE)`，tuple 归一化，`is_placeholder_flag`（`:31-42`）过滤 `flag{...}`/`HTB{...}` 等占位，`dict.fromkeys` 去重保序。非标准答案走 `extract_candidate_answers`（`:74-109`）解析 `FINAL_ANSWER_CANDIDATE:` + 后续 `CONFIDENCE:`/`EVIDENCE:` 行。

### A2.4 flag 提交 + 防重复/防超错

`_solve_with_candidates`（`orchestrator.py:950-988`）：
- `db.attempted_flag(cid, flag)` 去重（`:955`）
- `submit_mode`：none/manual/auto（`:958-966`）
- `max_wrong` 超限即停止提交该题（`:968-970`，HTB 默认 3，CTFd 默认 0 无限）
- `platform.submit_flag` 返回 `SubmitResult{accepted, message, raw, already_solved}`（`platforms/base.py:61-68`）
- 成功后 `db.mark_solved` 写 `submitted` 记录（`:539-547`、`:523-537`）

CTFd 提交判定 `platforms/ctfd.py:188-199`：`accepted = resp.success and not any(bad in msg for bad in ("incorrect","wrong"))`。

### A2.5 题型分类器（Koshary 的“分类”是**静态配置路由**，非模型分类）

没有独立 classifier 文件；分类逻辑就是 `config.json` 的 `routing` 表（`config.json:68-89`，web→gemini、crypto→claude、pwn→codex…）+ `category_matches_filter`（`orchestrator.py:222-244`，含 alias 组 {pwn,binary,binary exploitation} 等）。**这是纯规则，零 LLM 成本。**

### A2.6 StateDB（state.json 黑板）

`orchestrator.py:467-569`：`state/db.json` 存 `solved_ids`、`submitted`（每题每次提交记录）、`challenges`（每题 route/status/workdir/last_update）、`errors`、`stats`。`save()` 全程 `STATE_LOCK` 线程锁保护（`:484-486`），支持多 worker 并发写。`mark_solved`/`mark_attempt`/`attempted_flag`/`count_attempts` 支撑去重与限错。

### A2.7 平台适配器（对“未知 API”最相关）

`platforms/base.py:71-126` 定义 5 个抽象方法（list_challenges/get_challenge/download_files/submit_flag/needs_instance）+ 可选的 instance 生命周期。**编排器只面向 `BasePlatform` 与 `NormalizedChallenge`**，平台差异全部收敛在适配器。

对未知 API 的两种自适应手段：
1. **CTFd 标准**（`platforms/ctfd.py:22-95`）：CSRF nonce 从 `/challenges` HTML 里正则抓（`:34-52`），`submit_flag` POST `/api/v1/challenges/attempt` 带 `CSRF-Token` 头（`:81-95`）。
2. **HTB 浏览器 API 逆向**（`core/htb_cookie_client.py`）：`parse_headers_file`（`:54-68`）从 Burp 抓包原样解析 cookie/authorization/user-agent 三个头，`submit_flag` POST `/api/flags/own`（`:333-344`），`_json` 对 401/403 抛 `HTBAuthError`（`:184-198`）。**这是“未知端点只有抓包”场景的标准打法。**

HTB MCP 适配器（`platforms/htb_ctf_mcp.py`）额外演示了两件事：
- **工具名启发式解析** `_resolve`（`:102-122`）：先查 config 覆盖，再按 `_ROLE_KEYWORDS`（`:38-53`，如 `submit_flag` = ["submit"] AND ["flag"]）在 `tools/list` 结果里匹配真实工具名。
- **参数映射** `_build_args`（`:130-148`）：逻辑 key（event/challenge/flag）→ 工具 inputSchema 真实属性名（`_ARG_ALIASES` `:56-63`）。

### A2.8 并行调度与求解队列

`main`（`:1352-1374`）：`ThreadPoolExecutor(max_workers=parallel_workers)`，fullpwn 强制串行（`:1353-1354`）。`strategy_rank`（`:1046-1057`）给“先解哪题”打分：`points + 40*has_files + 25*kind_bonus - 30*failed_attempts`。

### A2.9 模型 runner 的 fallback 模式

`runners/run_codex.sh:29-63`：主模型失败/超时/quota 后自动切 fallback 模型（`gpt-5.5`→`gpt-5.3-codex`）；`run_claude.sh` 同理（opus→sonnet）；`run_gemini.sh:22-30`（pro→flash）。模型调用是**子进程调用 CLI**（`run_model` `:614-622`），非 API 直连。

---

# 第二部分：项目 B —— LLM-CTF-Solver（D:\ctf-agent\llmctf-ref）

## B1. 模块结构

| 文件/目录 | 职责 |
|---|---|
| `agent/solve_agent.py`（1603 行） | SolveAgent 主循环、三层解析回退、6 维僵局检测、工具执行、缓存 |
| `agent/memory.py`（344 行） | 三层记忆 + LLM 日志整合压缩 + 关键事实防丢 |
| `agent/checkpoint.py`（205 行） | 断点续跑（存档/读档/保留 3 份） |
| `agent/analyzer.py`（88 行） | 每步输出的 LLM 分析（JSON + 修复回退） |
| `agent/workflow.py`（892 行） | 入口、预处理、自动学习、writeup/report 生成 |
| `agent/attack_surface.py`（598 行） | 渗透模式攻击面/漏洞/凭据管理（CTF 模式不用） |
| `agent/user_interface.py`（78 行） | UI 抽象（CLI/TUI），人机交互接口 |
| `ctf_tool/*.py`（~20 个工具） | 题型工具集（web/crypto/reverse/stego/forensics/binary/codec/network…） |
| `ctf_tool/base_tool.py`（28 行） | 工具基类（execute/function_config/modes/tags） |
| `ctf_tool/flag_detector.py`（59 行） | flag 正则预检测 |
| `ctf_tool/challenge_classifier.py`（393 行） | 题型自动分类（后缀+关键词+连接） |
| `ctf_tool/ssh_shell.py`/`ssh_client.py` | Kali SSH 远程执行 |
| `ctf_tool/python.py`（318 行） | Python 代码执行（AST 审计 + subprocess/docker 沙箱 + 远程） |
| `ctf_tool/mcp_adapter.py`（318 行） | MCP 工具适配（stdio/http） |
| `utils/tools.py`（584 行） | 工具加载（反射）、工具推荐（向量）、XML/JSON 解析 |
| `utils/output_parser.py`（561 行） | 规则化工具输出压缩（非 LLM） |
| `utils/llm_request.py`（177 行） | litellm 封装（重试 + 语义缓存 + token 统计） |
| `utils/semantic_cache.py`（297 行） | L1 精确 + L2 语义双层 LLM 缓存 |
| `utils/tool_cache.py`（88 行） | 工具结果 TTL 缓存 + 防缓存死锁 |
| `utils/security.py`（339 行） | 命令黑名单（含 base64/hex 解码复检） |
| `utils/env_probe.py`（294 行） | Kali 环境探测（工具/模块/网络） |
| `utils/dynamic_resolver.py`（325 行） | 远程工具动态发现（SSH） |
| `rag/`（knowledge_base/rag_service/seed_data） | ChromaDB RAG（BM25+向量混合检索） |
| `backend/`（FastAPI + WS） | Web UI 编排层（本轮可忽略） |

## B2. 关键机制代码级拆解

### B2.1 SolveAgent 主循环（ReAct）

`solve()`（`agent/solve_agent.py:305-672`）是典型 ReAct 循环，每步：

1. **终止/成本检查**：外部 stop_event、`_max_steps`（默认 100，`:349`）、**成本熔断** `max_solve_cost_usd`（`:354-363`，token_tracker 累计成本达标即终止）。
2. `next_instruction()` 生成 (think, tool_calls)（`:371`）。
3. **思考循环检测**（`:381-398`）：`_normalize_think` 归一化指纹相同连续 ≥2 次 → 绕过语义缓存 + 清工具缓存。
4. **手动模式**：`manual_approval_step`（`:674-701`）等用户批准/反馈/终止；反馈走 `reflection`（`:703-739`）二次生成。
5. **多工具并行执行**（`:412-439`）：`ThreadPoolExecutor(max_workers=min(len(tool_calls),5))`，按索引回填保序。
6. **工具错误率追踪**（`:444-453`）：输出含 error/traceback/connection refused 等关键词记 `_recent_tool_errors`（维度 6 原料）。
7. **flag 正则预检测**（`:475-478`）：`detect_flag` 命中即零 LLM 成本确认。
8. **分析**（`:481-493`）：`_quick_analysis`（`:1275-1341`）能覆盖的（已中 flag/短输出/纯枚举）直接出结果，否则调 `analyzer.analyze_step_output`。
9. **flag 综合判定**（`:504-518`）：`flag_candidate = pre_flag or llm_flag`，经 `confirm_flag_callback` 让用户确认，正确才 `_clear_checkpoint` + return。
10. **阶段切换** `_check_phase_transition`（`:191-224`）、**僵局检测** `_detect_stuck_step`（`:611`）、**连续无进展终止**（`:626-645`）。
11. **每步记忆写入** `memory.add_step`（`:572-583`）+ `_write_journal_entry`（`:586-593`）。
12. **自动存档**：`_step_count % checkpoint_interval == 0` 时 `_save_checkpoint`（`:671-672`）。

### B2.2 三层解析回退（题目要求的“ReAct+三层解析回退”）

`next_instruction()`（`agent/solve_agent.py:863-1013`）的产出链是**三层**：

- **Layer 1 — 原生 function calling**（`:954-972`）：模型返回 `message.tool_calls`，`ToolUtils.parse_tool_calls` 解析；对不在可用列表的工具名尝试 `inject_dynamic_tools` 动态发现，再校验。
- **Layer 2 — 文本提取（堆栈法 JSON → XML）**（`:974-992`）：`_extract_tool_calls_from_content`（`:821-842`）先用 `_extract_json_blocks`（`:844-861`）**堆栈法**提取所有平衡 `{}` 块（按长度降序逐个 `json.loads` 尝试，识别 `tool_calls` 数组或单工具），失败回退 `ToolUtils._parse_xml_tool_calls`（`utils/tools.py:476-502`，`<tool_calls><tool_call name=...><arg key=...>`）。
- **Layer 3 — general_next 二次生成**（`:994-1013`）：前两层都无有效调用时，用 `extract_tool_mentions`（`utils/dynamic_resolver.py:107-140`）从 think 里抽工具名，`recommend_tools` 收窄工具集，再调 `tool_general`（`:1343-1364`，`json_check=True` 强制 JSON）单独生成工具调用。

另有独立的**分析层 JSON 回退**（`agent/analyzer.py:55-68`）：`json.loads` 失败 → `fix_json_with_llm` 修复 → 仍失败 → `_fallback_analysis`（`:70-88` 返回 progress_level="minor" 的兜底结构）。`utils/tools.py:parse_tool_calls`（`:504-559`）同样内置 json→fix_json→XML 三阶。

### B2.3 六维僵局检测（`_detect_stuck_step`，`agent/solve_agent.py:1393-1479`）

| 维度 | 规则 | 位置 |
|---|---|---|
| D1 LLM 标记无进展 | `progress_level=="none"` 且 step≥5，且需 1 个规则信号（最近 4 工具中当前工具≥2 次）配合 | `:1463-1473` |
| D2 连续相同工具 | 最近 4 个工具名中最高频 ≥3 次 | `:1407-1418` |
| D3 输出语义相似 | 最近 3 个输出样本完全相同，或 `SequenceMatcher.ratio()>0.85` | `:1420-1436` |
| D4 思考意图循环 | 最近 5 个意图指纹（`_extract_thought_intent` `:1383-1391` 抽 curl/jwt/爆破/扫描等动作词）最高频 ≥3 次 | `:1438-1447` |
| D5 步数多无进展 | step≥15 且 `_has_progress`（`:1481-1496`）为假 | `:1475-1477` |
| D6 工具错误率 | 最近 ≥5 次工具执行中错误率 ≥60%（纯规则，不依赖 LLM） | `:1449-1457` |

**触发后**：`_stuck_counter` 累计，达到 `max_stuck_steps`（默认 5）→ `_strategy_switch_count++` + `_build_switch_prompt`（`:1581-1603`，按切换次数给三级递进提示：换思路→换工具类别→质疑题型分类）+ `memory.add_fact` 注入。另有独立**连续无进展强制终止**（`:626-645`）：`progress_level=="none"` 连续 5 次（`_max_consecutive_none`）且输出变化 <30%（`_output_significantly_changed` `:1498-1515`）→ 返回 "exhausted_methods"。

### B2.4 三层记忆 + 压缩（`agent/memory.py`）

数据结构（`:36-40`）：
- **hot**：`history: List[Dict]` —— 最近详细步骤（think/tool_args/output/analysis 全量），`get_summary` 只取最近 6 步（`:117-148`）。
- **warm**：`journal_entries: List[str]` —— 每步 LLM 生成 150-350 字叙事日志（`_write_journal_entry` `solve_agent.py:1183-1224`），保持最近 `_CONSOLIDATION_INTERVAL=5` 条原样（`:12`）。
- **cold**：`consolidated_narrative: str` —— 早期日志的整合叙事，上限 `_NARRATIVE_MAX_CHARS=10000`（`:13`）。
- 另：`_external_facts`（策略切换提示）、`failed_attempts`（失败尝试计数，key 为规范化 tool_args）。

**压缩机制**（`_consolidate_journal` `:152-204`）：`journal_entries` 达到 `INTERVAL*2=10` 条时，保留最近 5 条，其余交给 LLM 按“宁可长不可丢（IP/端口/路径/凭据/flag/payload/失败原因一个都不能少）”合并进叙事；超 10000 字再 `_compress_narrative` 二次压缩（`:234-255`）。LLM 失败降级 `_fallback_consolidate`（`:206-232`，只保留 `## 步N` 骨架 + 关键事实）。**关键事实防丢** `_ensure_protected_facts`（`:257-290`）：整合后扫描 `_PROTECTED_PATTERNS`（`:17-29`，flag/password/Bearer/JWT/DB 连接串/AWS key/私钥/shadow 行/sha256/md5），缺失的以“关键事实（防丢）”块强制追加回叙事末尾。

去重 `_is_duplicate_step`（`:326-344`）：think 前 200 字符 + 规范化 tool_args（`_normalize_tool_args_key` `:294-324`，list 排序后 join、dict sort_keys）+ output 前 500 字符三者全同才判重。

### B2.5 flag 检测/提交

`ctf_tool/flag_detector.py:7-16` 五级 pattern（赛事前缀→`flag{}`/`CTF{}`/`HTB{}`→通用兜底 `[A-Za-z0-9_]{3,}\{…10+字符\}`），`_FALSE_POSITIVE_PATTERNS`（`:19-26`）过滤 `function(){...}`/JSON key/HTML 标签/URL 等误报。提交判定不在工具层：CTF 模式下由 `analyzer` 输出 `flag_found/flag/flag_confidence`（`prompts/v1/prompt.yaml:180-188` 定义了四类证据标准），`solve()` 用 `pre_flag or llm_flag` 经 `confirm_flag_callback` 人工确认（`solve_agent.py:504-518`），`workflow.confirm_flag`（`workflow.py:887-891`）是 CLI 确认。学习入库前还有 `_looks_like_flag`（`workflow.py:593-614`）格式白名单 + `_quality_gate_ctf`（`:572-591`，步数>1、用过工具、摘要>50 字）。

### B2.6 题型分类器（`ctf_tool/challenge_classifier.py`）

纯规则、零 LLM：`_EXTENSION_MAP`（`:16-101`，后缀→题型，如 `.elf→[pwn,binary]`、`.pcap→[forensics]`）、`_KEYWORD_MAP`（`:104-184`，正则+权重，如 `sql\s*inject|sqli→web 0.9`、`rsa|公钥→crypto 0.9`）、`_CONNECTION_MAP`（`:187-195`，`nc host port→pwn`）。`classify`（`:229-276`）加权合并（描述 0.6 + 文件 0.3 + 连接 0.1），返回 `primary_type`+`confidence`+`toolchain`。用途：步骤零 RAG 查询（`solve_agent.py:1041-1049`）、MCP 自动激活（`_auto_activate_mcp` `:1061-1077`）、writeup 题型标注。

### B2.7 Kali SSH 执行（`ctf_tool/ssh_client.py` + `ssh_shell.py`）

`SSHClient`（`ssh_client.py:16-187`）基于 paramiko：`connect`（`:31-73`）连续失败 3 次进入 30s 冷却（`_RECONNECT_COOLDOWN=30`，`:13`）；`exec`（`:107-112`）`exec_command` 合并 stdout/stderr，30s 超时；`upload_folder`（`:116-144`）SFTP 递归上传附件；`write_file`（`:146-159`）SFTP 直写（绕 shell 防注入）。`SSHShell.execute`（`ssh_shell.py:30-55`）：取 `arguments["content"]` 作为整条命令，冷却期直接短路返回，首次连接后延迟上传 `./attachments`。

**环境探测**（`utils/env_probe.py:153-212`）：启动时一次性 SSH 跑探测脚本（`_build_probe_script` `:43-83`，which 35 个工具 + import 19 个 Python 模块 + curl 网络 + uname），结果 `format_env_context`（`:253-294`）生成“Kali 执行环境”块，**每步注入 think prompt**（`solve_agent.py:896-897`），消除工具/网络盲区。

### B2.8 本地 AST 沙箱（`ctf_tool/python.py`）

`execute`（`:179-193`）顺序：`_fix_indentation`（`:108-129`，LLM 缩进修复）→ `get_blacklist().check_or_raise` → 远程模式走 SSH 写临时文件执行（`:267-277`）→ Docker 模式 `_execute_in_docker`（`:226-263`，`--network none --memory 256m --cpus 1 --pids-limit 50 --read-only`）→ **无 Docker 时 subprocess 回退**：先 `_audit_ast`（`:131-177`）AST 静态审计（禁止 `_DANGEROUS_MODULES`（`:18-22`，os/subprocess/socket/sys/importlib…）导入、`exec/eval/compile/getattr(__import__…)` 调用、`open(w/a)` 写文件），通过后 `_execute_locally`（`:197-222`）临时文件执行 30s 超时。另有全局命令黑名单 `utils/security.py:232-255`：先原文匹配，再对 base64/hex 编码片段解码复检（`_extract_encoded_chunks` `:257-308`）。

### B2.9 缓存体系（三层）

1. **工具结果缓存** `utils/tool_cache.py:16-88`：key=`MD5(tool_name+sorted args)`，TTL 300s；**防缓存死锁**：同一 key 连续命中 ≥2 次强制返回 None（`:47-54`）。
2. **LLM 语义缓存** `utils/semantic_cache.py:40-297`：L1 精确（MD5，归一化步骤号/时间戳/tmp 路径 `:64-73`），L2 语义（cosine≥0.92 直接命中，≥0.78 需关键词重叠率≥0.3 校验 `:144-159`）。配合 `_build_cache_fingerprint`（`solve_agent.py:787-815`）只对 prompt 的“动态核心”做 embedding 防超长。
3. **RAG 查询缓存** `solve_agent.py:129-131, 1015-1026`：query 的 md5 前 16 位 TTL 300s。
4. 另有**工具向量缓存** `utils/tools.py:49-98`（MCP 工具描述 embedding 落盘复用）。

### B2.10 断点续跑（`agent/checkpoint.py`）

`save`（`:33-114`）：key=`mode + md5(problem)[:12]`（`:27-30`），序列化**完整 agent_state**（stuck 计数器/6 维滑动窗口/阶段/缓存标记 `:79-96`）+ **完整 memory**（history/journal/narrative/failed_attempts `:97-103`），保留最近 3 份（`:110`）。`load`（`:117-141`）按 mtime 取最新，`_restore_from_checkpoint`（`solve_agent.py:226-258`）全量恢复。`clear_key`（`:179-183`）成功解题后清空。

### B2.11 RAG（ChromaDB）

`rag/rag_service.py:183-236` 混合检索：向量（取 3n 个）→ 本地轻量 BM25（`_BM25Scorer` `:17-78`，不依赖 rank_bm25）→ 归一化融合（BM25 权重 0.3，`:222-224`）→ 可选 LLM rerank（`:238-279`）。持久化 `chromadb.PersistentClient`（`:102-105`）。种子数据 `rag/seed_data/*.json`（web/pwn/crypto/reverse/stego/forensics 各 30-72 条）。

---

# 第三部分：适配分析（拿什么 / 改什么 / 怎么改 / 为什么）

> 标记约定：**直接抄**=逻辑可照搬只需接我们已有管道；**必须改**=思路可用但受 3h/无 Docker/Kali-REST/未知 API/预算约束必须重构；**放弃**=在此赛制下净收益为负。

## C1. 逐机制结论总表

| 机制 | 结论 | 关键点 |
|---|---|---|
| Koshary BasePlatform + NormalizedChallenge | **直接抄** | 未知 API 的统一抽象层 |
| Koshary CTFdClient（CSRF nonce） | **直接抄** | 类 CTFd 平台最可能命中 |
| Koshary htb_cookie_client（Burp→API） | **直接抄** | dasctf_client 骨架的参照 |
| Koshary StateDB 黑板 | 已有（state.json），**对齐字段即可** | 补充 attempted/去重语义 |
| Koshary 题型路由（config 规则表） | **直接抄** | 零 LLM、确定性、可审查 |
| Koshary plan 阶段 | **改**（降级为可选） | 3h 下 plan 额外 1 次调用需权衡 |
| Koshary 每轮 workspace/history 回注 | **直接抄**（参数调小） | 3h 下需更激进截断 |
| Koshary RUN:/code-block 执行 | **改**（接 Kali REST） | 本机 subprocess→kali REST |
| Koshary runner fallback 模型切换 | **改**（接 DeepSeek API） | CLI→OpenAI 兼容 SDK |
| LLMCTF 三层解析回退 | **直接抄** | DeepSeek 也是 OpenAI 兼容 |
| LLMCTF 6 维僵局检测 | **直接抄**（阈值调紧） | 3h 最值钱，见 C7 |
| LLMCTF 三层记忆+压缩 | **直接抄**（去掉 journal LLM 二次调用） | 见 C7 价值排序 |
| LLMCTF checkpoint 断点续跑 | **直接抄** | 决赛审查 + 意外中断刚需 |
| LLMCTF 题型分类器 | **直接抄** | 我们已有 5 个技能包，用它做路由 |
| LLMCTF flag_detector | **直接抄**（模式改西湖论剑前缀） | 未知 flag 前缀需通用兜底 |
| LLMCTF Kali SSH（ssh_client/env_probe/dynamic_resolver） | **必须改**（SSH→REST） | 见 C3 |
| LLMCTF AST 沙箱 python.py | **改**（弃 docker，留 subprocess+AST） | 本地只跑纯算法，攻击进 Kali |
| LLMCTF 命令黑名单 security.py | **直接抄**（放 Kali REST 侧） | 决赛审查加分项 |
| LLMCTF 工具缓存/语义缓存 | **改**（缓存默认关或大幅缩短） | 3h 短窗口缓存污染风险高 |
| LLMCTF RAG/ChromaDB/auto_learn | **放弃**（初赛）；决赛可留种子 | 3h 冷启动无积累 |
| LLMCTF 手动模式+reflection（人机交互） | **直接抄**（最高优先级） | 直接命中“人写提示纠偏” |
| LLMCTF output_parser 规则压缩 | **直接抄** | 纯规则零成本省 token |
| LLMCTF env_probe 环境上下文 | **必须改**（改走 REST） | 消除工具盲区很有价值 |
| LLMCTF 成本熔断 max_solve_cost_usd | **直接抄** | 预算有限硬约束 |
| LLMCTF 多工具并行 + 同题竞速 | 已有 | 与我们并行/竞速对齐 |

## C2. 平台 API 未知（可能类 CTFd）—— 怎么改

**拿**：`platforms/base.py:71-126` 的接口契约 + `NormalizedChallenge`（`:17-58`）字段集（含 `raw` 保留原始 payload，`:41`）+ `SubmitResult`（`:61-68`）。把我们的 `dasctf_client` 骨架实现成 `BasePlatform` 子类，编排器就完全平台无关。

**改（关键设计）**：
1. **双模提交判定**。类 CTFd 提交返回结构差异大，统一到 `SubmitResult`，判定逻辑做“布尔优先→关键词兜底”两段式，直接抄 `platforms/htb_ctf_mcp.py:498-516` 的 `_interpret_submission`（先找 `correct/accepted/success/solved/is_correct` 布尔字段，再 `correct|accepted|solved` 且非 `incorrect|wrong|invalid` 关键词）——这比 `ctfd.py:192-194` 只判 `success and not incorrect/wrong` 更鲁棒，适配“未知端点”。
2. **CSRF nonce 自动同步**（`ctfd.py:34-52`）：初赛首次连平台先 GET `/challenges` 抓 nonce；抓不到就退化无头提交。同时把 `htb_cookie_client.py:54-68` 的 `parse_headers_file`（Burp 抓包→cookie/bearer/UA 三头）作为**兜底接入方式**：比赛方给了抓包样例时，10 分钟内就能把 dasctf_client 落地。
3. **提交失败熔断**。抄 `_solve_with_candidates`（`orchestrator.py:968-970`）的 `max_wrong` 上限（建议每题 ≤3），防 3h 内把提交次数刷爆触发平台风控（西湖论剑对异常爆破提交已有治理公告）。

## C3. Kali 只给 REST（`http://<host>:5000/api/command`，无 SSH 无 Docker）—— 怎么改

这是 LLM-CTF-Solver 与我们环境最大的冲突点。它所有“远程”都硬编码 paramiko（`ssh_client.py`、`ssh_shell.py`、`python.py:_execute_remotely`、`env_probe.py`、`dynamic_resolver.py`），必须统一替换为 **Kali REST 客户端**（我们已有的 kali.ts 桥）。

**具体改法（新写一个 `KaliRestClient`，接口对齐 `SSHClient`）**：
```
exec(command, timeout=30)   → POST http://<host>:5000/api/command  {command, timeout}
upload_folder(local, remote) → 需要 REST 侧支持 /api/upload，否则把附件 base64 塞进 command 或走 /api/file
write_file(path, content)    → POST /api/command {command: "cat > /tmp/x.py <<'EOF' ... EOF"} 或 REST 文件端点
is_available()               → GET /api/health
```
保留 `ssh_shell.execute` 的**语义**（content 整段命令、超时合并 stdout/stderr、失败冷却短路），只换 transport。要点：
- **冷却期逻辑照搬** `ssh_client.py:32-41`（连败 3 次→30s 冷却），但把“连接失败”改为“REST 5xx/超时”。
- **env_probe 改 REST**：探测脚本 `env_probe.py:43-83` 原样可用（它只是拼 shell 字符串），改由 REST 执行；`format_env_context`（`:253-294`）输出块原样注入 prompt。这个对我们价值高——Kali 装了哪些工具/模块，让 DeepSeek 第一手知道，少走弯路。
- **dynamic_resolver 改 REST**：`_check_remote`（`:223-261`）的 `command -v/which/dpkg -l` 检查逻辑保留，执行层换 REST。但**建议初赛关掉按需动态发现**（它每次要实时探测，3h 里不如 env_probe 一次性探测 + 技能包静态声明）。
- **python.py**：`_execute_in_docker`（`:226-263`）**直接删除**（无 Docker）；本地算法脚本用 `_audit_ast` + `_execute_locally`（`:189-222`，subprocess 模式）即可；攻击性脚本（pwntools/网络）改走 Kali REST 执行——即把 `_execute_remotely`（`:267-277`）的 `self.ssh.write_file/exec` 换成 KaliRestClient。

**为什么**：Kali REST 是无状态 HTTP，天然比 paramiko 更利于并发（我们已有“同题竞速+并行”），且决赛代码审查时“REST 通道 + 命令黑名单”比“裸 SSH 无沙箱”更合规。

## C4. 3 小时限时 —— 全局时间预算怎么改

两个项目默认都按“无限时慢跑”设计：Koshary `max_agent_rounds=8`、每轮 model timeout 300s；LLMCTF `max_solve_steps=100`、`max_solve_cost_usd=5`。3h 必须加**硬时间预算**：

1. **逐题时间预算**：编排器侧给每题 `deadline`（建议：先按分值分配，如 150 分档给 40min、100 分给 30min、50 分给 15min，总池 2.5h 留 0.5h 缓冲），超时即 kill worker。Koshary 的 `process_challenge` 没有时间上限，只有轮数上限——照抄它的 `max_idle_rounds`（`orchestrator.py:865-868`）思路（workspace 连续不变即放弃），把 idle 判定从“轮次”改为“wall-clock 分钟”。
2. **预算熔断前置**：抄 `solve_agent.py:354-363` 的成本熔断，但把 `max_solve_cost_usd` 设为题目级而非全局，DeepSeek 便宜所以主要防“死循环狂刷”。
3. **轮数/步数上限收紧**：LLMCTF `_max_steps=100` 太大；改 `max_solve_steps≈12~15`（对齐 Koshary 的 8 轮，但 LLMCTF 每步可以多工具，故略多）。`_max_consecutive_none=5`（`:158`）→ 建议 3，更快放弃死题。
4. **同题竞速 + 并行已是我们强项**，Koshary 的 `strategy_rank`（`:1046-1057`）排序公式（分值+有附件+静态题优先，扣失败次数）直接抄进我们的调度队列——3h 里“先吃静态送分题、后啃需要实例的题”是正收益。

## C5. 模型 DeepSeek（OpenAI 兼容）+ 预算有限 —— 怎么改

- **Koshary 的 runner 是 CLI 子进程**（`run_model` `:614-622`），不能直接用；改成 OpenAI 兼容 SDK（我们 pi worker 已是 DeepSeek）。但**保留它的“主模型+fallback”双模型思路**（`run_codex.sh:29-63`）：`deepseek-chat`（主，reasoner）→ 出错/超时切 `deepseek-chat` 或更便宜档，防单点 429。
- **LLMCTF 全程 litellm**（`utils/llm_request.py:59-66` 直接传 `api_base`），本身就是 OpenAI 兼容，config.example.json 里甚至默认就是 deepseek（`config.example.json:3-22`）。**这一层基本直接抄**，含重试（`:51-128`，2 次重试/3s 间隔/120s 超时）+ token 统计。
- **省 token 的机制全要**：`output_parser.py`（规则压缩，零 LLM 成本）、`_build_tools_text`（`solve_agent.py:741-769`，紧凑单行工具描述省 ~70%）、`_quick_analysis`（`:1275-1341`，短输出/枚举零 LLM 分析）。这三个对预算最敏感，直接抄。
- **语义缓存慎用**：`semantic_cache` 的 L2 要额外 embedding 调用（又一项预算），且 3h 短窗口内“语义相近误命中旧响应”会污染推理链。建议：初赛**关 L2 只留 L1 精确**（`llm_request.py:40-43` 里 JSON 模式已经是 threshold=1.0 只 L1），或干脆默认关缓存。工具缓存 `tool_cache` 同理缩短 TTL 到 60s。

## C6. 人机交互（人写提示纠偏）—— 最高优先级，直接抄

我们的赛制明确“人写提示纠偏”。LLMCTF 是两者中**唯一**原生支持 human-in-the-loop 的：

- `UserInterface` 抽象（`agent/user_interface.py:13-35`）+ `CLIUserInterface`（`:37-78`），把显示/选择/确认/自由输入解耦——我们已有 Web GUI，只需再写一个 `WebUI(UserInterface)` 把 `display/choose/confirm/prompt_text` 转发到 GUI 即可。
- `manual_approval_step`（`solve_agent.py:674-701`）：每步“批准执行/提供反馈重新思考/终止”三选一，反馈走 `reflection`（`:703-739`）**基于历史续想、不从头开始**（prompt.yaml `:229-237` 明确要求）。
- `confirm_flag_callback`（`:511-518`）：flag 交给人确认再提交/入库，防幻觉 flag 误提交消耗 `max_wrong`。

**Koshary 无任何人机交互**（全 auto），这条不抄它。

**决赛代码审查**的对应：我们的编排器把“人工纠偏注入”落进 `state.json` 的 hints（已有），LLMCTF 的 `memory.add_fact`（`memory.py:44-46`，把策略切换/人提示作为 `_external_facts` 注入记忆且永不压缩丢失）是现成的“提示→记忆”通道，直接抄来承载人工 hints。

## C7. “僵局检测 / 三层记忆 / 解析回退”对 3h 限时赛的价值排序

**价值排序：解析回退 > 僵局检测 > 三层记忆**（理由基于 3h + DeepSeek + 预算）：

1. **解析回退（最高）**。DeepSeek 非 tool-calling 强绑定，长 ReAct 里 JSON 破格式是**必然高频事件**；解析回退决定“LLM 说对了能不能被执行”。三层回退（原生 tool_calls→堆栈法 JSON→XML→general_next）是无条件兜底，几乎不花预算（只有 general_next 是额外一次调用），是**正确性基座**。直接抄 `solve_agent.py:863-1013` + `utils/tools.py:476-559` + `analyzer.py:55-68`。特别抄**堆栈法 JSON 提取** `_extract_json_blocks`（`solve_agent.py:844-861`），它比 `re.search(r'\{.*\}'` 抗嵌套、抗“思考里夹多个 JSON 块”。

2. **僵局检测（次高）**。3h 里最怕的不是解不出，是**死循环烧预算/烧步数**。6 维里对 3h 最值钱的是：D6 错误率（纯规则零成本，`:1449-1457`）、D2 同工具（`:1407-1418`）、D5 步数多无进展（`:1475-1477`）；D1/D3/D4 依赖 analyzer 的 `progress_level` 或相似度，成本略高但价值也高。**改**：`max_stuck_steps` 5→3、`_max_consecutive_none` 5→3、`_min_step_interval`（`:163`，防过速）保留。切换提示 `_build_switch_prompt`（`:1581-1603`）三级递进文案直接抄（尤其第三级“质疑题型分类”对 Jeopardy 很关键）。

3. **三层记忆（第三）**。3h 单场比赛，历史规模远达不到需要 10000 字叙事压缩的程度（每题最多 ~15 步）。**价值在“去重 + 防丢 + 失败尝试计数”而非“压缩”**：
   - 必抄：`_is_duplicate_step`（`:326-344`，防同一步反复写）+ `_normalize_tool_args_key`（`:294-324`）+ `failed_attempts`（`:48-56, 80-81`，让 LLM 知道某命令已试过 N 次）+ `_PROTECTED_PATTERNS` 防丢（`:17-29`）。
   - **改**：`_write_journal_entry` 的每步额外一次 flash 叙事调用（`solve_agent.py:1183-1224`）在 3h 里是纯预算开销，**建议砍掉或降级为 output_summary 复用**——直接把 `memory.add_step` 的 `output_summary`（`_summarize_output` `:1147-1181`，只在 >2KB 才调 LLM）当记忆，不做第二层 journal。`_consolidate_journal` 的 LLM 整合（`:152-204`）对 3h 基本用不上，保留其**降级路径** `_fallback_consolidate` 即可。

## C8. 放弃清单 + 理由

| 机制 | 放弃理由 |
|---|---|
| LLMCTF Docker 沙箱（`python.py:226-263`） | 环境无 Docker；subprocess+AST 审计（`:131-222`）已够本地安全，攻击性代码走 Kali REST |
| LLMCTF RAG/ChromaDB/auto_learn（`rag/`、`workflow.py:560-791`） | 3h 初赛无历史积累，冷启动检索价值≈0，还要额外 embedding + chromadb 依赖 + 每步 RAG 查询预算；**决赛**（若跨多轮同题）可只留 `rag/seed_data` 种子 + 我们 5 个技能包做静态注入，不上 ChromaDB |
| LLMCTF 渗透模式全套（`attack_surface.py`/`_detect_credentials`/CVSS/报告生成） | Jeopardy 是 CTF 夺旗，非渗透，`mode="ctf"` 分支即可；留 `workflow._generate_writeup`（`:793-885`）做决赛审查材料 |
| Koshary HTB fullpwn/VPN/instance 全链路（`instance_manager.py`/`htb_*`） | 无 HTB 实例；但 `BasePlatform.needs_instance` 的 no-op 默认（`base.py:100-107`）保留，未来平台给实例 URL 时直接复用 |
| Koshary `first_blood.py` 的 brute-first 扫 ID | 违规风险（西湖论剑有异常爆破提交治理），且 Jeopardy 无需；只留“sanity/welcome 关键词”启发，去掉 brute |
| LLMCTF `semantic_cache` L2、`tool_cache` 长 TTL | 3h 短窗口缓存污染 > 省下的钱（见 C5） |
| Koshary 每轮 `collect_workspace_text` 全量 40 文件×12k | 3h 里 prompt 过肥烧预算，改成“最近变更文件 + 关键摘要”，只抄它的**排除 plan.md/challenge.json/agent_rounds** 思路（`:353-355`） |

## C9. 落地建议（最短路径）

1. **编排层**（已有）：把 `BasePlatform`+`NormalizedChallenge`+`SubmitResult`（`platforms/base.py`）作为 dasctf_client 的接口规范，抄 `_interpret_submission`（`htb_ctf_mcp.py:498-516`）做提交判定，抄 `_solve_with_candidates` 的去重/限错（`orchestrator.py:950-988`），抄 `strategy_rank` 做 3h 求解队列。
2. **worker 层**（pi worker，已有）：抄 LLMCTF 的 **三层解析回退 + 6 维僵局检测（阈值收紧）+ checkpoint 断点续跑 + 手动/reflection 人机交互 + 成本熔断 + output_parser 规则压缩**；把 `SSHClient` 换成 `KaliRestClient`（接口对齐 `ssh_client.py`），`env_probe` 走 REST，`python.py` 保留 AST 审计 + subprocess 本地 + REST 远程三态。
3. **题型路由**：抄 Koshary 的**静态 config 路由表**（`config.json:68-89` + `choose_route` `:214-219`）做确定性路由，用 LLMCTF 的 `challenge_classifier`（`:229-276`）做“category 字段缺失时”的兜底分类，二者结合，不依赖模型分类。
4. **flag**：抄 `flag_detector.py` 五级模式（把前缀换成西湖论剑实际前缀 + 保留通用兜底 `:15`），抄 Koshary `is_placeholder_flag`（`flag_extractor.py:31-42`）防占位误提交，交 `confirm_flag_callback` 人工确认。

---

*本文所有文件:行号均为对 `D:\ctf-agent\koshary-ref` 与 `D:\ctf-agent\llmctf-ref` 实际源码的精读引用。*
