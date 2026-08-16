import { useEffect, useState } from "react"
import { fetchConfig, saveConfig } from "../api"
import type { AgentConfig, ProviderInfo } from "../api"
import GlassCard from "./GlassCard"

const THINKING_LABEL: Record<string, string> = { low: "低", medium: "中", high: "高" }
const ROLE_LABEL: Record<string, string> = {
  strong: "强 worker（解题主力）",
  weak: "弱 worker（竞速陪跑）",
  planner: "Planner（解题思路）",
  observer: "Supervisor（看板维护）",
  digest: "digest（日志摘要）",
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

  if (!cfg) return <p className="muted">加载配置中…</p>

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
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, margin: "6px 0 14px" }}>
        <span className="panel-card-title" style={{ fontSize: 16 }}>⚙ 统一配置</span>
        <span className="muted" style={{ fontSize: 12 }}>
          写入 config/agent.json 与 config/secrets.json（secrets 不上传 GitHub）
        </span>
      </div>

      <GlassCard title="Providers（API 中转站/自定义端点都加在这里）">
        {cfg.providers.map((p, idx) => (
          <div key={idx} className="pending-row" style={{ marginBottom: 10, alignItems: "flex-start" }}>
            <span style={{ width: 90 }}>
              <input placeholder="id" value={p.id} style={{ width: "100%", fontFamily: "var(--font-mono)" }}
                onChange={(e) => patchProvider(idx, "id", e.target.value.toLowerCase())} />
            </span>
            <span style={{ width: 110 }}>
              <input placeholder="label" value={p.label} style={{ width: "100%" }}
                onChange={(e) => patchProvider(idx, "label", e.target.value)} />
            </span>
            <span style={{ flex: 1 }}>
              <input placeholder="https://你的中转站/v1" value={p.base_url} style={{ width: "100%", fontFamily: "var(--font-mono)" }}
                onChange={(e) => patchProvider(idx, "base_url", e.target.value)} />
            </span>
            <span style={{ flex: 1.4 }}>
              <input placeholder="模型 id，逗号分隔（如 gpt-4o, claude-sonnet-4）"
                value={p.models.join(", ")} style={{ width: "100%", fontFamily: "var(--font-mono)" }}
                onChange={(e) => patchProvider(idx, "models", e.target.value)} />
            </span>
            <button className="btn btn-sm" onClick={() => removeProvider(idx)} title="删除该 provider">✕</button>
          </div>
        ))}
        <button className="btn btn-sm" onClick={addProvider}>＋ 新增 Provider</button>
        <p className="muted" style={{ fontSize: 11, marginTop: 8 }}>
          自定义中转站请用<b>自定义 id</b>（如 myrelay），保存时自动合并进 pi 模型注册表
          （%USERPROFILE%\.pi\agent\models.json），其模型立即可在下方「角色模型」里选择。
          deepseek/openai/anthropic 等内置 id 由 pi 运行时自带元数据管理，不会写入注册表。
        </p>
      </GlassCard>

      <GlassCard title="API Key（provider 密钥）">
        {cfg.providers.filter((p) => p.id).map((p) => (
          <div key={p.id} className="pending-row" style={{ marginBottom: 10 }}>
            <span style={{ width: 140 }}><b>{p.label || p.id}</b><br />
              <span className="muted" style={{ fontSize: 11 }}>{p.base_url || "(未设 url)"}</span></span>
            <span style={{ flex: 1 }}>
              <input type="password"
                placeholder={cfg.keys[p.id] ? "已设置（留空保持不变）" : "未设置"}
                value={keys[p.id] ?? ""}
                onChange={(e) => setKeys({ ...keys, [p.id]: e.target.value })}
                style={{ width: "100%", fontFamily: "var(--font-mono)" }} />
            </span>
            <span className={`dot ${cfg.keys[p.id] ? "ok" : "bad"}`}
              title={cfg.keys[p.id] ? "已配置" : "未配置"} />
          </div>
        ))}
        {cfg.providers.filter((p) => p.id).length === 0 && (
          <p className="muted">先在上面添加至少一个带 id 的 provider。</p>
        )}
        <p className="muted" style={{ fontSize: 11 }}>
          保存后立即写入 secrets.json 并注入当前进程环境；新开 worker/评测进程自动继承。
        </p>
      </GlassCard>

      <GlassCard title="角色模型（strong/weak/planner/observer/digest）">
        {Object.entries(ROLE_LABEL).map(([role, label]) => {
          const v = cfg.llm[role] ?? { model: "" }
          return (
            <div key={role} className="pending-row" style={{ marginBottom: 10 }}>
              <span style={{ width: 200 }}>{label}</span>
              <select value={v.model} style={{ flex: 1, marginRight: 8 }}
                onChange={(e) => patchLlm(role, "model", e.target.value)}>
                <option value="" disabled>选择模型…</option>
                {cfg.providers.filter((p) => p.id).map((p) => (
                  <optgroup key={p.id} label={`${p.label || p.id}（${p.base_url}）`}>
                    {p.models.map((m) => <option key={m} value={m}>{m}</option>)}
                  </optgroup>
                ))}
              </select>
              {(role === "strong" || role === "weak" || role === "observer") && (
                <select value={v.thinking ?? "medium"} style={{ width: 90 }}
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
        <div className="pending-row" style={{ marginBottom: 10 }}>
          <span style={{ width: 200 }}>并发题数上限</span>
          <input type="number" min={1} max={8}
            value={cfg.runtime.max_parallel_challenges}
            onChange={(e) => setCfg({ ...cfg, runtime: { ...cfg.runtime, max_parallel_challenges: Number(e.target.value) || 3 } })}
            style={{ width: 90 }} />
        </div>
        {(["planning_enabled", "supervisor_enabled", "kb_enabled"] as const).map((k) => (
          <div key={k} className="pending-row" style={{ marginBottom: 10 }}>
            <span style={{ width: 200 }}>{k}</span>
            <button className="btn btn-sm"
              onClick={() => setCfg({ ...cfg, runtime: { ...cfg.runtime, [k]: !cfg.runtime[k] } })}>
              {cfg.runtime[k] ? "开" : "关"}
            </button>
          </div>
        ))}
        <p className="muted" style={{ fontSize: 11 }}>
          改动对下次启动的跑分/编排器生效（看板进程内的 digest 会立即生效）。
        </p>
      </GlassCard>

      <div style={{ marginTop: 12 }}>
        <button className="btn" disabled={saving} onClick={save}>
          {saving ? "保存中…" : "保存配置"}
        </button>
      </div>
    </>
  )
}
