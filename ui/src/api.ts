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
  revived: boolean
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
  status: "idle" | "running" | "done" | "failed" | "stopped"
  platform: string | null
  elapsed: number
  pid?: number
  exit_code?: number
  run_id?: string
  log_tail: string
}

export interface BenchRunInfo {
  id: string
  bench_id: string
  name: string
  filters: Record<string, string>
  started_at: number
  finished_at: number | null
  status: string
  pid: number
  snapshot: boolean
  resumed_from?: string
  result: { total: number; solved: number; by_category: Record<string, { solved: number; total: number }>; elapsed: number; partial?: boolean } | null
}

export type Mode = "main" | "bench"

const base = (mode: Mode) => (mode === "bench" ? "/api/bench" : "/api")
const qrun = (runId?: string) => (runId ? `?run=${encodeURIComponent(runId)}` : "")
const qsess = (sessionId?: string) => (sessionId ? `?session=${encodeURIComponent(sessionId)}` : "")

export async function fetchState(mode: Mode = "main", runId?: string, sessionId?: string): Promise<ChallengeView[]> {
  const q = mode === "bench" ? qrun(runId) : qsess(sessionId)
  const r = await fetch(`${base(mode)}/state${q}`)
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

export async function fetchDigest(cid: string, mode: Mode = "main", runId?: string, sessionId?: string): Promise<string> {
  const q = mode === "bench" ? qrun(runId) : qsess(sessionId)
  const r = await fetch(`${base(mode)}/digest/${cid}${q}`)
  if (!r.ok) return "摘要获取失败"
  const data = await r.json()
  return data.digest ?? "摘要获取失败"
}

export async function fetchLogs(cid: string, tail = 300, mode: Mode = "main", runId?: string, sessionId?: string): Promise<string> {
  const extra = mode === "bench" && runId ? `&run=${encodeURIComponent(runId)}` : (mode === "main" && sessionId ? `&session=${encodeURIComponent(sessionId)}` : "")
  const r = await fetch(`${base(mode)}/logs/${cid}?tail=${tail}${extra}`)
  if (!r.ok) return ""
  const data = await r.json()
  return data.text ?? ""
}

export async function fetchBoard(cid: string, mode: Mode = "main", runId?: string, sessionId?: string): Promise<Board> {
  const q = mode === "bench" ? qrun(runId) : qsess(sessionId)
  const r = await fetch(`${base(mode)}/board/${cid}${q}`)
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

export async function fetchTranscript(cid: string, worker = 0, limit = 600, mode: Mode = "main", runId?: string, sessionId?: string): Promise<Transcript> {
  const extra = mode === "bench" && runId ? `&run=${encodeURIComponent(runId)}` : (mode === "main" && sessionId ? `&session=${encodeURIComponent(sessionId)}` : "")
  const r = await fetch(`${base(mode)}/transcript/${cid}?worker=${worker}&limit=${limit}${extra}`)
  if (!r.ok) return { cid, workers: 0, worker: 0, worker_files: [], entries: [] }
  return await r.json()
}

export interface SessionInfo {
  id: string
  archived_at: number
  reason: string
  summary: { challenges: number; solved: number }
  logs_moved: number
}

export async function fetchSessionHistory(): Promise<SessionInfo[]> {
  const r = await fetch("/api/session/history")
  if (!r.ok) return []
  const d = await r.json()
  return d.sessions ?? []
}

export async function archiveSession(reason: string): Promise<{ ok: boolean; session_id?: string; msg?: string }> {
  try {
    const r = await fetch("/api/session/archive", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    })
    const d = await r.json()
    return { ok: r.ok && d.ok, session_id: d.session_id, msg: d.msg }
  } catch {
    return { ok: false, msg: "归档失败" }
  }
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

export async function resumeBench(runId: string): Promise<{ ok: boolean; msg: string }> {
  const r = await fetch(`/api/bench/resume/${encodeURIComponent(runId)}`, { method: "POST" })
  return await r.json()
}

// ---- 比赛模式（真实平台 dasctf）----
export interface MatchStatus {
  status: "idle" | "running" | "done" | "failed"
  elapsed: number
  pid?: number | null
  exit_code?: number | null
  log_tail: string
}

export async function fetchMatchStatus(): Promise<MatchStatus> {
  const r = await fetch("/api/match/status")
  if (!r.ok) return { status: "idle", elapsed: 0, log_tail: "" }
  return await r.json()
}

export async function startMatch(loop: number): Promise<{ ok: boolean; msg: string }> {
  const r = await fetch("/api/match/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ loop }),
  })
  return await r.json()
}

export async function stopMatch(): Promise<{ ok: boolean; msg: string }> {
  const r = await fetch("/api/match/stop", { method: "POST" })
  return await r.json()
}

// ---- 解题报告（report_writer）----
export interface ReportInfo {
  cid: string
  name: string
  size: number
  mtime: number
}

export async function fetchReports(): Promise<{ reports: ReportInfo[]; missing: string[] }> {
  const r = await fetch("/api/reports")
  if (!r.ok) return { reports: [], missing: [] }
  return await r.json()
}

export async function generateReport(cid: string): Promise<{ ok: boolean; msg: string }> {
  const r = await fetch(`/api/reports/${encodeURIComponent(cid)}/generate`, { method: "POST" })
  return await r.json()
}

export async function fetchReportText(cid: string): Promise<string> {
  const r = await fetch(`/api/reports/${encodeURIComponent(cid)}`)
  if (!r.ok) return "（报告不存在或尚未生成）"
  const d = await r.json()
  return d.text ?? ""
}

export const reportDownloadUrl = (cid: string) => `/api/reports/${encodeURIComponent(cid)}/download`
export const reportsZipUrl = () => "/api/reports/download-all"

export interface AuditReport {
  run_id: string
  summary: { clean: number; osint: number; cheat: number; total: number }
  solved_breakdown: { clean: number; osint: number; cheat: number; total: number }
  challenges: Record<string, { verdict: string; evidence_count: number; evidence: { tool: string; arg: string }[] }>
}

export async function fetchAudit(runId: string): Promise<AuditReport | null> {
  const r = await fetch(`/api/bench/audit/${encodeURIComponent(runId)}`)
  if (!r.ok) return null
  return await r.json()
}

// ---- 统一配置中心 ----
export interface ProviderInfo { id: string; label: string; base_url: string; models: string[] }
export interface AgentConfig {
  llm: Record<string, { model: string; thinking?: string }>
  runtime: { max_parallel_challenges: number; planning_enabled: boolean; supervisor_enabled: boolean; kb_enabled: boolean }
  providers: ProviderInfo[]
  keys: Record<string, boolean>
  catalog: Record<string, { name: string; reasoning: boolean; contextWindow: number; maxTokens: number }>
  presets: { id: string; name: string; base_url: string; models: string[] }[]
}

export async function fetchConfig(): Promise<AgentConfig | null> {
  const r = await fetch("/api/config")
  if (!r.ok) return null
  return await r.json()
}

export async function saveConfig(body: {
  llm?: Record<string, { model: string; thinking?: string }>
  runtime?: Record<string, unknown>
  providers?: ProviderInfo[]
  api_keys?: Record<string, string>
}): Promise<{ ok: boolean; msg: string }> {
  const r = await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  return await r.json()
}

export async function fetchBenchHistory(): Promise<BenchRunInfo[]> {
  const r = await fetch("/api/bench/history")
  if (!r.ok) return []
  const d = await r.json()
  return d.runs ?? []
}
