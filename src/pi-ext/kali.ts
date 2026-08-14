/**
 * Kali 远程执行扩展（CTF 打靶用）
 *
 * 把 pi 的 read / write / edit / bash 四个内置工具的执行后端替换为
 * Kali Tools API（POST http://<host>:5000/api/command），使 agent 的
 * 文件系统与 shell 全部落在 Kali 机器上（pwntools / angr / z3 / gdb 等）。
 *
 * 用法：
 *   pi -e kali.ts                      # KALI_API_URL 环境变量可覆盖地址
 *   pi -e kali.ts --kali /root/ctf     # 指定远程工作目录（可选 flag）
 *
 * 限制：Kali API 是一次性返回完整结果的 REST 接口，不支持流式输出和
 * 交互式 pty（gdb/nc 交互调试需后续换 SSH pty 通道）。
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

const DEFAULT_API = "http://10.174.153.128:5000";
const API_TIMEOUT_MS = 300_000; // Kali API 单次最长 5 分钟

function apiUrl(): string {
	return (process.env.KALI_API_URL ?? DEFAULT_API).replace(/\/+$/, "");
}

async function kaliExec(command: string, signal?: AbortSignal): Promise<{ stdout: string; stderr: string; code: number }> {
	const resp = await fetch(`${apiUrl()}/api/command`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ command }),
		signal,
	});
	if (!resp.ok) throw new Error(`kali api http ${resp.status}`);
	const data = (await resp.json()) as {
		stdout?: string;
		stderr?: string;
		return_code?: number;
		timed_out?: boolean;
	};
	if (data.timed_out) throw new Error("kali command timed out");
	const stdout = data.stdout ?? "";
	const stderr = data.stderr ?? "";
	if (stdout === "" && stderr !== "") throw new Error(`kali: ${stderr.slice(0, 2000)}`);
	return { stdout, stderr, code: data.return_code ?? 0 };
}

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
			const controller = new AbortController();
			const timer = setTimeout(() => controller.abort(), Math.min((timeout ?? 300) * 1000, API_TIMEOUT_MS));
			const onAbort = () => controller.abort();
			signal?.addEventListener("abort", onAbort, { once: true });
			try {
				const cmd = `mkdir -p ${q(toRemote(cwd))} 2>/dev/null; cd ${q(toRemote(cwd))} && (${command}) 2>&1`;
				const { stdout, code } = await kaliExec(cmd, controller.signal);
				if (stdout) onData(Buffer.from(stdout));
				return { exitCode: code };
			} catch (e) {
				const msg = e instanceof Error ? e.message : String(e);
				if (controller.signal.aborted && !signal?.aborted) throw new Error(`timeout:${timeout}`);
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
				`Current working directory: ${remoteCwd} (remote Kali via ${apiUrl()}, tools: pwntools/angr/z3/gdb/nmap/...)`,
			),
		};
	});
}
