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
	// 远程超时杀进程树（timeout -k 5）；sudo 提权（-S 从 stdin 读密码、-p '' 静默提示）
	const inner = `timeout -k 5 ${secs} bash -c ${JSON.stringify(command)}`;
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

	const localCwd = process.cwd();
	const localRead = createReadTool(localCwd);
	const localWrite = createWriteTool(localCwd);
	const localEdit = createEditTool(localCwd);
	const localBash = createBashTool(localCwd);

	let remoteCwd = "/root/ctf";
	const toRemote = (p: string) => (p === localCwd ? remoteCwd : p.replace(localCwd, remoteCwd));
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

	const bashOps: BashOperations = {
		exec: async (command, cwd, { onData, signal, timeout }) => {
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
