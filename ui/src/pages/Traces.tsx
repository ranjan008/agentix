import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Activity, ChevronDown, ChevronRight, Clock, Cpu, Wrench, AlertCircle, CheckCircle } from 'lucide-react'
import { api } from '../api/client'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtTs(ts: number | undefined): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toISOString().slice(0, 19).replace('T', ' ')
}

function fmtDuration(startedAt: number | undefined, finishedAt: number | undefined): string {
  if (!startedAt) return '—'
  const end = finishedAt ?? Date.now() / 1000
  const ms = (end - startedAt) * 1000
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function fmtSpanDuration(ms: number | undefined): string {
  if (ms == null) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

const STATUS_STYLE: Record<string, string> = {
  running: 'text-cyan-400 bg-cyan-500/10 border-cyan-500/20',
  done:    'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
  failed:  'text-rose-400 bg-rose-500/10 border-rose-500/20',
}

const SPAN_ICON: Record<string, React.ReactNode> = {
  'llm.call':   <Cpu size={12} className="text-violet-400" />,
  'tool.call':  <Wrench size={12} className="text-amber-400" />,
  'agent.run':  <Activity size={12} className="text-cyan-400" />,
}

// ---------------------------------------------------------------------------
// Span tree row
// ---------------------------------------------------------------------------
function SpanRow({ span, depth = 0 }: { span: any; depth?: number }) {
  const [open, setOpen] = useState(false)
  const hasDetail = span.input || span.output || span.error
  const statusOk = span.status === 'ok'

  return (
    <>
      <tr
        className="border-b border-white/[0.04] last:border-0 hover:bg-white/[0.03] transition-colors cursor-pointer"
        onClick={() => hasDetail && setOpen(o => !o)}
      >
        <td className="px-4 py-2.5">
          <div className="flex items-center gap-2" style={{ paddingLeft: depth * 16 }}>
            {hasDetail
              ? open ? <ChevronDown size={12} className="text-slate-500 shrink-0" /> : <ChevronRight size={12} className="text-slate-500 shrink-0" />
              : <span className="w-3" />
            }
            {SPAN_ICON[span.name] ?? <Activity size={12} className="text-slate-600" />}
            <span className="font-mono text-xs text-slate-300">{span.name}</span>
          </div>
        </td>
        <td className="px-4 py-2.5">
          {statusOk
            ? <CheckCircle size={12} className="text-emerald-400" />
            : <AlertCircle size={12} className="text-rose-400" />
          }
        </td>
        <td className="px-4 py-2.5 font-mono text-xs text-slate-500">
          {fmtSpanDuration(span.duration_ms)}
        </td>
        <td className="px-4 py-2.5 text-xs text-slate-600 truncate max-w-[200px]">
          {span.attributes?.model && <span className="text-violet-400">{span.attributes.model} </span>}
          {span.attributes?.tool_name && <span className="text-amber-400">{span.attributes.tool_name} </span>}
          {span.attributes?.input_tokens != null && (
            <span>{span.attributes.input_tokens}→{span.attributes.output_tokens} tok</span>
          )}
        </td>
      </tr>
      {open && hasDetail && (
        <tr className="border-b border-white/[0.04]">
          <td colSpan={4} className="px-4 pb-3">
            <div style={{ paddingLeft: (depth + 1) * 16 }} className="space-y-2">
              {span.error && (
                <div className="bg-rose-500/10 border border-rose-500/20 rounded-lg px-3 py-2 text-xs text-rose-400 font-mono">
                  Error: {span.error}
                </div>
              )}
              {span.input && (
                <div>
                  <p className="text-[10px] text-slate-600 uppercase tracking-wider mb-1">Input</p>
                  <pre className="text-[11px] text-slate-400 bg-white/[0.03] rounded-lg p-3 border border-white/[0.06] overflow-auto max-h-40">
                    {typeof span.input === 'object' ? JSON.stringify(span.input, null, 2) : String(span.input)}
                  </pre>
                </div>
              )}
              {span.output && (
                <div>
                  <p className="text-[10px] text-slate-600 uppercase tracking-wider mb-1">Output</p>
                  <pre className="text-[11px] text-slate-400 bg-white/[0.03] rounded-lg p-3 border border-white/[0.06] overflow-auto max-h-40">
                    {typeof span.output === 'object' ? JSON.stringify(span.output, null, 2) : String(span.output)}
                  </pre>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

// ---------------------------------------------------------------------------
// Trace detail panel
// ---------------------------------------------------------------------------
function TraceDetail({ traceId, onClose }: { traceId: string; onClose: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ['trace', traceId],
    queryFn: () => api.getTrace(traceId),
  })

  const spans: any[] = data?.spans ?? []

  // Build child map for indented rendering
  const childMap: Record<string, any[]> = {}
  const roots: any[] = []
  for (const s of spans) {
    if (s.parent_span_id) {
      childMap[s.parent_span_id] = childMap[s.parent_span_id] ?? []
      childMap[s.parent_span_id].push(s)
    } else {
      roots.push(s)
    }
  }

  function renderSpan(span: any, depth: number): React.ReactNode {
    return (
      <>
        <SpanRow key={span.id} span={span} depth={depth} />
        {(childMap[span.id] ?? []).map(child => renderSpan(child, depth + 1))}
      </>
    )
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-end" onClick={onClose}>
      <div
        className="w-[700px] max-w-full h-full bg-[#070e1c] border-l border-white/[0.08] shadow-2xl flex flex-col overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        <div className="px-6 py-4 border-b border-white/[0.06] flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold text-slate-100">Trace</h2>
            <p className="font-mono text-[11px] text-slate-500 mt-0.5">{traceId}</p>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-300 transition-colors text-lg leading-none">×</button>
        </div>

        {isLoading ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="w-5 h-5 border-2 border-cyan-500/30 border-t-cyan-500 rounded-full animate-spin" />
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto">
            {/* Summary bar */}
            <div className="px-6 py-3 border-b border-white/[0.04] flex gap-6 text-xs text-slate-500">
              <span>
                <span className="text-slate-400 font-medium">{data?.agent_id}</span>
              </span>
              <span className="flex items-center gap-1">
                <Clock size={11} />
                {fmtDuration(data?.started_at, data?.finished_at)}
              </span>
              <span>{data?.total_tokens?.toLocaleString() ?? 0} tokens</span>
              <span className={`px-1.5 py-0.5 rounded border text-[10px] font-medium ${STATUS_STYLE[data?.status ?? ''] ?? 'text-slate-500 bg-white/[0.04] border-white/[0.06]'}`}>
                {data?.status}
              </span>
            </div>

            {/* Span tree */}
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/[0.06] bg-white/[0.02]">
                  {['Span', 'Status', 'Duration', 'Details'].map(h => (
                    <th key={h} className="px-4 py-2.5 text-left text-[10px] font-semibold text-slate-600 uppercase tracking-wider">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {roots.length > 0
                  ? roots.map(s => renderSpan(s, 0))
                  : spans.map(s => <SpanRow key={s.id} span={s} depth={0} />)
                }
                {spans.length === 0 && (
                  <tr>
                    <td colSpan={4} className="px-4 py-10 text-center text-slate-600 text-xs">No spans recorded</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Traces page
// ---------------------------------------------------------------------------
const PAGE_SIZE = 30

export default function Traces() {
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null)
  const [agentFilter, setAgentFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [page, setPage] = useState(0)

  // Reset to first page whenever filters change
  const handleAgentFilter = (v: string) => { setAgentFilter(v); setPage(0) }
  const handleStatusFilter = (v: string) => { setStatusFilter(v); setPage(0) }

  const params: Record<string, string | number> = { limit: PAGE_SIZE, offset: page * PAGE_SIZE }
  if (agentFilter) params.agent_id = agentFilter
  if (statusFilter) params.status = statusFilter

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['traces', agentFilter, statusFilter, page],
    queryFn: () => api.listTraces(params),
    refetchInterval: 10_000,
  })

  const traces: any[] = data?.traces ?? []
  const total: number = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="p-8">
      {selectedTraceId && (
        <TraceDetail traceId={selectedTraceId} onClose={() => setSelectedTraceId(null)} />
      )}

      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-slate-100 tracking-tight">Traces</h1>
          <p className="mt-1 text-sm text-slate-500">Agent execution traces — LLM calls, tool calls, and timing</p>
        </div>
        <button
          onClick={() => refetch()}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white/[0.05] hover:bg-white/[0.08] border border-white/[0.08] text-slate-400 text-xs transition-colors"
        >
          <Activity size={13} />
          Refresh
        </button>
      </div>

      {/* Filters */}
      <div className="flex gap-3 mb-5">
        <input
          value={agentFilter}
          onChange={e => handleAgentFilter(e.target.value)}
          placeholder="Filter by agent…"
          className="flex-1 max-w-xs px-3 py-1.5 rounded-lg bg-white/[0.05] border border-white/[0.08] text-slate-300 text-xs placeholder-slate-600 focus:outline-none focus:ring-1 focus:ring-cyan-500/40"
        />
        <select
          value={statusFilter}
          onChange={e => handleStatusFilter(e.target.value)}
          className="px-3 py-1.5 rounded-lg bg-white/[0.05] border border-white/[0.08] text-slate-300 text-xs focus:outline-none focus:ring-1 focus:ring-cyan-500/40"
        >
          <option value="">All statuses</option>
          <option value="running">Running</option>
          <option value="done">Done</option>
          <option value="failed">Failed</option>
        </select>
      </div>

      {/* Table */}
      <div className="card overflow-hidden">
        {isLoading ? (
          <div className="divide-y divide-white/[0.04]">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="px-5 py-4 flex gap-4">
                {[...Array(5)].map((_, j) => (
                  <div key={j} className={`h-4 bg-white/[0.06] rounded animate-pulse ${j === 0 ? 'w-32' : 'flex-1'}`} />
                ))}
              </div>
            ))}
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.06] bg-white/[0.02]">
                {['Trace ID', 'Agent', 'Status', 'Duration', 'Tokens', 'Started'].map(h => (
                  <th key={h} className="px-5 py-3 text-left text-[11px] font-semibold text-slate-600 uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {traces.map((t: any) => (
                <tr
                  key={t.id}
                  onClick={() => setSelectedTraceId(t.id)}
                  className="border-b border-white/[0.04] last:border-0 hover:bg-white/[0.03] transition-colors cursor-pointer"
                >
                  <td className="px-5 py-3 font-mono text-xs text-cyan-400">{t.id.slice(0, 20)}…</td>
                  <td className="px-5 py-3 text-xs text-slate-300">{t.agent_id}</td>
                  <td className="px-5 py-3">
                    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded border ${STATUS_STYLE[t.status] ?? 'text-slate-500 bg-white/[0.04] border-white/[0.06]'}`}>
                      {t.status}
                    </span>
                  </td>
                  <td className="px-5 py-3 text-xs text-slate-500 font-mono flex items-center gap-1">
                    <Clock size={11} />
                    {fmtDuration(t.started_at, t.finished_at)}
                  </td>
                  <td className="px-5 py-3 text-xs text-slate-500">{(t.total_tokens ?? 0).toLocaleString()}</td>
                  <td className="px-5 py-3 text-xs text-slate-600 font-mono">{fmtTs(t.started_at)}</td>
                </tr>
              ))}
              {traces.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-5 py-14 text-center text-slate-600 text-sm">
                    No traces found. Run an agent to see execution traces here.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {!isLoading && (
        <div className="flex items-center justify-between mt-4 text-xs text-slate-500">
          <span>
            {total === 0 ? 'No results' : `${page * PAGE_SIZE + 1}–${Math.min((page + 1) * PAGE_SIZE, total)} of ${total}`}
          </span>
          <div className="flex gap-2">
            <button
              onClick={() => setPage(p => p - 1)}
              disabled={page === 0}
              className="px-3 py-1.5 rounded-lg bg-white/[0.05] border border-white/[0.08] text-slate-400 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-white/[0.08] transition-colors"
            >
              Previous
            </button>
            <span className="px-3 py-1.5 text-slate-600">Page {page + 1} of {totalPages}</span>
            <button
              onClick={() => setPage(p => p + 1)}
              disabled={page >= totalPages - 1}
              className="px-3 py-1.5 rounded-lg bg-white/[0.05] border border-white/[0.08] text-slate-400 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-white/[0.08] transition-colors"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
