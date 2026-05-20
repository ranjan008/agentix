import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ShieldCheck, Download, AlertTriangle, CheckCircle2, Clock,
  Trash2, FileText, RefreshCw, User,
} from 'lucide-react'
import { api } from '../api/client'
import { Page, PageHeader, Card, CardHeader, Table } from '../components/ui'

// ---------------------------------------------------------------------------
// Severity / status badges
// ---------------------------------------------------------------------------

const SEVERITY_STYLES: Record<string, string> = {
  low:      'bg-sky-500/10 text-sky-400 border border-sky-500/20',
  medium:   'bg-amber-500/10 text-amber-400 border border-amber-500/20',
  high:     'bg-orange-500/10 text-orange-400 border border-orange-500/20',
  critical: 'bg-rose-500/10 text-rose-400 border border-rose-500/20',
}

const STATUS_STYLES: Record<string, string> = {
  open:        'bg-rose-500/10 text-rose-400 border border-rose-500/20',
  in_progress: 'bg-amber-500/10 text-amber-400 border border-amber-500/20',
  resolved:    'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20',
  closed:      'bg-slate-500/10 text-slate-400 border border-slate-500/20',
  wont_fix:    'bg-slate-500/10 text-slate-500 border border-slate-700/40',
}

function Badge({ text, styles }: { text: string; styles: Record<string, string> }) {
  const cls = styles[text.toLowerCase()] ?? 'bg-slate-500/10 text-slate-400 border border-slate-700'
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wide ${cls}`}>
      {text}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Export section
// ---------------------------------------------------------------------------

function ExportSection() {
  const [oecdDays, setOecdDays] = useState(90)
  const [oecdLoading, setOecdLoading] = useState(false)
  const [soc2Loading, setSoc2Loading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleOecd() {
    setError(null)
    setOecdLoading(true)
    try { await api.downloadOecdReport(oecdDays) }
    catch (e: any) { setError(e.message) }
    finally { setOecdLoading(false) }
  }

  async function handleSoc2() {
    setError(null)
    setSoc2Loading(true)
    try { await api.downloadSoc2Report() }
    catch (e: any) { setError(e.message) }
    finally { setSoc2Loading(false) }
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-6">
      <Card>
        <CardHeader title="OECD Due Diligence Report" subtitle="6-step evidence bundle (ZIP)" />
        <div className="p-6 space-y-4">
          <div>
            <label className="block text-xs text-slate-500 mb-1.5">Reporting period (days)</label>
            <select
              value={oecdDays}
              onChange={e => setOecdDays(Number(e.target.value))}
              className="bg-[#0d1526] border border-white/[0.08] rounded-lg px-3 py-2 text-sm text-slate-300 w-full"
            >
              {[30, 60, 90, 180, 365].map(d => (
                <option key={d} value={d}>{d} days</option>
              ))}
            </select>
          </div>
          {error && <p className="text-xs text-rose-400">{error}</p>}
          <button
            onClick={handleOecd}
            disabled={oecdLoading}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-cyan-500/10 hover:bg-cyan-500/20 text-cyan-400 border border-cyan-500/20 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
          >
            {oecdLoading
              ? <RefreshCw size={14} className="animate-spin" />
              : <Download size={14} />}
            {oecdLoading ? 'Generating…' : 'Download OECD Report'}
          </button>
        </div>
      </Card>

      <Card>
        <CardHeader title="SOC 2 Evidence Bundle" subtitle="Audit log + chain verification (ZIP)" />
        <div className="p-6 space-y-4">
          <p className="text-xs text-slate-500 leading-relaxed">
            Packages the full audit log, HMAC chain verification, RBAC policy snapshot,
            and system configuration into a single evidence archive.
          </p>
          <button
            onClick={handleSoc2}
            disabled={soc2Loading}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 border border-indigo-500/20 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
          >
            {soc2Loading
              ? <RefreshCw size={14} className="animate-spin" />
              : <Download size={14} />}
            {soc2Loading ? 'Generating…' : 'Download SOC 2 Bundle'}
          </button>
        </div>
      </Card>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Remediation table
// ---------------------------------------------------------------------------

const STATUSES = ['open', 'in_progress', 'resolved', 'closed', 'wont_fix']

function RemediationSection() {
  const qc = useQueryClient()
  const [resolveId, setResolveId] = useState<number | null>(null)
  const [resolveNote, setResolveNote] = useState('')
  const [includeResolved, setIncludeResolved] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ['remediation', includeResolved],
    queryFn: () => api.listRemediation({ include_resolved: includeResolved }),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, body }: { id: number; body: any }) => api.updateRemediation(id, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['remediation'] })
      setResolveId(null)
      setResolveNote('')
    },
  })

  const items: any[] = data?.items ?? []
  const summary: any = data?.summary ?? {}

  function formatTs(ts: number | null): string {
    if (!ts) return '—'
    return new Date(ts * 1000).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
  }

  return (
    <Card className="mb-6">
      <CardHeader
        title="Remediation Log"
        subtitle="OECD Due Diligence Step 6 — adverse impact tracking"
        actions={
          <label className="flex items-center gap-2 text-xs text-slate-500 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={includeResolved}
              onChange={e => setIncludeResolved(e.target.checked)}
              className="accent-cyan-500"
            />
            Show resolved
          </label>
        }
      />

      {/* Summary pills */}
      {summary.by_status && (
        <div className="px-6 py-3 flex flex-wrap gap-2 border-b border-white/[0.06]">
          {Object.entries(summary.by_status as Record<string, number>).map(([s, n]) => (
            <span key={s} className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium ${STATUS_STYLES[s] ?? 'bg-slate-700 text-slate-400'}`}>
              {s.replace('_', ' ')} <span className="font-bold">{n}</span>
            </span>
          ))}
        </div>
      )}

      {isLoading ? (
        <div className="py-12 text-center text-slate-600 text-sm">Loading…</div>
      ) : items.length === 0 ? (
        <div className="py-16 text-center">
          <CheckCircle2 size={28} className="mx-auto text-emerald-600 mb-3" />
          <p className="text-sm text-slate-500">No open remediation items</p>
        </div>
      ) : (
        <Table headers={['ID', 'Type', 'Severity', 'Status', 'Description', 'Owner', 'Discovered', 'Actions']}>
          {items.map((item: any) => (
            <tr key={item.id} className="border-b border-white/[0.04] hover:bg-white/[0.03] transition-colors">
              <td className="px-5 py-3 text-xs text-slate-500 font-mono">#{item.id}</td>
              <td className="px-5 py-3 text-xs text-slate-400">{item.harm_type}</td>
              <td className="px-5 py-3">
                <Badge text={item.severity} styles={SEVERITY_STYLES} />
              </td>
              <td className="px-5 py-3">
                <Badge text={item.status} styles={STATUS_STYLES} />
              </td>
              <td className="px-5 py-3 text-xs text-slate-300 max-w-xs truncate" title={item.description}>
                {item.description}
              </td>
              <td className="px-5 py-3 text-xs text-slate-500">{item.owner ?? '—'}</td>
              <td className="px-5 py-3 text-xs text-slate-500 whitespace-nowrap">{formatTs(item.discovered_at)}</td>
              <td className="px-5 py-3">
                {item.status !== 'resolved' && item.status !== 'closed' && item.status !== 'wont_fix' && (
                  <button
                    onClick={() => { setResolveId(item.id); setResolveNote('') }}
                    className="text-xs text-emerald-500 hover:text-emerald-400 font-medium transition-colors"
                  >
                    Resolve
                  </button>
                )}
              </td>
            </tr>
          ))}
        </Table>
      )}

      {/* Resolve modal */}
      {resolveId !== null && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50">
          <div className="bg-[#0d1526] border border-white/[0.08] rounded-2xl p-6 w-full max-w-md shadow-2xl">
            <h3 className="text-sm font-semibold text-slate-200 mb-1">Resolve item #{resolveId}</h3>
            <p className="text-xs text-slate-500 mb-4">Provide a resolution note describing the fix applied.</p>
            <textarea
              rows={3}
              value={resolveNote}
              onChange={e => setResolveNote(e.target.value)}
              placeholder="e.g. Updated system prompt, re-evaluated with AdverseImpactScorer…"
              className="w-full bg-[#070e1c] border border-white/[0.08] rounded-lg px-3 py-2 text-sm text-slate-300 placeholder-slate-700 resize-none mb-4"
            />
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setResolveId(null)}
                className="px-4 py-2 text-sm text-slate-500 hover:text-slate-300 transition-colors"
              >
                Cancel
              </button>
              <button
                disabled={!resolveNote.trim() || updateMutation.isPending}
                onClick={() => updateMutation.mutate({ id: resolveId, body: { status: 'resolved', resolution_note: resolveNote } })}
                className="px-4 py-2 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border border-emerald-500/20 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
              >
                {updateMutation.isPending ? 'Saving…' : 'Mark Resolved'}
              </button>
            </div>
          </div>
        </div>
      )}
    </Card>
  )
}

