import { useEffect, useState } from "react"
import { fetchConfig, saveConfig } from "../api"
import type { AgentConfig, ProviderInfo } from "../api"
import GlassCard from "./GlassCard"
import Shell from "./Shell"

const THINKING_LABEL: Record<string, string> = { low: "低", medium: "中", high: "高" }
const ROLE_LABEL: Record<string, string> = {
  strong: "强 worker（解题主力）",
  weak: "弱 worker（竞速陪跑）",
  planner: "Planner（解题思路）",
  observer: "Supervisor（看板维护）",
  digest: "digest（日志摘要）",
}
const ROLE_ICON: Record<string, string> = {
  strong: "✦", weak: "·", planner: "◈", observer: "☽", digest: "❖",
}
const newProvider = (): ProviderInfo => ({ id: "", label: "", base_url: "", models: [] })

export default function ConfigPage({ toast }: {
  toast: (msg: string, kind?: "ok" | "err") => void
}) {
  const [cfg, setCfg] = useState<AgentConfig | null>(null)
  const [keys, setKeys] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)

  const load = async () => {
    const c = await fetchConfig()
    if (c) { setCfg(c); setKeys({}) }
  }
  useEffect(() => { void load() }, [])

  if (!cfg) {
    return (
      <>
        <Shell summary={{ solved: 0, solving: 0, needs_hint: 0, total: 0, cost: 0, tokens: 0 }} kali="?" />
        <div className="page"><p className="empty"><span className="empty-star">⚙</span><br />加载配置中…</p></div>
      </>
    )
  }

  const patchProvider = (idx: number, field: keyof ProviderInfo, value: string) => {
    const providers = cfg.providers.map((p, i) =>
      i === idx ? { ...p, [field]: field === "models" ? value.split(",").map((s) => s.trim()).filter(Boolean) : value } : p)
    setCfg({ ...cfg, providers })
  }
  const removeProvider = (idx: number) => setCfg({ ...cfg, providers: cfg.providers.filter((_, i) => i !== idx) })
  const addProvider = () => setCfg({ ...cfg, providers: [...cfg.providers, newProvider()] })

  const patchLlm = (role: string, field: "model" | "thinking", value: string) => {
    const llm = { ...cfg.llm, [role]: { ...(cfg.llm[role] ?? {}), [field]: value } }
    setCfg({ ...cfg, llm })
  }

  // ---- cc-switch 移植：预设添加 + 一键应用到角色 ----
  const metaOf = (mid: string) => {
    if (!mid) return null
    if (cfg.catalog[mid]) return cfg.catalog[mid]
    const hit = Object.entries(cfg.catalog).find(([k]) => k.endsWith("/" + mid))
    return hit ? hit[1] : null
  }
  const uniqueId = (base: string) => {
    let id = base, n = 2
    while (cfg.providers.some((p) => p.id === id)) id = `${base}-${n++}`
    return id
  }
  const addPreset = (preset: { id: string; name: string; base_url: string; models: string[] }) => {
    setCfg({
      ...cfg,
      providers: [...cfg.providers,
      { id: uniqueId(preset.id), label: preset.name, base_url: preset.base_url, models: preset.models }],
    })
  }
  const applyRoles = (models: string[]) => {
    const mids = models.filter(Boolean)
    if (mids.length === 0) { toast("该 provider 没有模型，先填写模型列表", "err"); return }
    const reasoning = mids.filter((m) => metaOf(m)?.reasoning)
    const heavy = reasoning[0] ?? mids[0]
    setCfg({
      ...cfg,
      llm: {
        ...cfg.llm,
        strong: { ...cfg.llm.strong, model: heavy },
        weak: { ...cfg.llm.weak, model: mids[0] },
        planner: { ...cfg.llm.planner, model: heavy },
        observer: { ...cfg.llm.observer, model: heavy },
        digest: { ...cfg.llm.digest, model: mids[0] },
      },
    })
    toast(`已应用：strong/planner/observer → ${heavy}，weak/digest → ${mids[0]}`, "ok")
  }

  const save = async () => {
    setSaving(true)
    const body: Parameters<typeof saveConfig>[0] = {
      llm: cfg.llm,
      runtime: cfg.runtime,
      providers: cfg.providers,
      api_keys: Object.fromEntries(Object.entries(keys).filter(([, v]) => v.trim() !== "")),
    }
    const r = await saveConfig(body)
    setSaving(false)
    toast(r.msg, r.ok ? "ok" : "err")
    if (r.ok) void load()
  }

  return (
    <>
      <Shell summary={{ solved: 0, solving: 0, needs_hint: 0, total: 0, cost: 0, tokens: 0 }} kali="?" />
      <div className="page">
        <div className="hero-grid" style={{ gridTemplateColumns: "1fr" }}>
          <div className="stat-hero">
            <div className="hero-label"><span className="hero-icon">⚙</span>统一配置</div>
            <div className="hero-value" style={{ fontSize: 22 }}>LLM 角色 · Providers · 密钥</div>
            <div className="hero-foot">
              写入 config/agent.json 与 config/secrets.json（secrets 不上传 GitHub）· 保存后新跑分生效
            </div>
          </div>
        </div>

        <GlassCard title="Providers（API 中转站 / 自定义端点）" actions={
          <div style={{ display: "flex", gap: 6 }}>
            {cfg.presets.map((p) => (
              <button key={p.id} className="btn btn-sm" onClick={() => addPreset(p)}>＋ {p.name}</button>
            ))}
            <button className="btn btn-sm" onClick={addProvider}>＋ 空白 Provider</button>
          </div>
        }>
          {cfg.providers.map((p, idx) => (
            <div key={idx} className="cfg-provider">
              <div className="cfg-provider-head">
                <span className="star-dot" style={{ background: "var(--stellar)" }} />
                <span className="cfg-name">{p.label || p.id || "（未命名）"}</span>
                <span className="cfg-url" title={p.base_url}>{p.base_url || "未设 base_url"}</span>
                <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                  <button className="btn btn-sm" onClick={() => applyRoles(p.models)}
                    title="按该 provider 的模型给五个角色分配合适模型">应用到角色</button>
                  <button className="btn btn-sm btn-danger" onClick={() => removeProvider(idx)}>删除</button>
                </div>
              </div>
              <div className="cfg-row">
                <label>id</label>
                <input className="input" style={{ width: 150, fontFamily: "var(--font-mono)" }}
                  placeholder="myrelay" value={p.id}
                  onChange={(e) => patchProvider(idx, "id", e.target.value.toLowerCase())} />
                <label style={{ width: "auto" }}>名称</label>
                <input className="input" style={{ width: 150 }}
                  placeholder="我的中转站" value={p.label}
                  onChange={(e) => patchProvider(idx, "label", e.target.value)} />
                <label style={{ width: "auto" }}>base_url</label>
                <input className="input" style={{ flex: 1, fontFamily: "var(--font-mono)" }}
                  placeholder="https://你的中转站/v1" value={p.base_url}
                  onChange={(e) => patchProvider(idx, "base_url", e.target.value)} />
              </div>
              <div className="cfg-row" style={{ marginBottom: 0 }}>
                <label>模型 id（逗号分隔）</label>
                <input className="input" style={{ flex: 1, fontFamily: "var(--font-mono)" }}
                  placeholder="gpt-5.6-sol, claude-sonnet-4.6, deepseek-v4-pro"
                  value={p.models.join(", ")}
                  onChange={(e) => patchProvider(idx, "models", e.target.value)} />
              </div>
            </div>
          ))}
          <p className="cfg-hint">
            自定义中转站请用<b>自定义 id</b>（如 myrelay）：保存时自动合并进 pi 模型注册表
            （%USERPROFILE%\.pi\agent\models.json），其模型立即可在下方「角色模型」里选择；
            deepseek/openai/anthropic 等内置 id 由 pi 运行时自带元数据管理，不会写入注册表。
          </p>
        </GlassCard>

        <GlassCard title="API Key（provider 密钥）">
          <div className="cfg-grid">
            {cfg.providers.filter((p) => p.id).map((p) => (
              <div key={p.id} className="cfg-key">
                <div className="cfg-key-head">
                  <span className={`dot ${cfg.keys[p.id] ? "ok" : "bad"}`} />
                  <span className="cfg-name">{p.label || p.id}</span>
                </div>
                <input className="input" type="password" style={{ fontFamily: "var(--font-mono)" }}
                  placeholder={cfg.keys[p.id] ? "已设置（留空保持不变）" : "未设置"}
                  value={keys[p.id] ?? ""}
                  onChange={(e) => setKeys({ ...keys, [p.id]: e.target.value })} />
                <div className="cfg-hint">{p.base_url || "（未设 url）"}</div>
              </div>
            ))}
            {cfg.providers.filter((p) => p.id).length === 0 && (
              <p className="muted">先在上方添加至少一个带 id 的 provider。</p>
            )}
          </div>
          <p className="cfg-hint">保存后立即写入 secrets.json 并注入当前进程环境；新开 worker/评测进程自动继承。</p>
        </GlassCard>

        <GlassCard title="角色模型（strong / weak / planner / observer / digest）">
          {Object.entries(ROLE_LABEL).map(([role, label]) => {
            const v = cfg.llm[role] ?? { model: "" }
            return (
              <div key={role} className="cfg-row">
                <label><span style={{ marginRight: 6, color: "var(--stellar-bright)" }}>{ROLE_ICON[role]}</span>{label}</label>
                <select className="input" style={{ flex: 1 }}
                  value={v.model} onChange={(e) => patchLlm(role, "model", e.target.value)}>
                  <option value="" disabled>选择模型…</option>
                  {cfg.providers.filter((p) => p.id).map((p) => (
                    <optgroup key={p.id} label={`${p.label || p.id}（${p.base_url || "未设 url"}）`}>
                      {p.models.map((m) => <option key={m} value={m}>{m}</option>)}
                    </optgroup>
                  ))}
                </select>
                {(role === "strong" || role === "weak" || role === "observer") && (
                  <select className="input" style={{ width: 100 }}
                    value={v.thinking ?? "medium"}
                    onChange={(e) => patchLlm(role, "thinking", e.target.value)}>
                    {Object.entries(THINKING_LABEL).map(([t, l]) => (
                      <option key={t} value={t}>思考·{l}</option>
                    ))}
                  </select>
                )}
              </div>
            )
          })}
        </GlassCard>

        <GlassCard title="运行时开关">
          <div className="cfg-row">
            <label>并发题数上限</label>
            <input className="input" type="number" min={1} max={8} style={{ width: 100 }}
              value={cfg.runtime.max_parallel_challenges}
              onChange={(e) => setCfg({ ...cfg, runtime: { ...cfg.runtime, max_parallel_challenges: Number(e.target.value) || 3 } })} />
            <span className="cfg-hint" style={{ margin: 0 }}>同时开打的题目数（受本机性能/API 限流约束）</span>
          </div>
          {(["planning_enabled", "supervisor_enabled", "kb_enabled"] as const).map((k) => (
            <div key={k} className="cfg-row">
              <label>{k}</label>
              <button className={`btn btn-sm ${cfg.runtime[k] ? "btn-success" : ""}`}
                onClick={() => setCfg({ ...cfg, runtime: { ...cfg.runtime, [k]: !cfg.runtime[k] } })}>
                {cfg.runtime[k] ? "开启" : "关闭"}
              </button>
            </div>
          ))}
          <p className="cfg-hint">改动对下次启动的跑分/编排器生效（看板进程内的 digest 立即生效）。</p>
        </GlassCard>

        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <button className="btn btn-primary" disabled={saving} onClick={save}>
            {saving ? "保存中…" : "保存配置"}</button>
          <span className="muted">改动只落本地 config/ 目录，重启看板不丢</span>
        </div>
      </div>
    </>
  )
}
