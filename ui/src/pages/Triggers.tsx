import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Zap, RefreshCw, RotateCcw, Filter, X, ChevronRight, Building2, User, TrendingUp, Globe, Target, UserCheck } from 'lucide-react'
import { api } from '../api/client'
import { Page, PageHeader, Card, CardHeader, Table, TableRow, Td, EmptyState, Skeleton, StatusBadge, MonoId, Timestamp, Btn } from '../components/ui'

const STATUSES = ['', 'running', 'done', 'failed', 'queued']

// ---------------------------------------------------------------------------
// Deal triage card — rendered when response JSON matches the deal-triage schema
// ---------------------------------------------------------------------------
function DealTriageCard({ data }: { data: any }) {
  const actionColor: Record<string, string> = {
    PRIORITY: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30',
    SCREEN: 'text-amber-400 bg-amber-500/10 border-amber-500/30',
    PASS: 'text-rose-400 bg-rose-500/10 border-rose-500/30',
  }
  const action = (data.recommended_action ?? '').toUpperCase()
  return (
    <div className="space-y-4">
      {/* header row */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-base font-semibold text-slate-100">{data.company_name}</h3>
          <p className="text-xs text-slate-500 mt-0.5">{data.stage} · {data.ask_size}</p>
        </div>
        {action && (
          <span className={`text-xs font-bold px-2.5 py-1 rounded-lg border ${actionColor[action] ?? 'text-slate-400 bg-white/[0.04] border-white/[0.08]'}`}>
            {action}
          </span>
        )}
      </div>

      {/* grid of key fields */}
      <div className="grid grid-cols-2 gap-2 text-xs">
        {[
          { icon: User, label: 'Founder', value: data.founder_name },
          { icon: Building2, label: 'Sector', value: data.sector },
          { icon: TrendingUp, label: 'Traction', value: data.traction_summary },
          { icon: Globe, label: 'Web finding', value: data.web_search_finding },
          { icon: Target, label: 'Thesis fit', value: data.thesis_fit_score != null ? `${data.thesis_fit_score}/10 — ${data.thesis_fit_reason}` : undefined },
          { icon: UserCheck, label: 'Assigned to', value: data.assigned_partner },
        ].filter(row => row.value).map(({ icon: Icon, label, value }) => (
          <div key={label} className={`${label === 'Traction' || label === 'Web finding' || label === 'Thesis fit' ? 'col-span-2' : ''} bg-white/[0.03] rounded-lg p-2.5 border border-white/[0.05]`}>
            <div className="flex items-center gap-1.5 text-slate-500 mb-1">
              <Icon size={10} />
              <span className="uppercase tracking-wide text-[10px]">{label}</span>
            </div>
            <p className="text-slate-300 leading-relaxed">{value}</p>
          </div>
        ))}
      </div>

      {/* summary */}
      {data.brief_summary && (
        <p className="text-xs text-slate-400 italic border-t border-white/[0.06] pt-3">
          {data.brief_summary}
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Response panel — slide-in drawer on the right
// ---------------------------------------------------------------------------
function ResponseDrawer({ triggerId, onClose }: { triggerId: string; onClose: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ['trigger', triggerId],
    queryFn: () => api.getTrigger(triggerId),
  })

  const response: string | undefined = data?.response

  // Try to parse JSON
  let parsed: any = null
  if (response) {
    try { parsed = JSON.parse(response) } catch { /* not JSON */ }
  }

  const isDealTriage = parsed && 'company_name' in parsed && 'recommended_action' in parsed

  return (
    <div className="fixed inset-y-0 right-0 w-[480px] bg-slate-900 border-l border-white/[0.07] shadow-2xl flex flex-col z-50">
      {/* drawer header */}
      <div className="flex items-center justify-between px-5 py-4 border-b border-white/[0.07]">
        <div>
          <p className="text-xs text-slate-500 uppercase tracking-wide mb-0.5">Trigger response</p>
          <MonoId value={triggerId} len={20} />
        </div>
        <button
          onClick={onClose}
          className="p-1.5 text-slate-600 hover:text-slate-300 hover:bg-white/[0.06] rounded-lg transition-colors"
        >
          <X size={16} />
        </button>
      </div>

      {/* metadata strip */}
      {data && (
        <div className="flex items-center gap-3 px-5 py-3 border-b border-white/[0.07] text-xs text-slate-500">
          <span className="font-medium text-slate-300">{data.agent_id}</span>
          <span>·</span>
          <span className="bg-white/[0.04] px-2 py-0.5 rounded-md border border-white/[0.06]">{data.channel ?? '—'}</span>
          <span>·</span>
          <StatusBadge status={data.status ?? 'unknown'} />
          {data.created_at && <><span>·</span><Timestamp value={data.created_at} /></>}
        </div>
      )}

      {/* content */}
      <div className="flex-1 overflow-y-auto px-5 py-5">
        {isLoading && (
          <div className="space-y-2">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="h-8 bg-white/[0.04] rounded-lg animate-pulse" />
            ))}
          </div>
        )}

        {!isLoading && !response && (
          <div className="flex flex-col items-center justify-center h-48 text-slate-600 gap-2">
            <Zap size={28} className="opacity-30" />
            <p className="text-sm">No response recorded yet</p>
          </div>
        )}

        {!isLoading && response && isDealTriage && (
          <DealTriageCard data={parsed} />
        )}

        {!isLoading && response && !isDealTriage && parsed && (
          <pre className="text-xs text-slate-300 font-mono whitespace-pre-wrap break-all bg-white/[0.03] rounded-xl p-4 border border-white/[0.06] leading-relaxed">
            {JSON.stringify(parsed, null, 2)}
          </pre>
        )}

        {!isLoading && response && !parsed && (
          <pre className="text-xs text-slate-300 font-mono whitespace-pre-wrap break-words bg-white/[0.03] rounded-xl p-4 border border-white/[0.06] leading-relaxed">
            {response}
          </pre>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function Triggers() {
  const qc = useQueryClient()
  const [statusFilter, setStatusFilter] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['triggers', statusFilter],
    queryFn: () => api.listTriggers({ limit: 50, ...(statusFilter ? { status: statusFilter } : {}) }),
    refetchInterval: 5000,
  })
  const replayMut = useMutation({
    mutationFn: api.replayTrigger,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['triggers'] }),
  })

  const triggers = data?.triggers ?? []

  return (
    <div className="flex h-full">
      <div className={`flex-1 min-w-0 transition-all duration-200 ${selectedId ? 'mr-[480px]' : ''}`}>
        <Page>
          <PageHeader
            title="Triggers"
            subtitle="Execution history · live refresh every 5s"
            actions={
              <>
                <div className="flex items-center gap-1.5 bg-white/[0.06] rounded-xl px-3 py-1.5 border border-white/[0.06]">
                  <Filter size={12} className="text-slate-500" />
                  <select
                    value={statusFilter}
                    onChange={e => setStatusFilter(e.target.value)}
                    className="text-xs text-slate-400 bg-transparent focus:outline-none"
                  >
                    {STATUSES.map(s => (
                      <option key={s} value={s} className="bg-slate-800">{s || 'All statuses'}</option>
                    ))}
                  </select>
                </div>
                <Btn onClick={() => qc.invalidateQueries({ queryKey: ['triggers'] })} size="sm">
                  <RefreshCw size={13} /> Refresh
                </Btn>
              </>
            }
          />

          <Card>
            <CardHeader
              title={`${triggers.length} trigger${triggers.length !== 1 ? 's' : ''}`}
              subtitle={statusFilter ? `Filtered: ${statusFilter}` : 'All'}
              actions={
                <span className="flex items-center gap-1.5 text-xs text-emerald-400 font-medium">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                  Live
                </span>
              }
            />
            {isLoading ? <Skeleton rows={6} cols={6} /> : (
              <Table headers={['Trigger ID', 'Agent', 'Channel', 'Identity', 'Status', 'Time', '']}>
                {triggers.length === 0 && (
                  <tr><td colSpan={7}>
                    <EmptyState icon={Zap} title="No triggers yet"
                      desc="Send a message to an agent via Telegram or the Chat page to create triggers." />
                  </td></tr>
                )}
                {triggers.map((t: any) => (
                  <TableRow
                    key={t.id}
                    onClick={() => setSelectedId(t.id === selectedId ? null : t.id)}
                    className={`cursor-pointer transition-colors ${t.id === selectedId ? 'bg-cyan-500/[0.06] border-l-2 border-l-cyan-500' : 'hover:bg-white/[0.02]'}`}
                  >
                    <Td>
                      <div className="flex items-center gap-1.5">
                        <ChevronRight size={12} className={`text-slate-600 transition-transform ${t.id === selectedId ? 'rotate-90 text-cyan-400' : ''}`} />
                        <MonoId value={t.id} len={20} />
                      </div>
                    </Td>
                    <Td>
                      <div className="flex items-center gap-2">
                        <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 flex-shrink-0" />
                        <span className="text-sm font-medium text-slate-300">{t.agent_id}</span>
                      </div>
                    </Td>
                    <Td>
                      <span className="text-xs text-slate-500 bg-white/[0.04] px-2 py-0.5 rounded-md border border-white/[0.06]">
                        {t.channel ?? '—'}
                      </span>
                    </Td>
                    <Td>
                      <span className="text-xs text-slate-500">{t.caller?.identity_id ?? '—'}</span>
                    </Td>
                    <Td><StatusBadge status={t.status ?? 'unknown'} /></Td>
                    <Td><Timestamp value={t.created_at ?? t.timestamp} /></Td>
                    <Td>
                      <button
                        onClick={e => { e.stopPropagation(); replayMut.mutate(t.id) }}
                        disabled={replayMut.isPending}
                        title="Replay trigger"
                        className="p-1.5 text-slate-700 hover:text-cyan-400 hover:bg-cyan-500/10 rounded-lg transition-colors disabled:opacity-40"
                      >
                        <RotateCcw size={14} />
                      </button>
                    </Td>
                  </TableRow>
                ))}
              </Table>
            )}
          </Card>
        </Page>
      </div>

      {selectedId && (
        <ResponseDrawer triggerId={selectedId} onClose={() => setSelectedId(null)} />
      )}
    </div>
  )
}
