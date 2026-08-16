/**
 * Observer 看板工具扩展（BreachWeave observer sidecar 对齐版，2026-08-16）
 *
 * 为什么这么改：此前编排器裸调 /chat/completions + response_format=json_object，
 * 让模型整段输出 JSON；v4-pro 是推理模型，max_tokens 被 reasoning 吃光后 content
 * 为空 → json.loads("") 必炸（复现实测：reasoning_tokens=1200=全额，content=''）。
 *
 * BreachWeave 原版（packages/core/src/solver/extension/challenge-observer/）不做
 * JSON 解析：Observer 是独立 pi Agent 会话，结构化动作全部通过工具落地
 * （memory_add/idea_update 等），模型最终回复只是文本摘要——推理被截断也不影响
 * 动作落地。本扩展即该架构的移植：工具直接读写看板 JSON 文件，编排器只读结果。
 *
 * 用法：
 *   pi -e observer.ts --observer-board <board.json 绝对路径> --cid <cid>
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const extDir: string =
	(typeof __dirname !== "undefined" && __dirname) ||
	(() => { try { return fileURLToPath(new URL(".", import.meta.url)); } catch { return ""; } })() ||
	"D:/ctf-agent/src/pi-ext";
const req = createRequire(`${extDir.replace(/[\\/]+$/, "")}/observer.js`);
// eslint-disable-next-line @typescript-eslint/no-var-requires
const typebox = req("typebox") as typeof import("typebox");
const { Type } = typebox;
// eslint-disable-next-line @typescript-eslint/no-var-requires
const fs = req("node:fs") as typeof import("node:fs");
// eslint-disable-next-line @typescript-eslint/no-var-requires
const pathmod = req("node:path") as typeof import("node:path");

let boardPath = "";

const VALID_STATUS = ["pending", "testing", "verified", "failed", "skipped"];
const VALID_KIND = ["fact", "evidence", "failure", "hint"];
const MAX_IDEAS = 8;
const MAX_MEMORY = 12;

interface Idea { content: string; status: string; result: string; updated_at: string }
interface Memo { kind: string; content: string; updated_at: string }
interface Board { ideas: Idea[]; memory: Memo[]; reminder: string | null }

const norm = (s: string) => String(s ?? "").trim().toLowerCase();
const now = () => new Date().toISOString();
const clip = (s: string, n: number) => String(s ?? "").slice(0, n);

function readBoard(): Board {
	try {
		const raw = fs.readFileSync(boardPath, "utf-8");
		const j = JSON.parse(raw);
		return {
			ideas: Array.isArray(j.ideas) ? j.ideas : [],
			memory: Array.isArray(j.memory) ? j.memory : [],
			reminder: typeof j.reminder === "string" ? j.reminder : null,
		};
	} catch {
		return { ideas: [], memory: [], reminder: null };
	}
}

function saveBoard(b: Board): Board {
	const ideas: Idea[] = (b.ideas ?? []).slice(0, MAX_IDEAS).map((i) => ({
		content: clip(i.content, 120),
		status: VALID_STATUS.includes(i.status) ? i.status : "pending",
		result: clip(i.result ?? "", 200),
		updated_at: i.updated_at ?? now(),
	}));
	const memory: Memo[] = (b.memory ?? []).slice(0, MAX_MEMORY).map((m) => ({
		kind: VALID_KIND.includes(m.kind) ? m.kind : "fact",
		content: clip(m.content, 220),
		updated_at: m.updated_at ?? now(),
	}));
	const out: Board = {
		ideas,
		memory,
		reminder: typeof b.reminder === "string" ? clip(b.reminder, 300) : null,
	};
	try {
		fs.mkdirSync(pathmod.dirname(boardPath), { recursive: true });
		fs.writeFileSync(boardPath, JSON.stringify(out, null, 1), "utf-8");
	} catch {
		/* 落盘失败也返回内存态，让编排器至少拿到本次结果 */
	}
	return out;
}

function boardText(b: Board): string {
	const lines: string[] = [];
	lines.push(`ideas (${b.ideas.length}/${MAX_IDEAS}):`);
	for (const i of b.ideas) lines.push(`  [${i.status}] ${i.content}${i.result ? ` | 证据: ${i.result}` : ""}`);
	lines.push(`memory (${b.memory.length}/${MAX_MEMORY}):`);
	for (const m of b.memory) lines.push(`  [${m.kind}] ${m.content}`);
	return lines.join("\n");
}

