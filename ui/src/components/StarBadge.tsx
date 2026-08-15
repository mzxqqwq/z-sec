export const STATUS_LABEL: Record<string, string> = {
  new: "未触及", queued: "排队中", solving: "解题中",
  solved: "已夺取", needs_hint: "待提示", dead: "已放弃",
}

export default function StarBadge({ status }: { status: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
      title={STATUS_LABEL[status] ?? status}>
      <span className={`star-dot ${status}`} />
      <span className="muted" style={{ fontSize: 11.5 }}>{STATUS_LABEL[status] ?? status}</span>
    </span>
  )
}

export function CategoryBadge({ category }: { category: string }) {
  const c = (category || "misc").toLowerCase()
  const cls = c === "crypto" ? "cat-crypto" : c === "pwn" ? "cat-pwn" :
    c === "web" ? "cat-web" : c === "rev" || c === "reverse" ? "cat-rev" : "cat-misc"
  return <span className={`cat-badge ${cls}`}>{category || "?"}</span>
}
