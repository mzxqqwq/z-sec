/**
 * Kali 远程执行扩展（CTF 打靶用）——SSH 直连版
 *
 * 把 pi 的 read / write / edit / bash 四个内置工具的执行后端替换为
 * Kali Linux 上的 SSH 直连（ssh2 库），使 agent 的文件系统与 shell 全部
 * 落在 Kali 机器上（pwntools / angr / z3 / gdb 等）。
 *
 * 用法：
 *   pi -e kali.ts                      # 凭据读 D:\ctf-agent\secrets\kali.json
 *   pi -e kali.ts --kali /root/ctf     # 指定远程工作目录（可选 flag）
 *
 * 配置：
 *   SSH 凭据在 secrets/kali.json（host/port/username/password/sudo），
 *   环境变量 KALI_HOST / KALI_PORT / KALI_USER / KALI_PASSWORD / KALI_SUDO 可覆盖。
 *   本机 SSH 以 kali 用户登录（root 直连被 sshd 拒绝），命令经
 *   `sudo -S -p ''` 提权到 root（密码从 stdin 写入后立即 EOF，与 REST 版
 *   无 stdin 的语义一致）；若 username=root 或 sudo=false 则直接执行。
 *   ⚠️ 开源前必须删除 secrets/kali.json 并检查本文档不含真实凭据。
 *
 * 实现要点：
 *   - 每 worker 进程懒建一条 ssh2 连接复用；每条命令开独立 channel exec；
 *   - 命令超时（默认 300s）：远程命令外包 `timeout -k 5 <secs>`（超时杀远端
 *     进程树），客户端侧超时/中止时关闭连接，下次命令自动重连；
 *   - 连接断开后自动重连一次；
 *   - 写文件沿用 echo <b64> | base64 -d > path 模式（exec 执行，无需 sftp）。
 *
 * 保留：Kali REST 的 /health 仍由 eval_run/preflight 用作健康闸门，本扩展不再使用 REST。
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import {
	type BashOperations,
	createBashTool,
	createEditTool,
	createReadTool,
	createWriteTool,
	type EditOperations,
	type ReadOperations,
	type WriteOperations,
} from "@earendil-works/pi-coding-agent";

// ---------- ssh2 加载（pi 的 jiti loader 从 pi-mono 解析依赖，找不到扩展目录下的
// node_modules；用 createRequire 锚定到本扩展目录加载，保证解析可靠） ----------
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const extDir: string =
	(typeof __dirname !== "undefined" && __dirname) ||
	(() => { try { return fileURLToPath(new URL(".", import.meta.url)); } catch { return ""; } })() ||
	process.env.KALI_EXT_DIR ||
	"D:/ctf-agent/src/pi-ext";
const req = createRequire(`${extDir.replace(/[\\/]+$/, "")}/kali.js`);
// eslint-disable-next-line @typescript-eslint/no-var-requires
const ssh2mod = req("ssh2") as typeof import("ssh2");
const { Client } = ssh2mod;
// eslint-disable-next-line @typescript-eslint/no-var-requires
const typebox = req("typebox") as typeof import("typebox");
const { Type } = typebox;

// ---------- worker-api（编排器本地回调：提交/取提示，纪律在编排器侧统一） ----------
const WORKER_API_URL = process.env.WORKER_API_URL ?? "http://127.0.0.1:8089";

// ---------- 配置 ----------
interface KaliSshConfig {
	host: string;
	port: number;
	username: string;
	password: string;
	sudo: boolean;
}

const DEFAULT_CONFIG_PATH = "D:/ctf-agent/secrets/kali.json";
const SSH_TIMEOUT_MS = 300_000; // 单条命令最长 5 分钟
const RECONNECT_ONCE = true;

function loadConfig(): KaliSshConfig {
	const envHost = process.env.KALI_HOST;
	if (envHost) {
		return {
			host: envHost,
			port: Number(process.env.KALI_PORT ?? "22"),
			username: process.env.KALI_USER ?? "kali",
			password: process.env.KALI_PASSWORD ?? "",
			sudo: (process.env.KALI_SUDO ?? "1") !== "0" && (process.env.KALI_USER ?? "kali") !== "root",
		};
	}
	const configPath = process.env.KALI_SSH_CONFIG ?? DEFAULT_CONFIG_PATH;
	const fs = req("node:fs") as typeof import("node:fs");
	const raw = fs.readFileSync(configPath, "utf-8");
	const j = JSON.parse(raw) as Partial<KaliSshConfig>;
	if (!j.host) throw new Error(`kali ssh config missing host (${configPath})`);
	return {
		host: j.host,
		port: Number(j.port ?? 22),
		username: j.username ?? "kali",
		password: j.password ?? "",
		sudo: j.sudo ?? j.username !== "root",
	};
}

function configLabel(cfg: KaliSshConfig): string {
	return `ssh://${cfg.username}@${cfg.host}:${cfg.port}`;
}

// ---------- 连接管理 ----------
let client: InstanceType<typeof Client> | null = null;
let connecting: Promise<InstanceType<typeof Client>> | null = null;

function resetClient(): void {
	if (client) {
		try { client.end(); } catch { /* ignore */ }
		client = null;
	}
	connecting = null;
}

