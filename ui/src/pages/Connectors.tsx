import { useState, useEffect, useCallback } from 'react'
import {
  Plus, RefreshCw, Trash2, ToggleLeft, ToggleRight, Zap, ChevronDown,
  CheckCircle2, XCircle, Clock, AlertCircle, Search, X, Settings,
} from 'lucide-react'
import { useAuth } from '../auth/AuthContext'

const API = '/api/v1'

// ── Types ────────────────────────────────────────────────────────────────────

interface Connector {
  name: string
  type: string
  display_name: string
  icon: string
  category: string
  description: string
  available_actions: string[]
  config: Record<string, string>
  enabled: boolean
  status: string | null
  last_error: string | null
}

interface CatalogEntry {
  type: string
  display_name: string
  icon: string
  category: string
  description: string
  auth_type: string
  required_config: string[]
  optional_config: string[]
  actions: { name: string; description: string }[]
}

// ── Helpers ──────────────────────────────────────────────────────────────────

const STATUS_BADGE: Record<string, { label: string; cls: string; icon: JSX.Element }> = {
  connected: {
    label: 'Connected',
    cls: 'bg-emerald-500/10 text-emerald-400 ring-1 ring-emerald-500/20',
    icon: <CheckCircle2 size={11} />,
  },
  error: {
    label: 'Error',
    cls: 'bg-red-500/10 text-red-400 ring-1 ring-red-500/20',
    icon: <XCircle size={11} />,
  },
  pending: {
    label: 'Pending',
    cls: 'bg-amber-500/10 text-amber-400 ring-1 ring-amber-500/20',
    icon: <Clock size={11} />,
  },
}

