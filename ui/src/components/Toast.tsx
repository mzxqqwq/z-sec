import { useEffect, useState } from "react"

export default function Toast({ msg, kind }: { msg: string; kind?: "ok" | "err" }) {
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    if (!msg) return
    setVisible(true)
    const t = setTimeout(() => setVisible(false), 4000)
    return () => clearTimeout(t)
  }, [msg])
  if (!msg || !visible) return null
  return <div className={`toast ${kind ?? ""}`}>{msg}</div>
}