function getClient(): Promise<InstanceType<typeof Client>> {
	if (client) return Promise.resolve(client);
	if (connecting) return connecting;
	connecting = new Promise((resolve, reject) => {
		const c = new Client();
		const cfg = loadConfig();
		const onReady = () => { connecting = null; client = c; resolve(c); };
		const onError = (err: Error) => {
			connecting = null;
			try { c.end(); } catch { /* ignore */ }
			reject(new Error(`kali ssh connect ${configLabel(cfg)} failed: ${err.message}`));
		};
		c.once("ready", onReady);
		c.once("error", onError);
		c.on("close", () => {
			if (client === c) client = null;
			if (connecting) { connecting = null; }
		});
		c.connect({
			host: cfg.host,
			port: cfg.port,
			username: cfg.username,
			password: cfg.password,
			readyTimeout: 20_000,
			keepaliveInterval: 30_000,
		});
	});
	return connecting;
}

// ---------- 单条命令执行 ----------
function buildRemoteCommand(cfg: KaliSshConfig, command: string, timeoutMs: number): string {
	const secs = Math.max(1, Math.ceil(timeoutMs / 1000));
	// 命令经 base64 传输：JSON.stringify 会把换行转义成字面 \n，heredoc/多行脚本
	// 在远端被压成一行导致语法错误（2026-08-16 describeme 日志实证）——base64 保真。
	const b64 = Buffer.from(command, "utf-8").toString("base64");
	const inner = `timeout -k 5 ${secs} bash -c ${JSON.stringify(`echo ${b64} | base64 -d | bash`)}`;
	return cfg.sudo ? `sudo -S -p '' ${inner}` : inner;
}

function execSsh(
	command: string,
	timeoutMs: number,
	signal?: AbortSignal,
): Promise<{ stdout: string; stderr: string; code: number }> {
	const once = (): Promise<{ stdout: string; stderr: string; code: number }> =>
		getClient().then(
			(c) =>
				new Promise((resolve, reject) => {
					let stdout = "";
					let stderr = "";
					let settled = false;
					let timer: ReturnType<typeof setTimeout> | null = null;

					const finish = (code: number) => {
						if (settled) return;
						settled = true;
						if (timer) clearTimeout(timer);
						resolve({ stdout, stderr, code });
					};
					const fail = (err: Error) => {
						if (settled) return;
						settled = true;
						if (timer) clearTimeout(timer);
						reject(err);
					};

					const cfg = loadConfig();
					const remote = buildRemoteCommand(cfg, command, timeoutMs);
					c.exec(remote, (err, stream) => {
						if (err) { fail(new Error(`kali ssh exec: ${err.message}`)); return; }
						// sudo 密码经 stdin 写入后立即 EOF：sudo 只消费第一行，
						// 实际命令拿到的 stdin 是 EOF（与 REST 版无 stdin 一致）
						if (cfg.sudo) {
							stream.write(cfg.password + "\n");
							stream.end();
						}
						stream.on("data", (d: Buffer) => { stdout += d.toString(); });
						stream.stderr.on("data", (d: Buffer) => { stderr += d.toString(); });
						stream.on("close", (code: number | null) => {
							finish(code ?? 0);
						});
						stream.on("error", (e: Error) => fail(e));
					});

					const abort = () => {
						// 超时/中止：关闭连接（远端由 timeout 包负责杀进程树），下次命令自动重连
						resetClient();
						fail(signal?.aborted
							? new Error("kali ssh aborted")
							: new Error(`kali ssh timeout after ${Math.round(timeoutMs / 1000)}s`));
					};
					timer = setTimeout(abort, timeoutMs);
					if (signal) {
						if (signal.aborted) { abort(); return; }
						signal.addEventListener("abort", abort, { once: true });
					}
				}),
		);

	// 断线自动重连一次
	if (!RECONNECT_ONCE) return once();
	return once().catch((e: Error) => {
		resetClient();
		return once();
	});
}