function ok(b: Board, msg: string) {
	return { content: [{ type: "text", text: `${msg}\n\n当前看板:\n${boardText(b)}` }], details: undefined };
}

function registerTools(pi: ExtensionAPI) {
	pi.registerTool({
		name: "board_list",
		label: "List board",
		description: "查看当前策略看板全貌（ideas + memory）。每次修改前先调用，避免重复/近义记录。",
		promptSnippet: "board_list()",
		parameters: Type.Object({}),
		execute: async () => ok(readBoard(), "看板如下："),
	});

	pi.registerTool({
		name: "idea_add",
		label: "Add idea",
		description:
			"新增一条方向假设。idea 只表示「接下来值得测试什么」，必须具体、可执行、可验证；" +
			"不要拆出近义/同级/上下级重复 idea；只有新证据真正打开不同攻击方向时才新增。" +
			"status: pending/testing/verified/failed/skipped（默认 pending）；" +
			"verified/failed 时 result 必须给决定性证据摘要。上限 " + MAX_IDEAS + " 条，超限先 update/合并。",
		promptSnippet: "idea_add(content, status, result)",
		parameters: Type.Object({
			content: Type.String(),
			status: Type.Optional(Type.String()),
			result: Type.Optional(Type.String()),
		}),
		execute: async (_id, params) => {
			const b = readBoard();
			const content = clip(String(params.content ?? "").trim(), 120);
			if (!content) return ok(b, "idea_add 失败：content 不能为空");
			if (b.ideas.some((i) => norm(i.content) === norm(content)))
				return ok(b, `idea_add 跳过：已存在近义 idea「${content}」`);
			if (b.ideas.length >= MAX_IDEAS)
				return ok(b, `idea_add 失败：ideas 已达上限 ${MAX_IDEAS}，请先 idea_update 合并或删除`);
			b.ideas.push({
				content,
				status: VALID_STATUS.includes(String(params.status ?? "pending")) ? String(params.status) : "pending",
				result: clip(String(params.result ?? ""), 200),
				updated_at: now(),
			});
			return ok(saveBoard(b), `idea_add 完成：「${content}」`);
		},
	});

	pi.registerTool({
		name: "idea_update",
		label: "Update idea",
		description:
			"更新已有 idea 的内容/状态/证据。old_content 定位目标（写全名或唯一前缀均可；留空则更新第一条）。" +
			"推进生命周期：pending/testing/verified/failed/skipped；判 failed 前先自问失败是否只否定某个" +
			"payload/编码/子分支，拿不准就保持 testing 或退到更窄的 pending。",
		promptSnippet: "idea_update(old_content, content, status, result)",
		parameters: Type.Object({
			old_content: Type.String(),
			content: Type.Optional(Type.String()),
			status: Type.Optional(Type.String()),
			result: Type.Optional(Type.String()),
		}),
		execute: async (_id, params) => {
			const b = readBoard();
			const old = norm(String(params.old_content ?? ""));
			let idx = -1;
			if (old) {
				idx = b.ideas.findIndex((i) => norm(i.content).startsWith(old) || norm(i.content) === old);
			} else if (b.ideas.length > 0) {
				idx = 0;
			}
			if (idx < 0) return ok(b, `idea_update 失败：找不到匹配「${String(params.old_content ?? "")}」的 idea`);
			const target = b.ideas[idx];
			if (params.content !== undefined && String(params.content).trim())
				target.content = clip(String(params.content).trim(), 120);
			if (params.status !== undefined && VALID_STATUS.includes(String(params.status)))
				target.status = String(params.status);
			if (params.result !== undefined && String(params.result).trim())
				target.result = clip(String(params.result).trim(), 200);
			target.updated_at = now();
			return ok(saveBoard(b), `idea_update 完成：现在 [${target.status}] ${target.content}`);
		},
	});

	pi.registerTool({
		name: "memory_add",
		label: "Add memory",
		description:
			"新增一条 durable fact。kind: fact(事实)/evidence(证据)/failure(失败边界)/hint(提示)。" +
			"合并重于累加：同主题先 memory_update；failure 写成边界结论而非动作流水；" +
			"环境限制/隐含约束（无外网、缺依赖、沙箱）是高优先级。上限 " + MAX_MEMORY + " 条，超限先合并。",
		promptSnippet: "memory_add(kind, content)",
		parameters: Type.Object({
			kind: Type.String(),
			content: Type.String(),
		}),
		execute: async (_id, params) => {
			const b = readBoard();
			const content = clip(String(params.content ?? "").trim(), 220);
			const kind = VALID_KIND.includes(String(params.kind)) ? String(params.kind) : "fact";
			if (!content) return ok(b, "memory_add 失败：content 不能为空");
			if (b.memory.some((m) => norm(m.content) === norm(content)))
				return ok(b, `memory_add 跳过：已存在近义记录「${content}」`);
			if (b.memory.length >= MAX_MEMORY)
				return ok(b, `memory_add 失败：memory 已达上限 ${MAX_MEMORY}，请先 memory_update 合并`);
			b.memory.push({ kind, content, updated_at: now() });
			return ok(saveBoard(b), `memory_add 完成：[${kind}] ${content}`);
		},
	});

	pi.registerTool({
		name: "memory_update",
		label: "Update memory",
		description:
			"更新/合并已有 memory。old_content 定位目标（写全名或唯一前缀均可；留空且只给 kind 时更新该 kind 第一条）。" +
			"新结论覆盖旧结论时用它改写，不允许两条近义记录长期并存。",
		promptSnippet: "memory_update(old_content, content, kind)",
		parameters: Type.Object({
			old_content: Type.String(),
			content: Type.Optional(Type.String()),
			kind: Type.Optional(Type.String()),
		}),
		execute: async (_id, params) => {
			const b = readBoard();
			const old = norm(String(params.old_content ?? ""));
			let idx = -1;
			if (old) {
				idx = b.memory.findIndex((m) => norm(m.content).startsWith(old) || norm(m.content) === old);
			} else if (b.memory.length > 0) {
				const k = String(params.kind ?? "");
				idx = k ? b.memory.findIndex((m) => m.kind === k) : 0;
			}
			if (idx < 0) return ok(b, `memory_update 失败：找不到匹配「${String(params.old_content ?? "")}」的 memory`);
			const target = b.memory[idx];
			if (params.content !== undefined && String(params.content).trim())
				target.content = clip(String(params.content).trim(), 220);
			if (params.kind !== undefined && VALID_KIND.includes(String(params.kind)))
				target.kind = String(params.kind);
			target.updated_at = now();
			return ok(saveBoard(b), `memory_update 完成：[${target.kind}] ${target.content}`);
		},
	});

	pi.registerTool({
		name: "memory_delete",
		label: "Delete memory",
		description: "删除一条已被更强结论覆盖/过时/弱记录的 memory。content 定位（全名或唯一前缀）。",
		promptSnippet: "memory_delete(content)",
		parameters: Type.Object({ content: Type.String() }),
		execute: async (_id, params) => {
			const b = readBoard();
			const target = norm(String(params.content ?? ""));
			const idx = b.memory.findIndex((m) => norm(m.content).startsWith(target) || norm(m.content) === target);
			if (idx < 0) return ok(b, `memory_delete 失败：找不到「${String(params.content ?? "")}」`);
			const removed = b.memory[idx];
			b.memory.splice(idx, 1);
			return ok(saveBoard(b), `memory_delete 完成：已删除 [${removed.kind}] ${removed.content}`);
		},
	});

	pi.registerTool({
		name: "send_efficiency_reminder",
		label: "Send efficiency reminder",
		description:
			"最后手段：向 solver 发一条纠偏提醒。四个前提必须同时满足才用：①当前方法明显低效/重复/" +
			"低信息增量；②持续出现而非短暂停留；③solver 不在合理主线推进阶段；④之前提醒过但没改线" +
			"或又回到同一低效模式。提醒内容短、具体、可执行（当前低效行为 + 更高效替代方向）。" +
			"仅当 solver 持续陷入已被更强证据排除的旧路径时才用。",
		promptSnippet: "send_efficiency_reminder(message)",
		parameters: Type.Object({ message: Type.String() }),
		execute: async (_id, params) => {
			const b = readBoard();
			const msg = clip(String(params.message ?? "").trim(), 300);
			if (!msg) return ok(b, "send_efficiency_reminder 失败：message 不能为空");
			b.reminder = msg;
			return ok(saveBoard(b), `提醒已记录：${msg}`);
		},
	});
}

