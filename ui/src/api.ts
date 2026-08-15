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

export async function fetchState(): Promise<ChallengeView[]> {
  const r = await fetch("/api/state")
  if (!r.ok) throw new Error(`state ${r.status}`)
  const data = await r.json()
  return data.challenges ?? []
}

export async function fetchSummary(): Promise<Summary> {
  try {
    const r = await fetch("/api/summary")
    if (!r.ok) throw new Error(`summary ${r.status}`)
    return await r.json()
  } catch {
    return { solved: 0, solving: 0, needs_hint: 0, total: 0, cost: 0, tokens: 0 }
  }
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

export async function fetchDigest(cid: string): Promise<string> {
  const r = await fetch(`/api/digest/${cid}`)
  if (!r.ok) return "摘要获取失败"
  const data = await r.json()
  return data.digest ?? "摘要获取失败"
}

export async function fetchLogs(cid: string, tail = 300): Promise<string> {
  const r = await fetch(`/api/logs/${cid}?tail=${tail}`)
  if (!r.ok) return ""
  const data = await r.json()
  return data.text ?? ""
}

export async function fetchBoard(cid: string): Promise<Board> {
  const r = await fetch(`/api/board/${cid}`)
  if (!r.ok) return {}
  const data = await r.json()
  return data.board ?? {}
}

export async function postHint(cid: string, text: string): Promise<boolean> {
  const r = await fetch(`/api/hints/${cid}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  })
  return r.ok
}

export async function postConfirm(cid: string, flag: string): Promise<boolean> {
  const r = await fetch(`/api/confirm/${cid}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ flag }),
  })
  return r.ok
}

export async function postVerify(cid: string): Promise<boolean> {
  const r = await fetch(`/api/verify/${cid}`, { method: "POST" })
  return r.ok
}