async function kaliExec(
	command: string,
	signal?: AbortSignal,
	timeoutSeconds?: number,
): Promise<{ stdout: string; stderr: string; code: number }> {
	const timeoutMs = Math.min((timeoutSeconds ?? 300) * 1000, SSH_TIMEOUT_MS);
	const { stdout, stderr, code } = await execSsh(command, timeoutMs, signal);
	if (stdout === "" && stderr !== "") throw new Error(`kali: ${stderr.slice(0, 2000)}`);
	return { stdout, stderr, code };
}

// ---------- 扩展主体（对外 API 与 REST 版完全一致） ----------
export default function (pi: ExtensionAPI) {
	pi.registerFlag("kali", { description: "Kali 远程工作目录（默认 /root/ctf）", type: "string" });
	pi.registerFlag("cid", { description: "当前挑战 id（提交/取提示用）", type: "string" });

	// ---------- 平台交互工具（T8：经编排器 worker-api 统一提交，纪律中心化） ----------
	async function workerApiPost(path: string, body: Record<string, unknown>): Promise<string> {
		let lastErr = "";
		for (let attempt = 0; attempt < 2; attempt++) {
			try {
				const resp = await fetch(`${WORKER_API_URL}${path}`, {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify(body),
				});
				const text = await resp.text();
				if (!resp.ok) return `worker-api http ${resp.status}: ${text.slice(0, 300)}`;
				return text;
			} catch (e) {
				const cause = (e as { cause?: { message?: string; code?: string } }).cause;
				lastErr = `${e instanceof Error ? e.message : String(e)}${cause ? ` (${cause.code ?? ""} ${cause.message ?? ""})` : ""}`;
				await new Promise((r) => setTimeout(r, 500));
			}
		}
		return `worker-api unreachable (${lastErr})；可改用 "FLAG: <flag>" 文本输出由编排器代为提交`;
	}

	pi.registerTool({
		name: "submit_flag",
		label: "Submit flag",
		description:
			"向比赛平台提交 flag 并获取平台判定（correct/incorrect）。提交前确保 flag 完整（如 DASCTF{...}）。返回 correct 即本题完成。",
		promptSnippet: "submit_flag(flag)",
		parameters: Type.Object({ flag: Type.String() }),
		execute: async (_id, params, _signal) => {
			const cid = (pi.getFlag("cid") as string | undefined) ?? "";
			const result = await workerApiPost("/worker-submit", {
				cid,
				flag: String(params.flag ?? "").trim(),
			});
			return { content: [{ type: "text", text: result }], details: undefined };
		},
	});

	pi.registerTool({
		name: "get_hint",
		label: "Get hint",
		description: "获取本题的官方提示（如平台提供）。卡住时可用。",
		promptSnippet: "get_hint()",
		parameters: Type.Object({}),
		execute: async () => {
			const cid = (pi.getFlag("cid") as string | undefined) ?? "";
			const result = await workerApiPost("/worker-hint", { cid });
			return { content: [{ type: "text", text: result }], details: undefined };
		},
	});

	// ---------- message bus（T10：同题其他 worker 的发现，排除自己、已读不回传） ----------
	let busCursor = 0;
	pi.registerTool({
		name: "check_findings",
		label: "Check findings",
		description:
			"查看同题其他 worker 的最新发现摘要（排除自己，已读不回传）。建议每几步调用一次；切换路线前、怀疑自己忘了别人的结论时也调用。",
		promptSnippet: "check_findings()",
		parameters: Type.Object({}),
		execute: async () => {
			const busFile = process.env.MESSAGE_BUS_FILE;
			const tag = process.env.WORKER_TAG ?? "worker";
			if (!busFile) {
				return { content: [{ type: "text", text: "(message bus 未配置)" }], details: undefined };
			}
			const fs = req("node:fs") as typeof import("node:fs");
			try {
				const data = JSON.parse(fs.readFileSync(busFile, "utf-8")) as {
					findings?: Array<{ model?: string; content?: string }>;
				};
				const findings = Array.isArray(data.findings) ? data.findings : [];
				const unread = findings
					.slice(busCursor)
					.filter((f) => (f?.model ?? "") !== tag);
				busCursor = findings.length;
				if (unread.length === 0) {
					return { content: [{ type: "text", text: "(没有来自其他 worker 的新发现)" }], details: undefined };
				}
				const parts = unread.map((f) => `[${f?.model ?? "?"}] ${f?.content ?? ""}`);
				return {
					content: [{ type: "text", text: "**Findings from other agents:**\n\n" + parts.join("\n\n") }],
					details: undefined,
				};
			} catch (e) {
				return {
					content: [{ type: "text", text: `check_findings 失败: ${e instanceof Error ? e.message : String(e)}` }],
					details: undefined,
				};
			}
		},
	});

	// ---------- 本地知识库检索（T13：KB 服务 :8099；评测模式默认关闭） ----------
	const KB_URL = process.env.KB_URL ?? "http://127.0.0.1:8099";
	pi.registerTool({
		name: "kb_search",
		label: "Search local KB",
		description:
			"检索本地 CTF 解题知识库（历年真题与 writeup 提炼的题型→手法参考）。按题型或关键词查询，如 'RSA 小指数'、'PNG 隐写'、'栈溢出'。",
		promptSnippet: "kb_search(query)",
		parameters: Type.Object({ query: Type.String() }),
		execute: async (_id, params) => {
			if ((process.env.KB_ENABLED ?? "0") !== "1") {
				return { content: [{ type: "text", text: "(本地知识库未启用)" }], details: undefined };
			}
			const q = encodeURIComponent(String(params.query ?? "").trim());
			if (!q) {
				return { content: [{ type: "text", text: "kb_search: 查询不能为空" }], details: undefined };
			}
			try {
				const resp = await fetch(`${KB_URL}/search?q=${q}`, { method: "GET" });
				const data = (await resp.json()) as {
					results?: Array<{ name?: string; category?: string; desc?: string; hint?: string }>;
				};
				const results = Array.isArray(data.results) ? data.results : [];
				if (results.length === 0) {
					return { content: [{ type: "text", text: "知识库没有匹配结果，换关键词试试" }], details: undefined };
				}
				const parts = results.map(
					(r) => `[${r.category ?? "?"}] ${r.name ?? ""}\n  ${r.desc ?? ""}${r.hint ? `\n  手法参考: ${r.hint}` : ""}`,
				);
				return { content: [{ type: "text", text: "**KB 检索结果:**\n\n" + parts.join("\n\n") }], details: undefined };
			} catch (e) {
				return {
					content: [{ type: "text", text: `kb_search 失败: ${e instanceof Error ? e.message : String(e)}` }],
					details: undefined,
				};
			}
		},
	});

	const localCwd = process.cwd();
	const localRead = createReadTool(localCwd);
	const localWrite = createWriteTool(localCwd);
	const localEdit = createEditTool(localCwd);
	const localBash = createBashTool(localCwd);

	let remoteCwd = "/root/ctf";
	// 路径归一：pi 的工具层先按 Windows 解析参数（/root/... → D:\root\...），
	// 而 bash 的 pwd 是 Kali 路径——两套坐标系在这里归一：
	//   ① localCwd（Windows 工作目录）→ remoteCwd
	//   ② D:\root\... 等被误解析的 Linux 绝对路径 → 还原为 /root/...
	//   ③ 已是 / 开头的 Linux 绝对路径 → 原样透传
	//   ④ 其余 Windows 绝对路径 → 当作远程 cwd 下的同名文件
	const toRemote = (p: string) => {
		if (p === localCwd) return remoteCwd;
		if (p.startsWith(localCwd)) return p.replace(localCwd, remoteCwd);
		const m = p.match(/^[A-Za-z]:\\(root|home|tmp|etc|opt|srv|var|usr)(\\|$)/);
		if (m) return "/" + p.slice(3).replace(/\\/g, "/");
		if (p.startsWith("/")) return p;
		const name = p.split(/[\\/]/).pop() ?? p;
		return `${remoteCwd}/${name}`;
	};
	const q = (s: string) => JSON.stringify(s); // shell-safe 引号

	const readOps: ReadOperations = {
		readFile: async (p, signal) => (await kaliExec(`cat ${q(toRemote(p))}`, signal)).stdout,
		access: async (p) => {
			await kaliExec(`test -r ${q(toRemote(p))}`);
		},
		detectImageMimeType: async (p) => {
			try {
				const m = (await kaliExec(`file --mime-type -b ${q(toRemote(p))}`)).stdout.trim();
				return ["image/jpeg", "image/png", "image/gif", "image/webp"].includes(m) ? m : null;
			} catch {
				return null;
			}
		},
	};

	const writeOps: WriteOperations = {
		writeFile: async (p, content, signal) => {
			const b64 = Buffer.from(content).toString("base64");
			await kaliExec(`echo ${q(b64)} | base64 -d > ${q(toRemote(p))}`, signal);
		},
		mkdir: async (dir, signal) => {
			await kaliExec(`mkdir -p ${q(toRemote(dir))}`, signal);
		},
	};

	const editOps: EditOperations = {
		readFile: readOps.readFile,
		access: readOps.access,
		writeFile: writeOps.writeFile,
	};

	// ---------- benchmark 网络封锁（2026-08-17） ----------
	// benchmark 题公开可搜，联网=开卷抄解；比赛题搜不到。编排器在 benchmark 模式下
	// 给 worker 注入 NET_POLICY=local-only，bash 工具在执行前拦截一切外联动作：
	// curl/wget/git/pip/npm/apt/go/cargo/podman/docker/ssh/dns 全拒；nc/ncat/socat
	// 只允许 127.0.0.1/localhost（本地复活的靶机照常可连）。read/write/edit 不涉及
	// 网络，不受影响。
	const NET_BLOCK_RE = /\b(curl|wget|git\s+(clone|ls-remote|fetch|pull)|pip(3)?\s+(install|download)|npm(\s|$)|npx|ssh\b|scp\b|telnet\b|aria2c\b|nslookup\b|dig\b|getent\s+hosts|apt(-get)?\b|apk\s+add|brew\b|cargo\b|gem\s+install|go\s+(get|install|mod)|cpan\b|podman\b|docker\b|ctr\b|nerdctl\b|containerd\b)\b/i;
	const PY_NET_RE = /python3?\b.*\b(urllib|requests|socket|http\.client)\b/i;
	const LOCAL_NET_RE = /\b(nc|ncat|netcat|socat)\b/i;

	function netBlocked(command: string): string | null {
		if (process.env.NET_POLICY !== "local-only") return null;
		if (PY_NET_RE.test(command) && !/\b127\.0\.0\.1\b|\blocalhost\b/.test(command)) {
			return "（benchmark 网络封锁）禁止用 python urllib/requests/socket 访问外网";
		}
		if (NET_BLOCK_RE.test(command)) {
			return "（benchmark 网络封锁）禁止 curl/wget/git/pip/npm/ssh/DNS 等外联——题目材料与本地靶机(127.0.0.1)足够解题";
		}
		if (LOCAL_NET_RE.test(command) && !/\b127\.0\.0\.1\b|\blocalhost\b/.test(command)) {
			return "（benchmark 网络封锁）nc/ncat/socat 只允许连接 127.0.0.1/localhost 的本地靶机";
		}
		return null;
	}

	const bashOps: BashOperations = {
		exec: async (command, cwd, { onData, signal, timeout }) => {
			const blocked = netBlocked(command);
			if (blocked) {
				onData(Buffer.from(blocked + "\n"));
				return { exitCode: 1 };
			}
			const timeoutSeconds = timeout ?? 300;
			const controller = new AbortController();
			const timer = setTimeout(() => controller.abort(), Math.min(timeoutSeconds * 1000, SSH_TIMEOUT_MS));
			const onAbort = () => controller.abort();
			signal?.addEventListener("abort", onAbort, { once: true });
			try {
				const cmd = `mkdir -p ${q(toRemote(cwd))} 2>/dev/null; cd ${q(toRemote(cwd))} && (${command}) 2>&1`;
				const { stdout, code } = await kaliExec(cmd, controller.signal, timeoutSeconds);
				if (stdout) onData(Buffer.from(stdout));
				return { exitCode: code };
			} catch (e) {
				const msg = e instanceof Error ? e.message : String(e);
				if (controller.signal.aborted && !signal?.aborted) throw new Error(`timeout:${timeoutSeconds}`);
				throw new Error(msg);
			} finally {
				clearTimeout(timer);
				signal?.removeEventListener("abort", onAbort);
			}
		},
	};

	pi.registerTool({ ...localRead, execute: async (id, p, s, u) => createReadTool(localCwd, { operations: readOps }).execute(id, p, s, u) });
	pi.registerTool({ ...localWrite, execute: async (id, p, s, u) => createWriteTool(localCwd, { operations: writeOps }).execute(id, p, s, u) });
	pi.registerTool({ ...localEdit, execute: async (id, p, s, u) => createEditTool(localCwd, { operations: editOps }).execute(id, p, s, u) });
	pi.registerTool({ ...localBash, execute: async (id, p, s, u) => createBashTool(localCwd, { operations: bashOps }).execute(id, p, s, u) });

	pi.on("session_start", () => {
		const arg = pi.getFlag("kali") as string | undefined;
		if (arg) remoteCwd = arg;
	});

	pi.on("before_agent_start", (event) => {
		return {
			systemPrompt: event.systemPrompt.replace(
				`Current working directory: ${localCwd}`,
				`Current working directory: ${remoteCwd} (remote Kali via SSH ${configLabel(loadConfig())}, tools: pwntools/angr/z3/gdb/nmap/...)`,
			),
		};
	});
}
