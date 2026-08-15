export interface ChallengeView {
  cid: string
  name: string
  category: string
  status: string
  races: number
  attempts: number
  elapsed: number
  wrong_submits: number
  verify_required: boolean
  pending_flags: string[]
  tokens: number
  cost: number
  digest_first: string
  connection: string
}

export interface Summary {
  solved: number
  solving: number
  needs_hint: number
  total: number
  cost: number
  tokens: number
}

export interface Board {
  ideas?: { id: string; content: string; status: string; result?: string }[]
  memory?: { id: string; kind: string; content: string }[]
}

export interface BenchInfo {
  id: string
  name: string
  desc: string
  challenges: number
  categories: Record<string, number>
  truth: number | null
}

export interface BenchStatus {
  status: "idle" | "running" | "done" | "failed"
  platform: string | null
  elapsed: number
  pid?: number
  exit_code?: number
  log_tail: string
}

export type Mode = "main" | "bench"

const base = (mode: Mode) => (mode === "bench" ? "/api/bench" : "/api")

export async function fetchState(mode: Mode = "main"): Promise<ChallengeView[]> {
  const r = await fetch(`${base(mode)}/state`)
  if (!r.ok) throw new Error(`state ${r.status}`)
  const data = await r.json()
  return data.challenges ?? []
}

export async function fetchKaliStatus(): Promise<"ok" | "bad"> {
  try {
    const r = await fetch("/api/kali-status")
    if (!r.ok) return "bad"
    const d = await r.json()
    return d.ok ? "ok" : "bad"
  } catch {
    return "bad"
  }
}

export async function fetchDigest(cid: string, mode: Mode = "main"): Promise<string> {
  const r = await fetch(`${base(mode)}/digest/${cid}`)
  if (!r.ok) return "摘要获取失败"
  const data = await r.json()
  return data.digest ?? "摘要获取失败"
}

export async function fetchLogs(cid: string, tail = 300, mode: Mode = "main"): Promise<string> {
  const r = await fetch(`${base(mode)}/logs/${cid}?tail=${tail}`)
  if (!r.ok) return ""
  const data = await r.json()
  return data.text ?? ""
}

export async function fetchBoard(cid: string, mode: Mode = "main"): Promise<Board> {
  const r = await fetch(`${base(mode)}/board/${cid}`)
  if (!r.ok) return {}
  const data = await r.json()
  return data.board ?? {}
}

export interface TranscriptEntry {
  kind: "prompt" | "think" | "reply" | "call" | "result"
  ts: string
  text: string
  tool?: string
  isError?: boolean
}

export interface Transcript {
  cid: string
  workers: number
  worker: number
  worker_files: string[]
  entries: TranscriptEntry[]
}

export async function fetchTranscript(cid: string, worker = 0, limit = 600, mode: Mode = "main"): Promise<Transcript> {
  const r = await fetch(`${base(mode)}/transcript/${cid}?worker=${worker}&limit=${limit}`)
  if (!r.ok) return { cid, workers: 0, worker: 0, worker_files: [], entries: [] }
  return await r.json()
}

export async function postHint(cid: string, text: string, mode: Mode = "main"): Promise<boolean> {
  const r = await fetch(`${base(mode)}/hints/${cid}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  })
  return r.ok
}

export async function postConfirm(cid: string, flag: string, mode: Mode = "main"): Promise<boolean> {
  const r = await fetch(`${base(mode)}/confirm/${cid}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ flag }),
  })
  return r.ok
}

export async function postVerify(cid: string, mode: Mode = "main"): Promise<boolean> {
  const r = await fetch(`${base(mode)}/verify/${cid}`, { method: "POST" })
  return r.ok
}

// ---- Benchmark 模块 ----
export async function fetchBenchList(): Promise<BenchInfo[]> {
  const r = await fetch("/api/bench/list")
  if (!r.ok) throw new Error(`bench list ${r.status}`)
  const d = await r.json()
  return d.benchmarks ?? []
}

export async function fetchBenchStatus(): Promise<BenchStatus> {
  const r = await fetch("/api/bench/status")
  if (!r.ok) return { status: "idle", platform: null, elapsed: 0, log_tail: "" }
  return await r.json()
}

export async function startBench(id: string, filters: Record<string, string>): Promise<{ ok: boolean; msg: string }> {
  const r = await fetch("/api/bench/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, filters }),
  })
  return await r.json()
}

export async function stopBench(): Promise<{ ok: boolean; msg: string }> {
  const r = await fetch("/api/bench/stop", { method: "POST" })
  return await r.json()
}
