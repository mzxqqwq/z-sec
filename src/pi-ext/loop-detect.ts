/**
 * loop-detect.ts —— LoopDetector 软警告扩展（verialabs loop_detect.py 移植）
 *
 * 签名 = 工具名 + 参数（sort_keys JSON 截 500 字符）；滑动窗口 12；
 * 窗口内同一签名 >=3 次 → 注入一次 steer 警告（同签名去重，防刷屏）；
 * >=5 次 → 阻止本次工具执行（tool_call 事件返回 block + reason）。
 * 只软性打断，从不终止 agent（与定版"不杀 worker"一致）。
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const WINDOW = 12;
const WARN_THRESHOLD = 3;
const BREAK_THRESHOLD = 5;

const LOOP_WARNING =
	"你似乎陷入了重复循环：同一命令/动作已重复多次且没有产生新信息。" +
	"停止重复，换一个完全不同的思路：检查遗漏的线索（附件、端口、参数），或换一种工具/方法。";

export default function (pi: ExtensionAPI) {
	const recent: string[] = [];
	let lastWarnedSig = "";
	let lastWarnedAt = 0;

	function signature(toolName: string, input: unknown): string {
		let raw = "";
		try {
			raw = JSON.stringify(input ?? {}, Object.keys((input as object) ?? {}).sort());
		} catch {
			raw = String(input);
		}
		return `${toolName}:${raw.slice(0, 500)}`;
	}

	pi.on("tool_call", (event) => {
		const input = (event as { input?: unknown }).input;
		const sig = signature(event.toolName, input);
		recent.push(sig);
		if (recent.length > WINDOW) recent.shift();
		const count = recent.filter((s) => s === sig).length;

		if (count >= BREAK_THRESHOLD) {
			return { block: true, reason: LOOP_WARNING };
		}
		if (count >= WARN_THRESHOLD) {
			// 同签名只警告一次（指纹去重），间隔 60s 防刷屏
			const now = Date.now();
			if (sig !== lastWarnedSig || now - lastWarnedAt > 60_000) {
				lastWarnedSig = sig;
				lastWarnedAt = now;
				try {
					pi.sendUserMessage(`【循环警告】${LOOP_WARNING}`, { deliverAs: "steer" });
				} catch {
					/* 注入失败不阻塞工具执行 */
				}
			}
		}
		return undefined;
	});
}
