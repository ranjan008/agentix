import { useQuery } from '@tanstack/react-query'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Cell } from 'recharts'
import { BarChart2, DollarSign, Cpu, CheckCircle2, XCircle, Activity } from 'lucide-react'
import { api } from '../api/client'
import { Page, PageHeader, Card, CardHeader } from '../components/ui'

function MetricTile({ label, value, sub, icon: Icon, color }: {
  label: string; value: string | number; sub?: string; icon: any; color: string
}) {
  return (
    <div className="p-5 flex items-start gap-4">
      <div className={`w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0 ${color} shadow-lg`}>
        <Icon size={18} className="text-white" />
      </div>
      <div>
        <p className="text-xs text-slate-500 font-medium uppercase tracking-wide">{label}</p>
        <p className="text-2xl font-bold text-slate-100 mt-0.5 tabular-nums">{value}</p>
        {sub && <p className="text-xs text-slate-600 mt-0.5">{sub}</p>}
      </div>
    </div>
  )
}

const CustomTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-slate-800 border border-white/[0.08] rounded-xl shadow-xl px-4 py-3 text-sm">
      <p className="font-semibold text-slate-200 mb-1">{label}</p>
      {payload.map((p: any) => (
        <p key={p.name} style={{ color: p.color }} className="text-xs">
          {p.name}: <strong>{p.value}</strong>
        </p>
      ))}
    </div>
  )
}

const COLORS = ['#06b6d4', '#6366f1', '#8b5cf6', '#f59e0b', '#10b981']

export default function Metrics() {
  const { data: stats } = useQuery({ queryKey: ['trigger-stats-24'], queryFn: () => api.triggerStats(24) })
  const { data: cost } = useQuery({ queryKey: ['cost-summary'], queryFn: () => api.costSummary() })
  const { data: agentStats = [] } = useQuery({ queryKey: ['agent-stats'], queryFn: api.agentStats })

  const successRate = stats?.total
    ? Math.round((stats.done / stats.total) * 100)
    : 0

  return (
    <Page>
      <PageHeader title="Metrics" subtitle="Platform performance and cost analytics" />

      {/* Cost row */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        <Card>
          <MetricTile label="Total Cost" value={`$${(cost?.total_usd ?? 0).toFixed(4)}`}
            sub="All time" icon={DollarSign} color="bg-gradient-to-br from-emerald-500 to-emerald-700" />
        </Card>
        <Card>
          <MetricTile label="Input Tokens" value={(cost?.total_input_tokens ?? 0).toLocaleString()}
            sub="All time" icon={Cpu} color="bg-gradient-to-br from-indigo-500 to-indigo-700" />
        </Card>
        <Card>
          <MetricTile label="Output Tokens" value={(cost?.total_output_tokens ?? 0).toLocaleString()}
            sub="All time" icon={Activity} color="bg-gradient-to-br from-violet-500 to-violet-700" />
        </Card>
      </div>

      {/* Trigger stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
        {[
          { label: 'Total (24h)', value: stats?.total ?? 0,   icon: BarChart2,   color: 'bg-gradient-to-br from-slate-600 to-slate-800' },
          { label: 'Running',    value: stats?.running ?? 0,  icon: Activity,    color: 'bg-gradient-to-br from-cyan-500 to-cyan-700' },
          { label: 'Completed',  value: stats?.done ?? 0,     icon: CheckCircle2,color: 'bg-gradient-to-br from-emerald-500 to-emerald-700' },
          { label: 'Failed',     value: stats?.failed ?? 0,   icon: XCircle,     color: 'bg-gradient-to-br from-rose-500 to-rose-700' },
        ].map(({ label, value, icon, color }) => (
          <Card key={label}>
            <MetricTile label={label} value={value} icon={icon} color={color} />
          </Card>
        ))}
      </div>

      {/* Success rate + chart */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        {/* Success rate gauge */}
        <Card>
          <CardHeader title="Success Rate" subtitle="Last 24 hours" />
          <div className="p-6 flex flex-col items-center justify-center">
            <div className="relative w-32 h-32">
              <svg className="w-full h-full -rotate-90" viewBox="0 0 36 36">
                <circle cx="18" cy="18" r="15.9" fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="3.2" />
                <circle cx="18" cy="18" r="15.9" fill="none"
                  stroke={successRate >= 90 ? '#10b981' : successRate >= 70 ? '#f59e0b' : '#ef4444'}
                  strokeWidth="3.2"
                  strokeDasharray={`${successRate} ${100 - successRate}`}
                  strokeLinecap="round"
                  className="transition-all duration-700"
                />
              </svg>
              <div className="absolute inset-0 flex flex-col items-center justify-center">
                <span className="text-2xl font-bold text-slate-100">{successRate}%</span>
                <span className="text-[10px] text-slate-500">success</span>
              </div>
            </div>
            <div className="mt-4 flex gap-4 text-xs text-slate-500">
              <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-emerald-500" />{stats?.done ?? 0} done</span>
              <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-rose-500" />{stats?.failed ?? 0} failed</span>
            </div>
          </div>
        </Card>

        {/* Agent bar chart */}
        <Card className="xl:col-span-2">
          <CardHeader title="Agent Execution Counts" subtitle="All time" />
          {agentStats.length === 0 ? (
            <div className="py-16 text-center">
              <BarChart2 size={28} className="mx-auto text-slate-700 mb-3" />
              <p className="text-sm text-slate-500">No agent runs yet</p>
            </div>
          ) : (
            <div className="px-4 py-5">
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={agentStats} margin={{ top: 4, right: 8, left: -16, bottom: 4 }} barGap={4}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" vertical={false} />
                  <XAxis dataKey="agent_id" tick={{ fontSize: 11, fill: '#475569' }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fontSize: 11, fill: '#475569' }} axisLine={false} tickLine={false} />
                  <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(255,255,255,0.03)' }} />
                  <Bar dataKey="total" name="Total" radius={[6, 6, 0, 0]} maxBarSize={48}>
                    {agentStats.map((_: any, i: number) => (
                      <Cell key={i} fill={COLORS[i % COLORS.length]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>
      </div>
    </Page>
  )
}