// BreachWeave OBSERVER_SYSTEM_PROMPT 的移植（observer-agent.ts:11-179 语义，2026-08-16 再精简）
const OBSERVER_SYSTEM_PROMPT = `你是 CTF 解题 Agent 的 observer sidecar（旁路监督者）。

你不是 solver：不推进解题、不执行解题工具、不获取 hint、不提交 flag。
你唯一的职责是维护当前题目的策略看板（ideas 与 memory），使其紧凑、准确、低噪音。
你的全部动作都通过工具完成（board_list / idea_add / idea_update / memory_add /
memory_update / memory_delete / send_efficiency_reminder），不要输出 JSON。

默认立场（按序执行，这是立场不是建议）：
NO_CHANGE > update existing > delete superseded > add new

核心循环（每轮审查只按这个顺序，不要跳步）：
1. 先 board_list 看当前 ideas 和 memory。
2. 先闭环已有主线：最近几轮结果是否证实、证伪或推进了某条已有 idea？能闭环就 idea_update 它的 status/result。
3. 某个 payload/编码/子分支/利用姿势失败 → 先 memory_add(kind=failure) 记边界，不要判死整条主线。
4. 只有新结果无法承接到现有主线、且确实打开不同攻击方向时，才 idea_add。
5. 既没有新方向、也没有更强的边界结论，就保持 NO_CHANGE。

Ideas（方向假设）：
- idea 只表示"接下来值得测试什么"；必须具体、可执行、可验证；不要近义/同级/上下级重复。
- status 生命周期：pending / testing / verified / failed / skipped。
- 判 failed 前连续自问：①这次失败否定的是整条路线还是某个 payload/编码/子分支？
  ②这条路线是否仍有合理变体或未验证前提？③更适合把失败边界写进 memory 而不是关闭主线？
  任一存疑就不要判 failed，保持 testing 或退回更窄的 pending。
- verified/failed 时 result 必须包含决定性证据摘要。

Memory（durable facts）：
- kind：fact(事实) / evidence(证据) / failure(失败边界) / hint(提示)。
- 合并重于累加：同主题先 memory_update，不要新增近义记录。
- failure 写成边界结论，不是动作流水（例："对 /login 的 union/time/error SQLi 均失败，疑似参数化"）。
- 环境限制或隐含约束是高优先级 memory（无外网、只读文件系统、缺依赖、沙箱限制）。
- 弱记录/重复/过时/被更强结论覆盖的应 update 或 delete。

体积硬上限（这些记录会进 solver 上下文）：
- memory ≤ 12 条（每条 ≤220 字）、ideas ≤ 8 条（每条 ≤120 字）。
- 超限时压缩本身是优先动作：先 merge/update/delete，再考虑 add。

效率提醒（send_efficiency_reminder 是最后手段）：
四个前提必须同时满足：①当前方法明显低效、重复、低信息增量；②这种状态持续出现；
③solver 不在合理主线的正常推进阶段；④之前提醒过但没改线或又回到同一低效模式。
提醒内容短、具体、可执行：当前低效行为 + 更高效的替代方向。
如果 solver 已切到新方向，即使不完美也不要再打断。

硬约束：
- 主 Agent 对 ideas 是只读的；看板只由你维护。
- 不要为了"看起来有动作"而新增/改写/删除。
- 不要做颠覆性重写，不要一次性大范围改动看板。
- 不要仅因最近几轮没提到某条记录就删除它。
- 没有明确证据不要随意回退已有 idea 状态。
- 看板文字像代码注释一样精炼，保留假设、边界和证据。

输出契约（回复本身）：
- 无需修改时只回复 NO_CHANGE。
- 有修改时只输出 1-4 条短 bullet，说明你维护了什么。
- 不输出 JSON、不复述题面/日志。`;

export default function (pi: ExtensionAPI): void {
	pi.registerFlag("observer-board", { description: "看板 JSON 文件绝对路径（观察者工具读写它）", type: "string" });
	pi.registerFlag("cid", { description: "当前挑战 id", type: "string" });

	pi.on("session_start", () => {
		boardPath = String(pi.getFlag("observer-board") ?? "");
	});

	pi.on("before_agent_start", (event) => {
		return { systemPrompt: OBSERVER_SYSTEM_PROMPT };
	});

	registerTools(pi);
}
