import { useEffect, useRef, useState } from "react"

export interface ToolEvent {
  kind: "call" | "result"
  text: string
  isError?: boolean
  ts?: string
}

export function parseToolEvents(raw: string, limit = 60): ToolEvent[] {
  const out: ToolEvent[] = []
  const lines = raw.split("\n")
  for (const line of lines) {
    const t = line.trim()
    if (!t || !t.startsWith("{")) continue
    let ev: any
    try { ev = JSON.parse(t) } catch { continue }
    if (!ev || typeof ev !== "object") continue
    const ts = typeof ev.timestamp === "string" ? ev.timestamp.slice(11, 19) : ""
    if (ev.type === "tool_execution_start") {
      const args = ev.args || {}
      const cmd = String(args.command ?? args.path ?? "").slice(0, 200)
      out.push({ kind: "call", text: `${ev.toolName ?? "?"}: ${cmd}`, ts })
    } else if (ev.type === "turn_end") {
      for (const r of ev.toolResults ?? []) {
        if (!r || typeof r !== "object") continue
        let content = r.content
        if (Array.isArray(content)) {
          content = content.map((c: any) => (c && typeof c === "object" ? c.text ?? "" : c)).join(" ")
        }
        out.push({
          kind: "result",
          text: String(content ?? "").slice(0, 300),
          isError: Boolean(r.isError),
          ts,
        })
      }
    }
    if (out.length >= limit * 2) break
  }
  return out.slice(-limit)
}

export default function EventStream({ events }: { events: ToolEvent[] }) {
  const [expanded, setExpanded] = useState<Record<number, boolean>>({})
  const [auto, setAuto] = useState(true)
  const bodyRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (auto && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight
    }
  }, [events, auto])

  return (
    <div>
      <div className="ev-stream-head">
        <span className="muted">{events.length} 条工具事件</span>
        <label className="ev-auto" onClick={() => setAuto(!auto)}>
          <span className={`dot ${auto ? "ok" : ""}`} style={{ width: 6, height: 6 }} />
          {auto ? "自动滚动" : "已暂停"}
        </label>
      </div>
      <div className="ev-stream" ref={bodyRef}>
        {events.map((e, i) => {
          const long = e.text.length > 140
          const show = expanded[i] ?? false
          return (
            <div key={i} className={`ev-line ${e.kind === "result" ? (e.isError ? "ev-err" : "ev-result") : "ev-call"}`}>
              <span className="ev-ts">{e.ts ?? ""}</span>
              <span className="ev-body">{e.kind === "call" ? "▶ " : e.isError ? "✗ " : "↳ "}
                {long && !show ? e.text.slice(0, 140) + "… " : e.text}
                {long && (
                  <span className="ev-expand" onClick={() => setExpanded({ ...expanded, [i]: !show })}>
                    {show ? "收起" : "展开"}
                  </span>
                )}
              </span>
            </div>
          )
        })}
        {events.length === 0 && <div className="empty"><div className="empty-star">✦</div>等待 worker 第一次动作</div>}
      </div>
    </div>
  )
}
