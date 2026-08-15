import { useEffect, useRef, useState } from "react"
import type { BenchInfo, BenchRunInfo, BenchStatus, Board, ChallengeView, Mode, SessionInfo, Summary } from "./api"
import {
  archiveSession, fetchBenchHistory, fetchBenchList, fetchBenchStatus, fetchBoard, fetchDigest,
  fetchKaliStatus, fetchSessionHistory, fetchState, postConfirm, postHint, postVerify,
  resumeBench, startBench, stopBench,
} from "./api"
import FullTranscript from "./components/FullTranscript"
import GlassCard from "./components/GlassCard"
import StatCard from "./components/StatCard"
import StarBadge, { CategoryBadge } from "./components/StarBadge"
import Toast from "./components/Toast"

const STATUS_TEXT: Record<string, string> = {
  new: "未触及", queued: "排队中", solving: "解题中",
  solved: "已夺取", needs_hint: "待提示", dead: "已放弃",
}

// 初赛（线上资格赛）结束时间：2026-08-21 17:00（赛程 14:00–17:00）
const RACE_DEADLINE = new Date("2026-08-21T17:00:00+08:00")

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
function computeSummary(list: ChallengeView[]): Summary {
  const s = { solved: 0, solving: 0, needs_hint: 0, total: list.length, cost: 0, tokens: 0 }
  for (const c of list) {
    if (c.status === "solved") s.solved++
    else if (c.status === "solving") s.solving++
    else if (c.status === "needs_hint") s.needs_hint++
    s.cost += c.cost ?? 0
    s.tokens += c.tokens ?? 0
  }
  return s
}

