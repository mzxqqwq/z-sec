import { useEffect, useState } from "react"
import type { Summary } from "../api"

// 初赛（线上资格赛）结束时间：2026-08-21 17:00（赛程 14:00–17:00）
const RACE_DEADLINE = new Date("2026-08-21T17:00:00+08:00")

export default function Shell({ summary, kali }: { summary: Summary; kali: "ok" | "bad" | "?" }) {
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
        <span className="shell-sub">AI 夺旗 Dashboard</span>
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
