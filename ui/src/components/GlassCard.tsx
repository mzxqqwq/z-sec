import type { ReactNode } from "react"

export default function GlassCard({ title, actions, children, warn }: {
  title?: string
  actions?: ReactNode
  children: ReactNode
  warn?: boolean
}) {
  return (
    <section className={`panel-card${warn ? " warn" : ""}`}>
      {(title || actions) && (
        <header className="panel-card-head">
          {title && <h3 className="panel-card-title">{title}</h3>}
          {actions && <div className="panel-card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  )
}
