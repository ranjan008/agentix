import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Zap, RefreshCw, RotateCcw, Filter } from 'lucide-react'
import { api } from '../api/client'
import { Page, PageHeader, Card, CardHeader, Table, TableRow, Td, EmptyState, Skeleton, StatusBadge, MonoId, Timestamp, Btn } from '../components/ui'

const STATUSES = ['', 'running', 'done', 'failed', 'queued']

export default function Triggers() {
  const qc = useQueryClient()
  const [statusFilter, setStatusFilter] = useState('')

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
              <TableRow key={t.id}>
                <Td><MonoId value={t.id} len={20} /></Td>
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
                    onClick={() => replayMut.mutate(t.id)}
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
  )
}