function Shell({ summary, kali }: { summary: Summary; kali: "ok" | "bad" | "?" }) {
  const [countdown, setCountdown] = useState("")
  useEffect(() => {
    const tick = () => {
      const diff = Math.max(0, RACE_DEADLINE.getTime() - Date.now())
      const h = Math.floor(diff / 3600000), m = Math.floor((diff % 3600000) / 60000), s = Math.floor((diff % 60000) / 1000)
      setCountdown(diff > 0
        ? `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
        : "已结束")
    }
    tick()
    const t = setInterval(tick, 1000)
    return () => clearInterval(t)
  }, [])
  const cls = (() => {
    const diff = RACE_DEADLINE.getTime() - Date.now()
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
        <span className={`shell-stat${cls}`}
          title={`初赛结束时间 ${RACE_DEADLINE.toLocaleString("zh-CN")}`}>⏱ 初赛 <b>{countdown}</b></span>
        <span className="shell-stat">⚑ <b>{summary.total ? `${summary.solved}/${summary.total}` : "-/-"}</b></span>
        <span className="shell-stat">¥ <b>{summary.cost.toFixed(4)}</b></span>
        <span className="shell-stat">Kali <span className={`dot ${kali === "ok" ? "ok" : kali === "bad" ? "bad" : ""}`} /></span>
      </div>
    </header>
  )
}

// ---------- 挑战星卡（主/bench 共用） ----------
function StarGrid({ challenges, onOpen }: {
  challenges: ChallengeView[]
  onOpen: (cid: string) => void
}) {
  return (
    <div className="star-grid">
      {challenges.map((c, idx) => (
        <div key={c.cid}
          className={`star-card ${c.status === "solved" ? "solved" : ""} ${c.status === "needs_hint" ? "needs-hint" : ""}`}
          style={{ animationDelay: `${Math.min(idx * 30, 240)}ms` }}
          onClick={() => onOpen(c.cid)}>
          <div className="star-card-head">
            <span className={`star-dot ${c.status}`} />
            <span className="star-card-title">{c.name || c.cid}</span>
            {c.connection && (
              <span className="cat-badge cat-misc"
                title={c.revived ? `远程服务 ${c.connection}（本地已复活）` : `远程服务 ${c.connection}`}>
                {c.revived ? "靶机·复活" : "靶机"}
              </span>
            )}
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
  )
}

// ---------- 星图（比赛） ----------
function Overview() {
  const [challenges, setChallenges] = useState<ChallengeView[]>([])
  const [kali, setKali] = useState<"ok" | "bad" | "?">("?")
  const [error, setError] = useState("")
  const [selected, setSelected] = useState<string | null>(null)
  const [sessions, setSessions] = useState<SessionInfo[]>([])
  const [selectedSession, setSelectedSession] = useState<string | null>(null)
  const [toast, setToast] = useState<{ msg: string; kind?: "ok" | "err" }>({ msg: "" })

  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const [list, k] = await Promise.all([
          fetchState("main", undefined, selectedSession ?? undefined), fetchKaliStatus(),
        ])
        if (!alive) return
        setChallenges(list); setKali(k); setError("")
      } catch (e) {
        if (alive) setError(`后端不可达：${String(e)}`)
      }
      try {
        const ss = await fetchSessionHistory()
        if (alive) setSessions(ss)
      } catch { /* ignore */ }
    }
    load()
    const t = setInterval(load, 10000)
    return () => { alive = false; clearInterval(t) }
  }, [selectedSession])

  if (selected) return <Detail cid={selected} mode="main" sessionId={selectedSession ?? undefined}
    onBack={() => setSelected(null)} />

  const summary = computeSummary(challenges)
  const fmtTime = (ts: number) => new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false })

  return (
    <>
      <Shell summary={summary} kali={kali} />
      <div className="page">
        <Toast msg={toast.msg} kind={toast.kind} />
        {error && <div className="banner-error">{error}</div>}

        {selectedSession && (
          <div className="banner-error" style={{ borderColor: "var(--border-bright)", color: "var(--stellar-bright)",
            background: "rgba(125,146,232,.08)", display: "flex", alignItems: "center", gap: 10 }}>
            <span>正在回看历史场次 <b>{selectedSession}</b>（只读）</span>
            <span className="spacer" style={{ flex: 1 }} />
            <button className="btn btn-sm" onClick={() => { setSelectedSession(null); setSelected(null) }}>
              返回当前场次</button>
          </div>
        )}

        <div className="hero-grid">
          <StatCard label="已夺取" icon="⚑" accent="green" value={`${summary.solved}`}
            foot={summary.total ? `共 ${summary.total} 颗星 · ${Math.round((summary.solved / summary.total) * 100)}%` : "等待拉题"} />
          <StatCard label="解题中" icon="✦" accent="blue" value={`${summary.solving}`} foot="双模型竞速进行中" />
          <StatCard label="待提示" icon="◈" accent="amber" value={`${summary.needs_hint}`} foot="需要你关注（写 hint 纠偏）" />
          <StatCard label="累计成本" icon="¥" accent="blue" value={summary.cost.toFixed(4)}
            foot={`${fmtTokens(summary.tokens)} tokens`} />
        </div>
        <StarGrid challenges={challenges} onOpen={setSelected} />

        <div className="panel-card" style={{ marginTop: 16 }}>
          <div className="panel-card-head">
            <h3 className="panel-card-title">历史场次（持久化，重启不丢）</h3>
            <div className="panel-card-actions">
              {!selectedSession && (
                <button className="btn btn-sm"
                  onClick={async () => {
                    const r = await archiveSession("手动归档")
                    setToast(r.ok ? { msg: `已归档为新场次 ${r.session_id}`, kind: "ok" }
                      : { msg: r.msg ?? "归档失败", kind: "err" })
                    const ss = await fetchSessionHistory()
                    setSessions(ss)
                  }}>
                  归档当前 · 新开一场
                </button>
              )}
            </div>
          </div>
          {sessions.length === 0 ? (
            <p className="muted">暂无历史场次——点「归档当前 · 新开一场」把当前比赛状态存起来。</p>
          ) : (
            <table className="table">
              <thead>
                <tr><th>归档时间</th><th>原因</th><th>战果</th><th>归档日志数</th><th></th></tr>
              </thead>
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.id} className="row-click"
                    style={selectedSession === s.id ? { background: "rgba(125,146,232,.12)" } : undefined}
                    onClick={() => { setSelectedSession(selectedSession === s.id ? null : s.id); setSelected(null) }}>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>{fmtTime(s.archived_at)}</td>
                    <td>{s.reason || "-"}</td>
                    <td>{s.summary?.solved}/{s.summary?.challenges}</td>
                    <td>{s.logs_moved}</td>
                    <td><button className="btn btn-sm" onClick={(e) => {
                      e.stopPropagation()
                      setSelectedSession(selectedSession === s.id ? null : s.id)
                      setSelected(null)
                    }}>{selectedSession === s.id ? "收起" : "查看"}</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </>
  )
}

// ---------- Benchmark 跑分页 ----------
function BenchPage() {
  const [benches, setBenches] = useState<BenchInfo[]>([])
  const [selected, setSelected] = useState<string>("")
  const [filters, setFilters] = useState({ difficulty: "", categories: "", only: "", exclude: "" })
  const [status, setStatus] = useState<BenchStatus>({ status: "idle", platform: null, elapsed: 0, log_tail: "" })
  const [challenges, setChallenges] = useState<ChallengeView[]>([])
  const [history, setHistory] = useState<BenchRunInfo[]>([])
  const [selectedRun, setSelectedRun] = useState<string | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [toast, setToast] = useState<{ msg: string; kind?: "ok" | "err" }>({ msg: "" })

  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const list = await fetchBenchList()
        if (alive) setBenches(list)
      } catch { /* ignore */ }
      try {
        const st = await fetchBenchStatus()
        if (!alive) return
        setStatus(st)
        if (selectedRun === null && (st.status === "running" || st.status === "done" || st.status === "failed" || st.status === "stopped")) {
          setChallenges(await fetchState("bench"))
        } else if (selectedRun !== null) {
          setChallenges(await fetchState("bench", selectedRun))
        }
      } catch { /* ignore */ }
      try {
        const h = await fetchBenchHistory()
        if (alive) setHistory(h)
      } catch { /* ignore */ }
    }
    load()
    const t = setInterval(load, 5000)
    return () => { alive = false; clearInterval(t) }
  }, [selectedRun])

  if (open) return <Detail cid={open} mode="bench" runId={selectedRun ?? undefined}
    onBack={() => setOpen(null)} />

  const running = status.status === "running"
  const summary = computeSummary(challenges)
  const fmtTime = (ts: number | null) => ts ? new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false }) : "-"
  const RUN_STATUS: Record<string, string> = { running: "运行中", done: "已完成", failed: "失败", stopped: "已停止" }

  return (
    <>
      <Shell summary={summary} kali="?" />
      <div className="page">
        <Toast msg={toast.msg} kind={toast.kind} />

        {selectedRun && (
          <div className="banner-error" style={{ borderColor: "var(--border-bright)", color: "var(--stellar-bright)",
            background: "rgba(125,146,232,.08)", display: "flex", alignItems: "center", gap: 10 }}>
            <span>正在查看历史跑分 <b>{selectedRun}</b>（只读）</span>
            <span className="spacer" style={{ flex: 1 }} />
            <button className="btn btn-sm" onClick={() => { setSelectedRun(null); setOpen(null) }}>
              返回当前跑分</button>
          </div>
        )}

        <div className="bench-grid">
          {benches.map((b) => (
            <div key={b.id}
              className={`bench-card ${selected === b.id ? "selected" : ""}`}
              onClick={() => setSelected(b.id)}>
              <div className="bench-name">
                <span className={`star-dot ${selected === b.id ? "solving" : "new"}`} />
                {b.name}
              </div>
              <div className="bench-desc">{b.desc}</div>
              <div className="bench-stats">
                <span>题库 <b>{b.challenges}</b> 题</span>
                {b.truth !== null && <span>· 真值 <b>{b.truth}</b> 份</span>}
                <span>· 分类 {Object.keys(b.categories || {}).length}</span>
              </div>
              <div className="bench-cats">
                {Object.entries(b.categories || {}).map(([c, n]) => (
                  <span key={c} className="cat-badge cat-misc">{c}×{n}</span>
                ))}
              </div>
            </div>
          ))}
        </div>

        {!selectedRun && (
          <div className="filter-row">
            <label>难度</label>
            <select value={filters.difficulty}
              onChange={(e) => setFilters({ ...filters, difficulty: e.target.value })}>
              <option value="">全部</option>
              <option value="very_easy,easy">very_easy+easy</option>
              <option value="easy">easy</option>
              <option value="moderate">moderate</option>
              <option value="hard">hard</option>
            </select>
            <label>题型</label>
            <input className="input" placeholder="如 crypto,rev（空=全部）"
              value={filters.categories}
              onChange={(e) => setFilters({ ...filters, categories: e.target.value })} />
            <label>只跑</label>
            <input className="input" placeholder="cid，逗号分隔（可选）"
              value={filters.only}
              onChange={(e) => setFilters({ ...filters, only: e.target.value })} />
            <label>排除</label>
            <input className="input" placeholder="cid，逗号分隔（可选）"
              value={filters.exclude}
              onChange={(e) => setFilters({ ...filters, exclude: e.target.value })} />
            <span className="spacer" />
            <button className="btn btn-primary" disabled={!selected || running}
              onClick={async () => {
                const r = await startBench(selected, filters)
                setToast({ msg: r.msg, kind: r.ok ? "ok" : "err" })
              }}>
              {running ? "跑分中…" : "开始跑分"}
            </button>
            <button className="btn btn-danger" disabled={!running}
              onClick={async () => {
                const r = await stopBench()
                setToast({ msg: r.msg, kind: "ok" })
              }}>停止</button>
          </div>
        )}

        {!selectedRun && status.status !== "idle" && (
          <div className="run-panel">
            <div className="run-head">
              <span className={`run-badge`}>{
                status.status === "running" ? "运行中" :
                status.status === "done" ? "已完成" : status.status === "failed" ? "失败" :
                status.status === "stopped" ? "已停止" : "空闲"
              }</span>
              <span className="muted">题库 <b style={{ color: "var(--text)" }}>{status.platform}</b></span>
              <span className="muted">已运行 {fmtElapsed(status.elapsed)}</span>
              {status.run_id && <span className="muted">run {status.run_id}</span>}
              {status.pid && <span className="muted">pid {status.pid}</span>}
              <span className="spacer" style={{ flex: 1 }} />
              <span className="muted">成绩单：{summary.solved}/{summary.total} · ¥{summary.cost.toFixed(4)}</span>
            </div>
            {status.log_tail && <pre className="run-log">{status.log_tail}</pre>}
          </div>
        )}

        {history.length > 0 && (
          <div className="panel-card">
            <div className="panel-card-head">
              <h3 className="panel-card-title">历史跑分（持久化，重启不丢）</h3>
            </div>
            <table className="table">
              <thead>
                <tr><th>时间</th><th>题库</th><th>状态</th><th>成绩</th><th>耗时</th><th>过滤</th><th></th></tr>
              </thead>
              <tbody>
                {history.map((h) => (
                  <tr key={h.id} className={`row-click ${selectedRun === h.id ? "selected" : ""}`}
                    style={selectedRun === h.id ? { background: "rgba(125,146,232,.12)" } : undefined}
                    onClick={() => { setSelectedRun(selectedRun === h.id ? null : h.id); setOpen(null) }}>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>{fmtTime(h.started_at)}</td>
                    <td>{h.name}</td>
                    <td>
                      <span className={`star-dot ${h.status === "running" ? "solving" : h.status === "done" ? "solved" : "new"}`} />
                      {" "}{RUN_STATUS[h.status] ?? h.status}
                    </td>
                    <td>{h.result ? `${h.result.solved}/${h.result.total}` : "-"}</td>
                    <td>{h.result?.elapsed ? fmtElapsed(h.result.elapsed) : "-"}</td>
                    <td className="muted" style={{ fontSize: 11 }}>
                      {Object.entries(h.filters || {}).filter(([, v]) => v).map(([k, v]) => `${k}=${v}`).join(" ") || "-"}
                    </td>
                    <td>
                      <span style={{ display: "inline-flex", gap: 6 }}>
                        {h.snapshot && h.status !== "running" && (
                          <button className="btn btn-sm"
                            onClick={async (e) => {
                              e.stopPropagation()
                              const r = await resumeBench(h.id)
                              setToast({ msg: r.msg, kind: r.ok ? "ok" : "err" })
                              if (r.ok) { setSelectedRun(null); setHistory(await fetchBenchHistory()) }
                            }}>
                            续跑</button>
                        )}
                        <button className="btn btn-sm"
                          onClick={(e) => { e.stopPropagation(); setSelectedRun(selectedRun === h.id ? null : h.id); setOpen(null) }}>
                          {selectedRun === h.id ? "收起" : "查看"}</button>
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {challenges.length > 0 && (
          <>
            <div className="hero-grid">
              <StatCard label="已解出" icon="⚑" accent="green" value={`${summary.solved}`}
                foot={summary.total ? `${Math.round((summary.solved / summary.total) * 100)}% 解出率` : ""} />
              <StatCard label="解题中" icon="✦" accent="blue" value={`${summary.solving}`} foot="" />
              <StatCard label="待提示" icon="◈" accent="amber" value={`${summary.needs_hint}`} foot="" />
              <StatCard label="成本" icon="¥" accent="blue" value={summary.cost.toFixed(4)}
                foot={`${fmtTokens(summary.tokens)} tokens`} />
            </div>
            <StarGrid challenges={challenges} onOpen={setOpen} />
          </>
        )}
      </div>
    </>
  )
}

// ---------- 题目详情（主/bench 共用） ----------
function Detail({ cid, mode, runId, sessionId, onBack }: {
  cid: string; mode: Mode; runId?: string; sessionId?: string; onBack: () => void
}) {
  const [digest, setDigest] = useState("加载中…")
  const [board, setBoard] = useState<Board>({})
  const [hint, setHint] = useState("")
  const [toast, setToast] = useState<{ msg: string; kind?: "ok" | "err" }>({ msg: "" })
  const [challenge, setChallenge] = useState<ChallengeView | null>(null)
  const timerRef = useRef(0)

  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const [d, b] = await Promise.all([
          fetchDigest(cid, mode, runId, sessionId), fetchBoard(cid, mode, runId, sessionId),
        ])
        if (!alive) return
        setDigest(d); setBoard(b)
      } catch { /* ignore */ }
      try {
        const list = await fetchState(mode, runId, sessionId)
        if (!alive) return
        setChallenge(list.find((c) => c.cid === cid) ?? null)
      } catch { /* ignore */ }
    }
    load()
    timerRef.current = window.setInterval(load, 15000)
    return () => { alive = false; window.clearInterval(timerRef.current) }
  }, [cid, mode, runId, sessionId])

  const sendHint = async () => {
    if (!hint.trim() || readonly) return
    const ok = await postHint(cid, hint, mode)
    setToast(ok ? { msg: "提示已写入，编排器下一轮注入", kind: "ok" } : { msg: "写入失败", kind: "err" })
    setHint("")
  }
  const confirm = async (flag: string) => {
    if (readonly) return
    const ok = await postConfirm(cid, flag, mode)
    setToast(ok ? { msg: "已提交复核，编排器将代交", kind: "ok" } : { msg: "提交失败", kind: "err" })
  }

  const stuck = challenge?.status === "needs_hint"
  const readonly = (mode === "bench" && !!runId) || (mode === "main" && !!sessionId)

  return (
    <>
      <Toast msg={toast.msg} kind={toast.kind} />
      <div className="page">
        <div className="panel-card" style={{ marginBottom: 12, display: "flex", alignItems: "center", gap: 12 }}>
          <button className="btn btn-sm" onClick={onBack}>← {mode === "bench" ? "Benchmark" : "星图"}</button>
          <span className="panel-card-title" style={{ fontSize: 16 }}>{challenge?.name || cid}</span>
          {challenge && <StarBadge status={challenge.status} />}
          {challenge && <CategoryBadge category={challenge.category} />}
          {challenge?.connection && (
            <span className="cat-badge cat-misc"
              title={challenge.revived ? `远程服务 ${challenge.connection}（本地已复活）` : `远程服务 ${challenge.connection}`}>
              {challenge.revived ? "靶机·复活" : "靶机"} {challenge.connection}
            </span>
          )}
          <span className="muted" style={{ marginLeft: "auto", fontFamily: "var(--font-mono)" }}>
            ⏱ {fmtElapsed(challenge?.elapsed ?? 0)} · ◈ {fmtTokens(challenge?.tokens ?? 0)} · ¥ {(challenge?.cost ?? 0).toFixed(4)}
          </span>
        </div>

        <div className="detail-grid">
          <div>
            <GlassCard title="当前摘要（AI 翻译日志）" warn={stuck}>
              <div className={`digest-lead ${digest === "摘要生成失败" ? "digest-muted" : ""}`}>{digest}</div>
            </GlassCard>
            <GlassCard title="全程记录（指令 / 思考 / 回复 / 工具）">
              <FullTranscript cid={cid} mode={mode} runId={runId} sessionId={sessionId} />
            </GlassCard>
          </div>

          <div className="detail-side">
            <GlassCard title="人工纠偏（hint）">
              <textarea value={hint} onChange={(e) => setHint(e.target.value)} rows={3} disabled={readonly}
                placeholder={readonly ? "历史跑分只读，不可写提示" : "一次一个方向、指向具体线索，如：看 PNG 文件尾部的 base64"} />
              <button className="btn btn-primary" style={{ marginTop: 8, width: "100%" }}
                onClick={sendHint} disabled={!hint.trim() || readonly}>写入提示</button>
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
              <button className="btn btn-sm" disabled={readonly} onClick={async () => {
                await postVerify(cid, mode)
                setToast({ msg: "已切换复核模式", kind: "ok" })
              }}>
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
    </>
  )
}

export default function App() {
  const [nav, setNav] = useState<"main" | "bench">("main")
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="side-group">作战</div>
        <ul className="side-nav">
          <li><button className={nav === "main" ? "active" : ""} onClick={() => setNav("main")}>
            <span className="nav-icon">✦</span>星图（比赛）</button></li>
          <li><button className={nav === "bench" ? "active" : ""} onClick={() => setNav("bench")}>
            <span className="nav-icon">◈</span>Benchmark 跑分</button></li>
        </ul>
      </aside>
      <main className="content">
        {nav === "main" ? <Overview /> : <BenchPage />}
      </main>
    </div>
  )
}
