import { useEffect, useRef, useState } from "react"
import type { Board, ChallengeView } from "./api"
import {
  fetchBoard, fetchDigest, fetchLogs, fetchState,
  postConfirm, postHint, postVerify,
} from "./api"

const STATUS_LABEL: Record<string, string> = {
  new: "未开始", queued: "排队中", solving: "解题中",
  solved: "已解出", needs_hint: "待提示", dead: "已放弃",
}
const STATUS_CLASS: Record<string, string> = {
  new: "badge-gray", queued: "badge-gray", solving: "badge-blue",
  solved: "badge-green", needs_hint: "badge-orange", dead: "badge-red",
}

interface ToolEvent {
  kind: "call" | "result"
  text: string
  isError?: boolean
}

function parseToolEvents(raw: string, limit = 30): ToolEvent[] {
  const out: ToolEvent[] = []
  for (const line of raw.split("\n")) {
    const t = line.trim()
    if (!t || !t.startsWith("{")) continue
    let ev: any
    try { ev = JSON.parse(t) } catch { continue }
    if (!ev || typeof ev !== "object") continue
    if (ev.type === "tool_execution_start") {
      const args = ev.args || {}
      const cmd = String(args.command ?? args.path ?? "").slice(0, 140)
      out.push({ kind: "call", text: `${ev.toolName ?? "?"}: ${cmd}` })
    } else if (ev.type === "turn_end") {
      for (const r of ev.toolResults ?? []) {
        if (!r || typeof r !== "object") continue
        let content = r.content
        if (Array.isArray(content)) {
          content = content.map((c: any) => (c && typeof c === "object" ? c.text ?? "" : c)).join(" ")
        }
        out.push({
          kind: "result",
          text: String(content ?? "").slice(0, 200),
          isError: Boolean(r.isError),
        })
      }
    }
    if (out.length >= limit * 2) break
  }
  return out.slice(-limit)
}

