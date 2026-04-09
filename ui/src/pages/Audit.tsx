import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { ShieldCheck, ShieldAlert } from 'lucide-react'

function fmtTs(ts: number | string | undefined): string {
  if (!ts) return '—'
  const ms = typeof ts === 'number' ? ts * 1000 : Number(ts) * 1000
  return new Date(ms).toISOString().slice(0, 19).replace('T', ' ')
}

export default function Audit() {
  const { data: auditData, isLoading } = useQuery({
    queryKey: ['audit'],
    queryFn: () => api.listAudit({ limit: 100 }),
  })
  const { data: chainStatus } = useQuery({
    queryKey: ['audit-chain'],
    queryFn: api.verifyAuditChain,
    refetchInterval: 60_000,
  })

  const entries: any[] = auditData?.entries ?? []

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-xl font-bold text-slate-100 tracking-tight">Audit Log</h1>
          <p className="mt-1 text-sm text-slate-500">Immutable HMAC-chained event trail</p>
        </div>
        <div className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium border ${
          chainStatus?.chain_valid === false
            ? 'bg-red-500/10 text-red-400 border-red-500/20'
            : 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
        }`}>
          {chainStatus?.chain_valid === false
            ? <><ShieldAlert size={16} /> Chain tampered</>
            : <><ShieldCheck size={16} /> Chain valid</>
          }
        </div>
      </div>

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
                {['Event', 'Trigger ID', 'Agent', 'Actor', 'Timestamp'].map(h => (
                  <th key={h} className="px-5 py-3 text-left text-[11px] font-semibold text-slate-600 uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {entries.map((e: any, i: number) => (
                <tr key={i} className="border-b border-white/[0.04] last:border-0 hover:bg-white/[0.03] transition-colors">
                  <td className="px-5 py-3 font-mono text-xs text-cyan-400">{e.event_type ?? e.action ?? '—'}</td>
                  <td className="px-5 py-3 font-mono text-xs text-slate-500">{e.trigger_id ? String(e.trigger_id).slice(0, 16) : '—'}</td>
                  <td className="px-5 py-3 text-xs text-slate-300">{e.agent_id ?? '—'}</td>
                  <td className="px-5 py-3 text-xs text-slate-500">{e.identity_id ?? e.actor ?? '—'}</td>
                  <td className="px-5 py-3 text-xs text-slate-500 font-mono">{fmtTs(e.timestamp ?? e.ts)}</td>
                </tr>
              ))}
              {entries.length === 0 && (
                <tr><td colSpan={5} className="px-5 py-12 text-center text-slate-600 text-sm">No audit entries</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