function StatusBadge({ status }: { status: string | null }) {
  const s = STATUS_BADGE[status ?? ''] ?? {
    label: 'Unknown',
    cls: 'bg-slate-500/10 text-slate-400 ring-1 ring-slate-500/20',
    icon: <AlertCircle size={11} />,
  }
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium ${s.cls}`}>
      {s.icon} {s.label}
    </span>
  )
}

// ── Add Connector Modal ───────────────────────────────────────────────────────

function AddConnectorModal({
  catalog,
  onClose,
  onCreated,
  authHeader,
}: {
  catalog: CatalogEntry[]
  onClose: () => void
  onCreated: () => void
  authHeader: string
}) {
  const [selected, setSelected] = useState<CatalogEntry | null>(null)
  const [name, setName] = useState('')
  const [config, setConfig] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const categories = [...new Set(catalog.map(c => c.category))]

  function handleSelect(entry: CatalogEntry) {
    setSelected(entry)
    setName(entry.type)
    const defaults: Record<string, string> = {}
    for (const k of entry.required_config) defaults[k] = ''
    for (const k of entry.optional_config) defaults[k] = ''
    setConfig(defaults)
    setError('')
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!selected) return
    setSaving(true)
    setError('')
    try {
      const res = await fetch(`${API}/connectors`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: authHeader },
        body: JSON.stringify({ name, type: selected.type, config }),
      })
      if (!res.ok) {
        const d = await res.json().catch(() => ({}))
        throw new Error(d.detail ?? `HTTP ${res.status}`)
      }
      onCreated()
      onClose()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-2xl mx-4 card p-0 overflow-hidden max-h-[90vh] flex flex-col">
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.06]">
          <h2 className="text-white font-semibold">Add Connector</h2>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-300 transition-colors">
            <X size={18} />
          </button>
        </div>

        <div className="flex flex-1 overflow-hidden">
          {/* Catalog browser */}
          <div className="w-56 border-r border-white/[0.06] overflow-y-auto">
            {categories.map(cat => (
              <div key={cat}>
                <p className="px-4 pt-4 pb-1 text-[10px] font-semibold text-slate-600 uppercase tracking-widest">
                  {cat}
                </p>
                {catalog.filter(c => c.category === cat).map(entry => (
                  <button
                    key={entry.type}
                    onClick={() => handleSelect(entry)}
                    className={`w-full text-left px-4 py-2.5 flex items-center gap-3 transition-colors text-sm
                      ${selected?.type === entry.type
                        ? 'bg-cyan-500/10 text-cyan-400 border-l-2 border-cyan-500'
                        : 'text-slate-400 hover:bg-white/[0.04] hover:text-slate-300 border-l-2 border-transparent'
                      }`}
                  >
                    <span className="text-base leading-none">{entry.icon}</span>
                    <span className="font-medium truncate">{entry.display_name}</span>
                  </button>
                ))}
              </div>
            ))}
          </div>

          {/* Config form */}
          <div className="flex-1 overflow-y-auto p-6">
            {!selected ? (
              <div className="h-full flex flex-col items-center justify-center text-center">
                <Settings size={32} className="text-slate-700 mb-3" />
                <p className="text-slate-500 text-sm">Select a connector type from the list</p>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-2xl">{selected.icon}</span>
                    <h3 className="text-white font-semibold">{selected.display_name}</h3>
                  </div>
                  <p className="text-slate-500 text-xs">{selected.description}</p>
                </div>

                <div>
                  <label className="block text-xs text-slate-400 mb-1">Connector Name</label>
                  <input
                    value={name}
                    onChange={e => setName(e.target.value)}
                    required
                    className="w-full bg-slate-800/60 border border-white/[0.08] rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-cyan-500/50 focus:bg-slate-800"
                    placeholder="e.g. my-github"
                  />
                  <p className="mt-1 text-[11px] text-slate-600">
                    Used to reference this connector in agent specs
                  </p>
                </div>

                {Object.keys(config).length > 0 && (
                  <div className="space-y-3">
                    <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
                      Configuration
                    </p>
                    {selected.required_config.map(k => (
                      <div key={k}>
                        <label className="block text-xs text-slate-400 mb-1">
                          {k} <span className="text-red-400">*</span>
                        </label>
                        <input
                          value={config[k] ?? ''}
                          onChange={e => setConfig(c => ({ ...c, [k]: e.target.value }))}
                          required
                          placeholder={`Enter ${k} or \${ENV_VAR}`}
                          className="w-full bg-slate-800/60 border border-white/[0.08] rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-cyan-500/50 font-mono"
                        />
                      </div>
                    ))}
                    {selected.optional_config.map(k => (
                      <div key={k}>
                        <label className="block text-xs text-slate-400 mb-1">{k}</label>
                        <input
                          value={config[k] ?? ''}
                          onChange={e => setConfig(c => ({ ...c, [k]: e.target.value }))}
                          placeholder={`Enter ${k} (optional)`}
                          className="w-full bg-slate-800/60 border border-white/[0.08] rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-cyan-500/50 font-mono"
                        />
                      </div>
                    ))}
                  </div>
                )}

                {error && (
                  <p className="text-red-400 text-xs bg-red-500/10 rounded-lg px-3 py-2 border border-red-500/20">
                    {error}
                  </p>
                )}

                <div className="flex justify-end gap-2 pt-2">
                  <button type="button" onClick={onClose} className="btn btn-secondary text-sm">
                    Cancel
                  </button>
                  <button type="submit" disabled={saving} className="btn btn-primary text-sm">
                    {saving ? 'Creating…' : 'Create Connector'}
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Connector Card ────────────────────────────────────────────────────────────

function ConnectorCard({
  connector,
  authHeader,
  onRefresh,
}: {
  connector: Connector
  authHeader: string
  onRefresh: () => void
}) {
  const [testing, setTesting] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [toggling, setToggling] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; error?: string } | null>(null)

  async function handleTest() {
    setTesting(true)
    setTestResult(null)
    try {
      const res = await fetch(`${API}/connectors/${connector.name}/test`, {
        method: 'POST',
        headers: { Authorization: authHeader },
      })
      const d = await res.json()
      setTestResult(d)
      onRefresh()
    } catch {
      setTestResult({ ok: false, error: 'Network error' })
    } finally {
      setTesting(false)
    }
  }

  async function handleToggle() {
    setToggling(true)
    try {
      await fetch(`${API}/connectors/${connector.name}/enable`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', Authorization: authHeader },
        body: JSON.stringify({ enabled: !connector.enabled }),
      })
      onRefresh()
    } finally {
      setToggling(false)
    }
  }

  async function handleDelete() {
    if (!confirm(`Delete connector "${connector.name}"? This cannot be undone.`)) return
    setDeleting(true)
    try {
      await fetch(`${API}/connectors/${connector.name}`, {
        method: 'DELETE',
        headers: { Authorization: authHeader },
      })
      onRefresh()
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className={`card p-0 overflow-hidden transition-all duration-200 ${!connector.enabled ? 'opacity-60' : ''}`}>
      <div className="px-5 py-4 flex items-start gap-4">
        <div className="w-10 h-10 rounded-xl bg-slate-800 border border-white/[0.08] flex items-center justify-center text-xl flex-shrink-0 shadow-inner">
          {connector.icon}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-white font-semibold text-sm">{connector.name}</span>
            <span className="text-slate-600 text-xs font-mono">({connector.type})</span>
            <StatusBadge status={connector.status} />
            {!connector.enabled && (
              <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium bg-slate-700/50 text-slate-500 ring-1 ring-slate-600/30">
                Disabled
              </span>
            )}
          </div>
          <p className="text-slate-500 text-xs mt-0.5 truncate">{connector.description}</p>
          {connector.last_error && (
            <p className="text-red-400 text-[11px] mt-1 bg-red-500/5 rounded px-2 py-1 border border-red-500/10">
              {connector.last_error}
            </p>
          )}
        </div>
        <div className="flex items-center gap-1.5 flex-shrink-0">
          <button
            onClick={handleTest}
            disabled={testing}
            title="Test connection"
            className="p-1.5 rounded-lg text-slate-500 hover:text-cyan-400 hover:bg-cyan-500/10 transition-colors disabled:opacity-50"
          >
            <Zap size={14} className={testing ? 'animate-pulse' : ''} />
          </button>
          <button
            onClick={handleToggle}
            disabled={toggling}
            title={connector.enabled ? 'Disable' : 'Enable'}
            className="p-1.5 rounded-lg text-slate-500 hover:text-amber-400 hover:bg-amber-500/10 transition-colors"
          >
            {connector.enabled
              ? <ToggleRight size={14} className="text-emerald-400" />
              : <ToggleLeft size={14} />
            }
          </button>
          <button
            onClick={handleDelete}
            disabled={deleting}
            title="Delete"
            className="p-1.5 rounded-lg text-slate-500 hover:text-red-400 hover:bg-red-500/10 transition-colors disabled:opacity-50"
          >
            <Trash2 size={14} />
          </button>
          <button
            onClick={() => setExpanded(e => !e)}
            className="p-1.5 rounded-lg text-slate-500 hover:text-slate-300 hover:bg-white/[0.05] transition-colors"
          >
            <ChevronDown size={14} className={`transition-transform ${expanded ? 'rotate-180' : ''}`} />
          </button>
        </div>
      </div>

      {testResult && (
        <div className={`mx-5 mb-3 px-3 py-2 rounded-lg text-xs border ${
          testResult.ok
            ? 'bg-emerald-500/5 text-emerald-400 border-emerald-500/20'
            : 'bg-red-500/5 text-red-400 border-red-500/20'
        }`}>
          {testResult.ok ? '✓ Connection successful' : `✗ ${testResult.error ?? 'Connection failed'}`}
        </div>
      )}

      {expanded && connector.available_actions.length > 0 && (
        <div className="px-5 pb-4 border-t border-white/[0.04] pt-3">
          <p className="text-[10px] font-semibold text-slate-600 uppercase tracking-widest mb-2">
            Available Actions
          </p>
          <div className="flex flex-wrap gap-1.5">
            {connector.available_actions.map(a => (
              <span
                key={a}
                className="px-2 py-0.5 rounded text-[11px] font-mono bg-slate-800 text-slate-400 border border-white/[0.06]"
              >
                {connector.type}__{a}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Catalog Browser ───────────────────────────────────────────────────────────

function CatalogBrowser({
  catalog,
  onConfigure,
}: {
  catalog: CatalogEntry[]
  onConfigure: (entry: CatalogEntry) => void
}) {
  const [search, setSearch] = useState('')
  const [cat, setCat] = useState('all')
  const categories = ['all', ...new Set(catalog.map(c => c.category))]

  const filtered = catalog.filter(e => {
    const q = search.toLowerCase()
    return (
      (cat === 'all' || e.category === cat) &&
      (e.display_name.toLowerCase().includes(q) || e.description.toLowerCase().includes(q))
    )
  })

  return (
    <div>
      <div className="flex gap-2 mb-4">
        <div className="relative flex-1">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-600" />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search catalog…"
            className="w-full bg-slate-800/60 border border-white/[0.08] rounded-lg pl-8 pr-3 py-2 text-sm text-slate-300 placeholder:text-slate-600 focus:outline-none focus:border-cyan-500/50"
          />
        </div>
        <select
          value={cat}
          onChange={e => setCat(e.target.value)}
          className="bg-slate-800/60 border border-white/[0.08] rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none focus:border-cyan-500/50"
        >
          {categories.map(c => (
            <option key={c} value={c} className="bg-slate-900">
              {c === 'all' ? 'All Categories' : c.charAt(0).toUpperCase() + c.slice(1)}
            </option>
          ))}
        </select>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {filtered.map(entry => (
          <div
            key={entry.type}
            className="card p-4 flex items-start gap-3 hover:border-white/[0.12] transition-colors cursor-default"
          >
            <div className="w-9 h-9 rounded-lg bg-slate-800 border border-white/[0.08] flex items-center justify-center text-lg flex-shrink-0">
              {entry.icon}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-0.5">
                <span className="text-slate-200 font-medium text-sm">{entry.display_name}</span>
                <span className="px-1.5 py-0.5 rounded text-[10px] bg-slate-700/50 text-slate-500 border border-white/[0.06]">
                  {entry.auth_type}
                </span>
              </div>
              <p className="text-slate-500 text-xs leading-relaxed">{entry.description}</p>
              <div className="flex items-center justify-between mt-2">
                <span className="text-[11px] text-slate-600">{entry.actions.length} actions</span>
                <button
                  onClick={() => onConfigure(entry)}
                  className="text-xs text-cyan-500 hover:text-cyan-400 transition-colors font-medium"
                >
                  Configure →
                </button>
              </div>
            </div>
          </div>
        ))}
        {filtered.length === 0 && (
          <div className="col-span-2 text-center py-8 text-slate-600 text-sm">
            No connectors match your search
          </div>
        )}
      </div>
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function Connectors() {
  const { token } = useAuth()
  const authHeader = `Bearer ${token}`

  const [connectors, setConnectors] = useState<Connector[]>([])
  const [catalog, setCatalog] = useState<CatalogEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<'configured' | 'catalog'>('configured')
  const [showAdd, setShowAdd] = useState(false)
  const [preselect, setPreselect] = useState<CatalogEntry | null>(null)

  const fetchConnectors = useCallback(async () => {
    try {
      const res = await fetch(`${API}/connectors`, { headers: { Authorization: authHeader } })
      const d = await res.json()
      setConnectors(d.connectors ?? [])
    } catch { /* silent */ }
  }, [authHeader])

  const fetchCatalog = useCallback(async () => {
    try {
      const res = await fetch(`${API}/connectors/catalog`, { headers: { Authorization: authHeader } })
      const d = await res.json()
      setCatalog(d.catalog ?? [])
    } catch { /* silent */ }
  }, [authHeader])

  useEffect(() => {
    Promise.all([fetchConnectors(), fetchCatalog()]).finally(() => setLoading(false))
  }, [fetchConnectors, fetchCatalog])

  function handleConfigureFromCatalog(entry: CatalogEntry) {
    setPreselect(entry)
    setShowAdd(true)
    setTab('configured')
  }

  const connected = connectors.filter(c => c.status === 'connected').length
  const enabled = connectors.filter(c => c.enabled).length

  return (
    <div className="p-8 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white">Connectors</h1>
          <p className="text-slate-500 text-sm mt-0.5">
            Integrate external services with your agents
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => { fetchConnectors(); fetchCatalog() }}
            className="btn btn-secondary gap-1.5"
          >
            <RefreshCw size={14} /> Refresh
          </button>
          <button onClick={() => { setPreselect(null); setShowAdd(true) }} className="btn btn-primary gap-1.5">
            <Plus size={14} /> Add Connector
          </button>
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-4 mb-8">
        {[
          { label: 'Configured', value: connectors.length, color: 'text-slate-300' },
          { label: 'Enabled', value: enabled, color: 'text-emerald-400' },
          { label: 'Connected', value: connected, color: 'text-cyan-400' },
        ].map(({ label, value, color }) => (
          <div key={label} className="card px-5 py-4 flex items-center gap-4">
            <span className={`text-3xl font-bold ${color}`}>{value}</span>
            <span className="text-slate-500 text-sm">{label}</span>
          </div>
        ))}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-6 border-b border-white/[0.06] pb-0">
        {(['configured', 'catalog'] as const).map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px
              ${tab === t
                ? 'border-cyan-500 text-cyan-400'
                : 'border-transparent text-slate-500 hover:text-slate-300'
              }`}
          >
            {t === 'configured' ? `Configured (${connectors.length})` : `Catalog (${catalog.length})`}
          </button>
        ))}
      </div>

      {/* Content */}
      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map(i => (
            <div key={i} className="card px-5 py-4 animate-pulse h-20 bg-slate-900/60" />
          ))}
        </div>
      ) : tab === 'configured' ? (
        connectors.length === 0 ? (
          <div className="card py-16 flex flex-col items-center gap-4 text-center">
            <div className="w-14 h-14 rounded-2xl bg-slate-800 border border-white/[0.06] flex items-center justify-center text-3xl">
              🔌
            </div>
            <div>
              <p className="text-slate-300 font-medium mb-1">No connectors configured</p>
              <p className="text-slate-600 text-sm">
                Add a connector to give your agents access to external services
              </p>
            </div>
            <button onClick={() => setShowAdd(true)} className="btn btn-primary gap-1.5 mt-2">
              <Plus size={14} /> Add your first connector
            </button>
          </div>
        ) : (
          <div className="space-y-3">
            {connectors.map(c => (
              <ConnectorCard
                key={c.name}
                connector={c}
                authHeader={authHeader}
                onRefresh={fetchConnectors}
              />
            ))}
          </div>
        )
      ) : (
        <CatalogBrowser catalog={catalog} onConfigure={handleConfigureFromCatalog} />
      )}

      {/* Add modal */}
      {showAdd && (
        <AddConnectorModal
          catalog={preselect ? [preselect, ...catalog.filter(c => c.type !== preselect.type)] : catalog}
          onClose={() => { setShowAdd(false); setPreselect(null) }}
          onCreated={fetchConnectors}
          authHeader={authHeader}
        />
      )}
    </div>
  )
}
