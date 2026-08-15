import { useEffect, useRef, useState } from "react"
import type { Board, ChallengeView } from "./api"
import {
  fetchBoard, fetchDigest, fetchKaliStatus, fetchLogs, fetchState, fetchSummary,
  postConfirm, postHint, postVerify,
} from "./api"
import EventStream, { parseToolEvents, type ToolEvent } from "./components/EventStream"
import GlassCard from "./components/GlassCard"
import StatCard from "./components/StatCard"
import StarBadge, { CategoryBadge } from "./components/StarBadge"
import Toast from "./components/Toast"

const STATUS_TEXT: Record<string, string> = {
  new: "未触及", queued: "排队中", solving: "解题中",
  solved: "已夺取", needs_hint: "待提示", dead: "已放弃",
}

function fmtElapsed(s: number): string {
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m${Math.round(s % 60)}s`
  return `${Math.floor(s / 3600)}h${Math.round((s % 3600) / 60)}m`
}
function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`
  return String(n)
}

interface Summary { solved: number; solving: number; needs_hint: number; total: number; cost: number; tokens: number }

function Shell({ summary, kali }: { summary: Summary | null; kali: "ok" | "bad" | "?" }) {
  const [countdown, setCountdown] = useState("")
  useEffect(() => {
    const tick = () => {
      const now = new Date()
      const end = new Date()
      end.setHours(17, 0, 0, 0) // 默认 17:00（初赛 14:00-17:00）
      let diff = Math.max(0, end.getTime() - now.getTime())
      if (diff <= 0) diff = 0
      const h = Math.floor(diff / 3600000), m = Math.floor((diff % 3600000) / 60000), s = Math.floor((diff % 60000) / 1000)
      setCountdown(`${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`)
    }
    tick()
    const t = setInterval(tick, 1000)
    return () => clearInterval(t)
  }, [])
  const left = countdown && (() => {
    const now = new Date(); const end = new Date(); end.setHours(17, 0, 0, 0)
    const diff = end.getTime() - now.getTime()
    if (diff <= 0) return ""
    if (diff < 600000) return " danger"
    if (diff < 3600000) return " warn"
    return ""
  })()
  return (
    <header className="shell">
      <div className="shell-brand">
        <span className="shell-logo">⚑</span>
        <span className="shell-name">z-sec</span>
        <span className="shell-sub">星图 · AI 夺旗驾驶舱</span>
      </div>
      <div className="shell-right">
        <span className={`shell-stat${left}`}>⏱ <b>{countdown || "--:--:--"}</b></span>
        <span className="shell-stat">⚑ <b>{summary ? `${summary.solved}/${summary.total}` : "-/-"}</b></span>
        <span className="shell-stat">¥ <b>{summary ? summary.cost.toFixed(4) : "-"}</b></span>
        <span className="shell-stat">Kali <span className={`dot ${kali === "ok" ? "ok" : kali === "bad" ? "bad" : ""}`} /></span>
      </div>
    </header>
  )
}

