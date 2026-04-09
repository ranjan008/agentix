import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { Bot, Zap, AlertTriangle, CheckCircle2, Clock, TrendingUp } from 'lucide-react'

function fmtTs(val: string | number | undefined): string {
  if (!val) return '—'
  const ms = typeof val === 'number' ? val * 1000 : new Date(val).getTime()
  return new Date(ms).toLocaleString('en-IN', { dateStyle: 'short', timeStyle: 'short' })
}

function StatCard({
  title, value, icon: Icon, gradient, change,
}: { title: string; value: any; icon: any; gradient: string; change?: string }) {
  return (
    <div className="card p-5 flex items-start gap-4 hover:border-white/[0.10] transition-all duration-200 group">
      <div className={`w-11 h-11 rounded-xl flex items-center justify-center flex-shrink-0 ${gradient} shadow-lg`}>
        <Icon size={20} className="text-white" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-xs text-slate-500 font-medium uppercase tracking-wide">{title}</p>
        <p className="text-2xl font-bold text-slate-100 mt-0.5 tabular-nums">{value ?? '—'}</p>
        {change && (
          <p className="text-xs text-emerald-400 mt-1 flex items-center gap-1">
            <TrendingUp size={11} />{change}
          </p>
        )}
      </div>
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const cls: Record<string, string> = {
    running: 'badge badge-blue',
    done:    'badge badge-green',
    failed:  'badge badge-red',
    queued:  'badge badge-yellow',
    pending: 'badge badge-yellow',
  }
  return <span className={cls[status] ?? 'badge badge-gray'}>{status}</span>
}

function RecentTriggers() {
  const { data, isLoading } = useQuery({
    queryKey: ['triggers-recent'],
    queryFn: () => api.listTriggers({ limit: 8 }),
    refetchInterval: 5000,
  })
  const triggers = data?.triggers ?? []

  if (isLoading) {
    return (
      <div className="space-y-3 p-6">
        {[...Array(4)].map((_, i) => (
          <div key={i} className="h-8 bg-white/[0.06] rounded-lg animate-pulse" />
        ))}
      </div>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/[0.06]">
            {['Trigger ID', 'Agent', 'Channel', 'Status', 'Time'].map(h => (
              <th key={h} className="px-5 py-3.5 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.04]">
          {triggers.map((t: any) => (
            <tr key={t.id} className="hover:bg-white/[0.03] transition-colors">
              <td className="px-5 py-3 font-mono text-xs text-slate-500">{String(t.id ?? '').slice(0, 18)}…</td>
              <td className="px-5 py-3">
                <span className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-300">
                  <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 flex-shrink-0" />
                  {t.agent_id}
                </span>
              </td>
              <td className="px-5 py-3 text-xs text-slate-500">{t.channel}</td>
              <td className="px-5 py-3"><StatusBadge status={t.status} /></td>
              <td className="px-5 py-3 text-xs text-slate-500">{fmtTs(t.created_at)}</td>
            </tr>
          ))}
          {triggers.length === 0 && (
            <tr>
              <td colSpan={5} className="px-5 py-10 text-center text-slate-600 text-sm">
                No triggers yet — send a message to an agent to get started.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

export default function Dashboard() {
  const { data: agents } = useQuery({ queryKey: ['agents'], queryFn: api.listAgents })
  const { data: stats } = useQuery({ queryKey: ['trigger-stats'], queryFn: () => api.triggerStats(24) })
  const { data: agentStats } = useQuery({ queryKey: ['agent-stats'], queryFn: api.agentStats })

  const successRate = stats?.total
    ? Math.round((stats.done / stats.total) * 100)
    : null

  return (
    <div className="p-8 space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-slate-100 tracking-tight">
          Dashboard
        </h1>
        <p className="mt-1 text-sm text-slate-500">Platform overview · last 24 hours</p>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        <StatCard title="Active Agents" value={agents?.length ?? 0}
          icon={Bot} gradient="bg-gradient-to-br from-indigo-500 to-indigo-700" />
        <StatCard title="Triggers (24h)" value={stats?.total ?? 0}
          icon={Zap} gradient="bg-gradient-to-br from-cyan-500 to-cyan-700" />
        <StatCard title="Completed" value={stats?.done ?? 0}
          icon={CheckCircle2} gradient="bg-gradient-to-br from-emerald-500 to-emerald-700"
          change={successRate != null ? `${successRate}% success rate` : undefined} />
        <StatCard title="Failed" value={stats?.failed ?? 0}
          icon={AlertTriangle} gradient="bg-gradient-to-br from-rose-500 to-rose-700" />
      </div>

      {/* Main content row */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        {/* Recent triggers */}
        <div className="xl:col-span-2 card overflow-hidden">
          <div className="px-5 py-4 border-b border-white/[0.06] flex items-center justify-between">
            <div>
              <h2 className="font-semibold text-slate-200">Recent Triggers</h2>
              <p className="text-xs text-slate-500 mt-0.5">Live · updates every 5s</p>
            </div>
            <span className="flex items-center gap-1.5 text-xs text-emerald-400 font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              Live
            </span>
          </div>
          <RecentTriggers />
        </div>

        {/* Agent stats */}
        <div className="card overflow-hidden">
          <div className="px-5 py-4 border-b border-white/[0.06]">
            <h2 className="font-semibold text-slate-200">Agent Performance</h2>
            <p className="text-xs text-slate-500 mt-0.5">All time</p>
          </div>
          <div className="divide-y divide-white/[0.04]">
            {(agentStats ?? []).slice(0, 6).map((a: any) => {
              const rate = a.total > 0 ? Math.round((a.succeeded / a.total) * 100) : 0
              return (
                <div key={a.agent_id} className="px-5 py-3.5 flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg bg-indigo-500/10 flex items-center justify-center flex-shrink-0">
                    <Bot size={14} className="text-indigo-400" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-medium text-slate-300 truncate">{a.agent_id}</p>
                    <div className="mt-1.5 flex items-center gap-2">
                      <div className="flex-1 h-1 bg-white/[0.08] rounded-full overflow-hidden">
                        <div
                          className="h-full bg-gradient-to-r from-cyan-500 to-emerald-500 rounded-full transition-all"
                          style={{ width: `${rate}%` }}
                        />
                      </div>
                      <span className="text-[10px] text-slate-500 flex-shrink-0">{rate}%</span>
                    </div>
                  </div>
                  <div className="text-right flex-shrink-0">
                    <p className="text-xs font-semibold text-slate-300">{a.total}</p>
                    <p className="text-[10px] text-slate-600">runs</p>
                  </div>
                </div>
              )
            })}
            {(!agentStats || agentStats.length === 0) && (
              <p className="px-5 py-8 text-center text-sm text-slate-600">No agent runs yet</p>
            )}
          </div>
        </div>
      </div>

      {/* Running now */}
      {(stats?.running ?? 0) > 0 && (
        <div className="card px-5 py-4 flex items-center gap-3 border-l-4 border-l-cyan-500/60">
          <Clock size={16} className="text-cyan-400 flex-shrink-0" />
          <p className="text-sm text-slate-300">
            <span className="font-semibold text-cyan-400">{stats?.running}</span> agent{stats?.running !== 1 ? 's' : ''} currently running
          </p>
        </div>
      )}
    </div>
  )
}