// ---------------------------------------------------------------------------
// GDPR section
// ---------------------------------------------------------------------------

function GdprSection() {
  const [identityId, setIdentityId] = useState('')
  const [tenantId, setTenantId] = useState('default')
  const [exportData, setExportData] = useState<any | null>(null)
  const [exportLoading, setExportLoading] = useState(false)
  const [eraseConfirm, setEraseConfirm] = useState(false)
  const [eraseResult, setEraseResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function handleExport() {
    if (!identityId.trim()) return
    setError(null)
    setExportData(null)
    setExportLoading(true)
    try {
      const data = await api.gdprExport(identityId, tenantId)
      setExportData(data)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setExportLoading(false)
    }
  }

  async function handleErase() {
    if (!identityId.trim()) return
    setError(null)
    setEraseResult(null)
    try {
      const result = await api.gdprErasure(identityId, tenantId)
      setEraseResult(JSON.stringify(result, null, 2))
      setEraseConfirm(false)
    } catch (e: any) {
      setError(e.message)
      setEraseConfirm(false)
    }
  }

  return (
    <Card>
      <CardHeader title="GDPR Data Subject Requests" subtitle="Article 17 erasure · Article 20 portability" />
      <div className="p-6 space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="block text-xs text-slate-500 mb-1.5">Identity ID</label>
            <input
              value={identityId}
              onChange={e => setIdentityId(e.target.value)}
              placeholder="user@example.com or UUID"
              className="w-full bg-[#070e1c] border border-white/[0.08] rounded-lg px-3 py-2 text-sm text-slate-300 placeholder-slate-700"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1.5">Tenant ID</label>
            <input
              value={tenantId}
              onChange={e => setTenantId(e.target.value)}
              placeholder="default"
              className="w-full bg-[#070e1c] border border-white/[0.08] rounded-lg px-3 py-2 text-sm text-slate-300 placeholder-slate-700"
            />
          </div>
        </div>

        {error && <p className="text-xs text-rose-400">{error}</p>}

        <div className="flex gap-3">
          <button
            disabled={!identityId.trim() || exportLoading}
            onClick={handleExport}
            className="flex items-center gap-2 px-4 py-2 bg-cyan-500/10 hover:bg-cyan-500/20 text-cyan-400 border border-cyan-500/20 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
          >
            <FileText size={14} />
            {exportLoading ? 'Loading…' : 'Export Data'}
          </button>
          <button
            disabled={!identityId.trim()}
            onClick={() => setEraseConfirm(true)}
            className="flex items-center gap-2 px-4 py-2 bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/20 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
          >
            <Trash2 size={14} />
            Request Erasure
          </button>
        </div>

        {exportData && (
          <div>
            <p className="text-xs text-slate-500 mb-2 font-medium uppercase tracking-wide">Exported data</p>
            <pre className="bg-[#070e1c] border border-white/[0.06] rounded-lg p-4 text-xs text-slate-400 overflow-auto max-h-64 leading-relaxed">
              {JSON.stringify(exportData, null, 2)}
            </pre>
          </div>
        )}

        {eraseResult && (
          <div className="flex items-start gap-3 p-4 bg-emerald-500/5 border border-emerald-500/20 rounded-xl">
            <CheckCircle2 size={16} className="text-emerald-500 mt-0.5 flex-shrink-0" />
            <div>
              <p className="text-xs font-semibold text-emerald-400 mb-1">Erasure completed</p>
              <pre className="text-xs text-slate-400 whitespace-pre-wrap">{eraseResult}</pre>
            </div>
          </div>
        )}
      </div>

      {/* Erase confirm dialog */}
      {eraseConfirm && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50">
          <div className="bg-[#0d1526] border border-white/[0.08] rounded-2xl p-6 w-full max-w-sm shadow-2xl">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-9 h-9 rounded-xl bg-rose-500/10 flex items-center justify-center">
                <AlertTriangle size={16} className="text-rose-400" />
              </div>
              <h3 className="text-sm font-semibold text-slate-200">Confirm erasure</h3>
            </div>
            <p className="text-xs text-slate-400 mb-5 leading-relaxed">
              All personal data for <span className="font-mono text-slate-200">{identityId}</span> in
              tenant <span className="font-mono text-slate-200">{tenantId}</span> will be permanently
              deleted. This action cannot be undone.
            </p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setEraseConfirm(false)}
                className="px-4 py-2 text-sm text-slate-500 hover:text-slate-300 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleErase}
                className="px-4 py-2 bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/20 rounded-lg text-sm font-medium transition-colors"
              >
                Erase permanently
              </button>
            </div>
          </div>
        </div>
      )}
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Page root
// ---------------------------------------------------------------------------

export default function Compliance() {
  return (
    <Page>
      <PageHeader
        title="Compliance"
        subtitle="OECD Due Diligence · SOC 2 · GDPR data subject rights"
      />
      <ExportSection />
      <RemediationSection />
      <GdprSection />
    </Page>
  )
}