function Overview() {
  const [challenges, setChallenges] = useState<ChallengeView[]>([])
  const [summary, setSummary] = useState<Summary | null>(null)
  const [kali, setKali] = useState<"ok" | "bad" | "?">("?")
  const [error, setError] = useState("")
  const [selected, setSelected] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const [list, sum, k] = await Promise.all([fetchState(), fetchSummary(), fetchKaliStatus()])
        if (!alive) return
        setChallenges(list); setSummary(sum); setKali(k); setError("")
      } catch (e) {
        if (alive) setError(`后端不可达：${String(e)}`)
      }
    }
    load()
    const t = setInterval(load, 10000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  if (selected) return <Detail cid={selected} onBack={() => setSelected(null)} />

  return (
    <div className="page">
      <Shell summary={summary} kali={kali} />
      {error && <div className="banner-error">{error}</div>}

      <div className="hero-grid">
        <StatCard label="已夺取" icon="⚑" accent="green"
          value={summary ? `${summary.solved}` : "—"}
          foot={summary && summary.total ? `共 ${summary.total} 颗星 · ${Math.round((summary.solved / summary.total) * 100)}%` : "等待拉题"} />
        <StatCard label="解题中" icon="✦" accent="blue"
          value={summary ? `${summary.solving}` : "—"}
          foot="双模型竞速进行中" />
        <StatCard label="待提示" icon="◈" accent="amber"
          value={summary ? `${summary.needs_hint}` : "—"}
          foot="需要你关注（写 hint 纠偏）" />
        <StatCard label="累计成本" icon="¥" accent="blue"
          value={summary ? summary.cost.toFixed(4) : "—"}
          foot={summary ? `${fmtTokens(summary.tokens)} tokens` : ""}
          spark={[0, 1, 2, 4, 3, 6, 8, 7, 9, 12, 10, 14]} />
      </div>

      <div className="star-grid">
        {challenges.map((c, idx) => (
          <div key={c.cid}
            className={`star-card ${c.status === "solved" ? "solved" : ""} ${c.status === "needs_hint" ? "needs-hint" : ""}`}
            style={{ animationDelay: `${Math.min(idx * 30, 240)}ms` }}
            onClick={() => setSelected(c.cid)}>
            <div className="star-card-head">
              <span className={`star-dot ${c.status}`} />
              <span className="star-card-title">{c.name || c.cid}</span>
              <CategoryBadge category={c.category} />
            </div>
            <div className="star-card-desc">{c.digest_first ?? ""}</div>
            <div className="star-card-meta">
              <span>⏱ <b>{fmtElapsed(c.elapsed)}</b></span>
              <span>◈ <b>{fmtTokens(c.tokens)}</b></span>
              <span>¥ <b>{c.cost.toFixed(4)}</b></span>
              <span style={{ marginLeft: "auto" }}>{STATUS_TEXT[c.status] ?? c.status}</span>
            </div>
          </div>
        ))}
        {challenges.length === 0 && (
          <div className="empty" style={{ gridColumn: "1 / -1" }}>
            <div className="empty-star">✦</div>星图尚未点亮——等待编排器拉题
          </div>
        )}
      </div>
    </div>
  )
}