function fmtElapsed(s: number): string {
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m${Math.round(s % 60)}s`
  return `${Math.floor(s / 3600)}h${Math.round((s % 3600) / 60)}m`
}

function App() {
  const [challenges, setChallenges] = useState<ChallengeView[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [error, setError] = useState("")

  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const list = await fetchState()
        if (alive) { setChallenges(list); setError("") }
      } catch (e) {
        if (alive) setError(`后端不可达：${String(e)}`)
      }
    }
    load()
    const timer = setInterval(load, 10000)
    return () => { alive = false; clearInterval(timer) }
  }, [])

  if (selected) {
    return <Detail cid={selected} onBack={() => setSelected(null)} />
  }

  return (
    <div className="page">
      <header className="topbar">
        <h1>CTF Agent 驾驶舱</h1>
        <span className="muted">10s 自动刷新 · 后端 {error ? "异常" : "在线"}</span>
      </header>
      {error && <div className="banner-error">{error}</div>}
      <table className="table">
        <thead>
          <tr>
            <th>题目</th><th>分类</th><th>状态</th><th>竞速</th><th>尝试</th>
            <th>耗时</th><th>错交</th><th>成本(¥)</th><th>复核</th>
          </tr>
        </thead>
        <tbody>
          {challenges.map((c) => (
            <tr key={c.cid} onClick={() => setSelected(c.cid)} className="row-click">
              <td>{c.name || c.cid}</td>
              <td>{c.category}</td>
              <td><span className={`badge ${STATUS_CLASS[c.status] ?? "badge-gray"}`}>
                {STATUS_LABEL[c.status] ?? c.status}</span></td>
              <td>{c.races}</td>
              <td>{c.attempts}</td>
              <td>{fmtElapsed(c.elapsed)}</td>
              <td>{c.wrong_submits}</td>
              <td title={`${(c.tokens ?? 0).toLocaleString()} tokens`}>{(c.cost ?? 0).toFixed(4)}</td>
              <td>{c.verify_required ? "复核中" : "自动"}</td>
            </tr>
          ))}
          {challenges.length === 0 && (
            <tr><td colSpan={9} className="muted">暂无题目（编排器尚未拉题）</td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

function Detail({ cid, onBack }: { cid: string; onBack: () => void }) {
  const [digest, setDigest] = useState("加载中…")
  const [logs, setLogs] = useState<ToolEvent[]>([])
  const [board, setBoard] = useState<Board>({})
  const [hint, setHint] = useState("")
  const [hintMsg, setHintMsg] = useState("")
  const [challenge, setChallenge] = useState<ChallengeView | null>(null)
  const timerRef = useRef(0)

  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const [d, raw, b] = await Promise.all([
          fetchDigest(cid), fetchLogs(cid, 300), fetchBoard(cid),
        ])
        if (!alive) return
        setDigest(d)
        setLogs(parseToolEvents(raw))
        setBoard(b)
      } catch { /* ignore */ }
      try {
        const list = await fetchState()
        if (!alive) return
        setChallenge(list.find((c) => c.cid === cid) ?? null)
      } catch { /* ignore */ }
    }
    load()
    timerRef.current = window.setInterval(load, 15000)
    return () => { alive = false; window.clearInterval(timerRef.current) }
  }, [cid])

  const sendHint = async () => {
    if (!hint.trim()) return
    const ok = await postHint(cid, hint)
    setHintMsg(ok ? "已写入，编排器下一轮注入" : "写入失败")
    setHint("")
    setTimeout(() => setHintMsg(""), 4000)
  }

  const confirm = async (flag: string) => {
    const ok = await postConfirm(cid, flag)
    setHintMsg(ok ? "已提交复核，编排器将代交" : "提交失败")
    setTimeout(() => setHintMsg(""), 4000)
  }

  return (
    <div className="page">
      <header className="topbar">
        <button onClick={onBack}>← 返回列表</button>
        <h1 style={{ marginLeft: 12 }}>{challenge?.name || cid}</h1>
        {challenge && (
          <span className={`badge ${STATUS_CLASS[challenge.status] ?? "badge-gray"}`}
            style={{ marginLeft: 8 }}>
            {STATUS_LABEL[challenge.status] ?? challenge.status}
          </span>
        )}
        <span className="muted" style={{ marginLeft: "auto" }}>15s 自动刷新</span>
      </header>

      <div className="grid">
        <section className="card">
          <h2>当前摘要（AI 翻译日志）</h2>
          <pre className="digest">{digest}</pre>
        </section>

        <section className="card">
          <h2>人工纠偏（hint）</h2>
          <textarea value={hint} onChange={(e) => setHint(e.target.value)} rows={3}
            placeholder="一次一个方向、指向具体线索，如：看 PNG 文件尾部的 base64" />
          <button onClick={sendHint}>写入提示</button>
          <span className="muted">{hintMsg}</span>
        </section>

        <section className="card">
          <h2>方向看板（Supervisor 维护）</h2>
          <div className="board-grid">
            <div>
              <h3>Ideas（待验证方向）</h3>
              <ul>
                {(board.ideas ?? []).map((i) => (
                  <li key={i.id}>
                    <span className={`idea-${i.status ?? "pending"}`}>[{i.status ?? "pending"}]</span>{" "}
                    {i.content}{i.result ? ` — ${i.result}` : ""}
                  </li>
                ))}
                {(board.ideas ?? []).length === 0 && <li className="muted">（空）</li>}
              </ul>
            </div>
            <div>
              <h3>Memory（事实/边界）</h3>
              <ul>
                {(board.memory ?? []).map((m) => (
                  <li key={m.id}><span className="muted">[{m.kind}]</span> {m.content}</li>
                ))}
                {(board.memory ?? []).length === 0 && <li className="muted">（空）</li>}
              </ul>
            </div>
          </div>
        </section>

        <section className="card">
          <h2>提交复核</h2>
          <div className="row">
            <button onClick={() => { postVerify(cid); setHintMsg("已切换复核模式"); setTimeout(() => setHintMsg(""), 4000) }}>
              {challenge?.verify_required ? "复核模式：开" : "复核模式：关"}（点击切换）
            </button>
            <span className="muted">{hintMsg}</span>
          </div>
          {(challenge?.pending_flags ?? []).map((f) => (
            <div key={f} className="row pending">
              <code>{f.slice(0, 60)}</code>
              <button onClick={() => confirm(f)}>确认提交</button>
            </div>
          ))}
          {(challenge?.pending_flags ?? []).length === 0 && (
            <p className="muted">暂无待复核候选 flag</p>
          )}
        </section>
      </div>

      <section className="card">
        <h2>最近工具调用</h2>
        <div className="logbox">
          {logs.map((e, idx) => (
            <div key={idx} className={e.kind === "result" ? (e.isError ? "log-err" : "log-res") : "log-call"}>
              {e.kind === "call" ? "▶ " : "  ↳ "}{e.text}
            </div>
          ))}
          {logs.length === 0 && <div className="muted">（无工具活动）</div>}
        </div>
      </section>
    </div>
  )
}

export default App
