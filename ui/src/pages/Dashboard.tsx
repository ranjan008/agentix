import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { Bot, Zap, AlertTriangle, CheckCircle } from 'lucide-react'

function StatCard({ title, value, icon: Icon, color }: { title: string; value: any; icon: any; color: string }) {
  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-5 flex items-center gap-4">
      <div className={`p-3 rounded-lg ${color}`}>
        <Icon size={20} className="text-white" />
      </div>
      <div>
        <p className="text-sm text-gray-500">{title}</p>
        <p className="text-2xl font-bold">{value ?? '—'}</p>
      </div>
    </div>
  )
}

export default function Dashboard() {
  const { data: agents } = useQuery({ queryKey: ['agents'], queryFn: api.listAgents })
  const { data: stats } = useQuery({ queryKey: ['trigger-stats'], queryFn: () => api.triggerStats(24) })

  return (
    <div className="p-8">
      <h1 className="text-2xl font-bold mb-6">Dashboard</h1>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatCard title="Agents" value={agents?.length} icon={Bot} color="bg-indigo-500" />
        <StatCard title="Triggers (24h)" value={stats?.total} icon={Zap} color="bg-blue-500" />
        <StatCard title="Completed" value={stats?.done} icon={CheckCircle} color="bg-green-500" />
        <StatCard title="Failed" value={stats?.failed} icon={AlertTriangle} color="bg-red-500" />
      </div>

      <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-5">
        <h2 className="font-semibold mb-3">Recent Triggers</h2>
        <RecentTriggers />
      </div>
    </div>
  )
}

function RecentTriggers() {
  const { data, isLoading } = useQuery({
    queryKey: ['triggers-recent'],
    queryFn: () => api.listTriggers({ limit: 10 }),
    refetchInterval: 5000,
  })

  if (isLoading) return <p className="text-gray-400 text-sm">Loading…</p>
  const triggers = data?.triggers ?? []

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-gray-400 border-b">
          <th className="pb-2 font-medium">ID</th>
          <th className="pb-2 font-medium">Agent</th>
          <th className="pb-2 font-medium">Channel</th>
          <th className="pb-2 font-medium">Status</th>
          <th className="pb-2 font-medium">Timestamp</th>
        </tr>
      </thead>
      <tbody>
        {triggers.map((t: any) => (
          <tr key={t.id} className="border-b last:border-0 hover:bg-gray-50">
            <td className="py-2 font-mono text-xs text-gray-500">{t.id?.slice(0, 16)}</td>
            <td className="py-2">{t.agent_id}</td>
            <td className="py-2">{t.channel}</td>
            <td className="py-2">
              <StatusBadge status={t.status} />
            </td>
            <td className="py-2 text-gray-400 text-xs">{t.timestamp?.slice(0, 19)}</td>
          </tr>
        ))}
        {triggers.length === 0 && (
          <tr><td colSpan={5} className="py-4 text-center text-gray-400">No triggers yet</td></tr>
        )}
      </tbody>
    </table>
  )
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    running: 'bg-blue-100 text-blue-700',
    done: 'bg-green-100 text-green-700',
    failed: 'bg-red-100 text-red-700',
    queued: 'bg-yellow-100 text-yellow-700',
  }
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${colors[status] ?? 'bg-gray-100 text-gray-600'}`}>
      {status}
    </span>
  )
}
