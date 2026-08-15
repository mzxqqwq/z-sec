export default function StatCard({ label, value, icon, foot, accent, spark }: {
  label: string
  value: string | number
  icon?: string
  foot?: string
  accent?: "blue" | "green" | "amber"
  spark?: number[]
}) {
  return (
    <div className={`stat-hero${accent ? ` accent-${accent}` : ""}`}>
      <div className="hero-label">
        {icon && <span className="hero-icon">{icon}</span>}
        {label}
      </div>
      <div className="hero-value">{value}</div>
      {foot && <div className="hero-foot">{foot}</div>}
      {spark && spark.length > 1 && (
        <div className="hero-spark">
          <SparklineImport data={spark} width={150} height={44}
            stroke={accent === "green" ? "#5fc89a" : accent === "amber" ? "#e8c879" : "#7d92e8"} />
        </div>
      )}
    </div>
  )
}

import SparklineImport from "./Sparkline"
