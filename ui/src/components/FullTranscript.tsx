import { useEffect, useRef, useState } from "react"
import { fetchTranscript, type Mode, type Transcript, type TranscriptEntry } from "../api"

function Entry({ e }: { e: TranscriptEntry }) {
  const [open, setOpen] = useState(false)
  if (e.kind === "prompt") {
    const long = e.text.length > 140
    return (
      <div className="t-entry t-prompt">
        <span className="t-ts">{e.ts}</span>
        <span className="t-kind">指令 ▸</span>
        <span className="t-body" onClick={() => setOpen(!open)}>
          {long && !open ? e.text.slice(0, 140) + " …" : e.text}
          {long && <span className="ev-expand">{open ? " 收起" : " 展开"}</span>}
        </span>
      </div>
    )
  }
  if (e.kind === "think") {
    return (
      <div className="t-entry t-think">
        <span className="t-ts">{e.ts}</span>
        <span className="t-kind">思考</span>
        <span className="t-body">{e.text}</span>
      </div>
    )
  }
  if (e.kind === "reply") {
    return (
      <div className="t-entry t-reply">
        <span className="t-ts">{e.ts}</span>
        <span className="t-kind">回复</span>
        <span className="t-body">{e.text}</span>
      </div>
    )
  }
  if (e.kind === "call") {
    return (
      <div className="t-entry t-call">
        <span className="t-ts">{e.ts}</span>
        <span className="t-kind">▶ {e.tool ?? ""}</span>
        <span className="t-body">{e.text}</span>
      </div>
    )
  }
  return (
    <div className={`t-entry t-result${e.isError ? " t-err" : ""}`}>
      <span className="t-ts">{e.ts}</span>
      <span className="t-kind">{e.isError ? "✗" : "↳"} {e.tool ?? ""}</span>
      <span className="t-body">{e.text}</span>
    </div>
  )
}

export default function FullTranscript({ cid, mode, runId, sessionId }: {
  cid: string
  mode: Mode
  runId?: string
  sessionId?: string
}) {
  const [data, setData] = useState<Transcript>({ cid, workers: 0, worker: 0, worker_files: [], entries: [] })
  const [worker, setWorker] = useState(0)
  const [auto, setAuto] = useState(true)
  const bodyRef = useRef<HTMLDivElement>(null)
  const timerRef = useRef(0)

  useEffect(() => {
    let alive = true
    const load = async () => {
      const t = await fetchTranscript(cid, worker, 800, mode, runId, sessionId)
      if (!alive) return
      setData(t)
    }
    load()
    timerRef.current = window.setInterval(load, 15000)
    return () => { alive = false; window.clearInterval(timerRef.current) }
  }, [cid, worker, mode, runId, sessionId])

  useEffect(() => {
    if (auto && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight
    }
  }, [data, auto])

  return (
    <div>
      <div className="t-head">
        <div className="t-tabs">
          {data.workers === 0 && <span className="muted">暂无 worker 记录</span>}
          {data.worker_files.map((f, i) => (
            <button key={f} className={`btn btn-sm ${i === worker ? "btn-primary" : ""}`}
              onClick={() => setWorker(i)} title={f}>
              {i === 0 ? "强 worker" : `弱 worker ${i}`}
            </button>
          ))}
        </div>
        <label className="ev-auto" onClick={() => setAuto(!auto)}>
          <span className={`dot ${auto ? "ok" : ""}`} style={{ width: 6, height: 6 }} />
          {auto ? "自动滚动" : "已暂停"}
        </label>
      </div>
      <div className="transcript" ref={bodyRef}>
        {data.entries.map((e, i) => <Entry key={i} e={e} />)}
        {data.entries.length === 0 && (
          <div className="empty"><div className="empty-star">✦</div>等待 worker 第一步动作</div>
        )}
      </div>
    </div>
  )
}