function Detail({ cid, onBack }: { cid: string; onBack: () => void }) {
  const [digest, setDigest] = useState("加载中…")
  const [logs, setLogs] = useState<ToolEvent[]>([])
  const [board, setBoard] = useState<Board>({})
  const [hint, setHint] = useState("")
  const [toast, setToast] = useState<{ msg: string; kind?: "ok" | "err" }>({ msg: "" })
  const [challenge, setChallenge] = useState<ChallengeView | null>(null)
  const [summary, setSummary] = useState<Summary | null>(null)
  const timerRef = useRef(0)

  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const [d, raw, b] = await Promise.all([
          fetchDigest(cid), fetchLogs(cid, 300), fetchBoard(cid),
        ])
        if (!alive) return
        setDigest(d); setLogs(parseToolEvents(raw)); setBoard(b)
      } catch { /* ignore */ }
      try {
        const [list, sum] = await Promise.all([fetchState(), fetchSummary()])
        if (!alive) return
        setChallenge(list.find((c) => c.cid === cid) ?? null)
        setSummary(sum)
      } catch { /* ignore */ }
    }
    load()
    timerRef.current = window.setInterval(load, 15000)
    return () => { alive = false; window.clearInterval(timerRef.current) }
  }, [cid])

  const sendHint = async () => {
    if (!hint.trim()) return
    const ok = await postHint(cid, hint)
    setToast(ok ? { msg: "提示已写入，编排器下一轮注入", kind: "ok" } : { msg: "写入失败", kind: "err" })
    setHint("")
  }
  const confirm = async (flag: string) => {
    const ok = await postConfirm(cid, flag)
    setToast(ok ? { msg: "已提交复核，编排器将代交", kind: "ok" } : { msg: "提交失败", kind: "err" })
  }
  const toggleVerify = async () => {
    await postVerify(cid)
    setToast({ msg: "已切换复核模式", kind: "ok" })
  }

  const stuck = challenge?.status === "needs_hint"

  return (
    <div className="page">
      <Shell summary={summary} kali="?" />
      <Toast msg={toast.msg} kind={toast.kind} />
      <div className="panel-card" style={{ marginBottom: 12, display: "flex", alignItems: "center", gap: 12 }}>
        <button className="btn btn-sm" onClick={onBack}>← 星图</button>
        <span className="panel-card-title" style={{ fontSize: 16 }}>{challenge?.name || cid}</span>
        {challenge && <StarBadge status={challenge.status} />}
        {challenge && <CategoryBadge category={challenge.category} />}
        <span className="muted" style={{ marginLeft: "auto", fontFamily: "var(--font-mono)" }}>
          ⏱ {fmtElapsed(challenge?.elapsed ?? 0)} · ◈ {fmtTokens(challenge?.tokens ?? 0)} · ¥ {(challenge?.cost ?? 0).toFixed(4)}
        </span>
      </div>

      <div className="detail-grid">
        <div>
          <GlassCard title="当前摘要（AI 翻译日志）" warn={stuck}>
            <div className={`digest-lead ${digest === "摘要生成失败" ? "digest-muted" : ""}`}>{digest}</div>
          </GlassCard>
          <GlassCard title="工具调用流">
            <EventStream events={logs} />
          </GlassCard>
        </div>

        <div className="detail-side">
          <GlassCard title="人工纠偏（hint）">
            <textarea value={hint} onChange={(e) => setHint(e.target.value)} rows={3}
              placeholder="一次一个方向、指向具体线索，如：看 PNG 文件尾部的 base64" />
            <button className="btn btn-primary" style={{ marginTop: 8, width: "100%" }}
              onClick={sendHint} disabled={!hint.trim()}>写入提示</button>
            <p className="muted" style={{ marginTop: 8, fontSize: 11.5 }}>
              编排器下一轮派工自动注入；hint 是"意图级"输入，会被转译成技术指引。</p>
          </GlassCard>

          <GlassCard title="方向看板（Supervisor 维护）">
            <ul className="board-list">
              {(board.ideas ?? []).map((i, n) => (
                <li key={n}>
                  <span className={`idea-dot idea-${i.status ?? "pending"}`} />
                  {i.content}{i.result ? <span className="muted"> — {i.result}</span> : null}
                </li>
              ))}
              {(board.ideas ?? []).length === 0 && <li className="muted">（Supervisor 尚未产出方向）</li>}
            </ul>
            <ul className="board-list" style={{ marginTop: 6 }}>
              {(board.memory ?? []).map((m, n) => (
                <li key={`m${n}`}>
                  <span className={`kind-badge kind-${m.kind ?? "fact"}`}>{m.kind ?? "fact"}</span>
                  {m.content}
                </li>
              ))}
              {(board.memory ?? []).length === 0 && <li className="muted">（暂无已知事实/边界）</li>}
            </ul>
          </GlassCard>

          <GlassCard title="提交复核" actions={
            <button className="btn btn-sm" onClick={toggleVerify}>
              {challenge?.verify_required ? "复核模式：开" : "复核模式：关"}
            </button>}>
            {(challenge?.pending_flags ?? []).map((f) => (
              <div key={f} className="pending-row">
                <code>{f.slice(0, 60)}</code>
                <button className="btn btn-sm btn-success" onClick={() => confirm(f)}>确认提交</button>
              </div>
            ))}
            {(challenge?.pending_flags ?? []).length === 0 && (
              <p className="muted">暂无待复核候选 flag</p>
            )}
          </GlassCard>
        </div>
      </div>
    </div>
  )
}

export default function App() {
  return <Overview />
}
