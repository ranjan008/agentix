import { useQuery } from '@tanstack/react-query'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'
import { api } from '../api/client'

export default function Metrics() {
  const { data: stats } = useQuery({ queryKey: ['trigger-stats-24'], queryFn: () => api.triggerStats(24) })
  const { data: cost } = useQuery({ queryKey: ['cost-summary'], queryFn: () => api.costSummary() })
  const { data: agentStats = [] } = useQuery({ queryKey: ['agent-stats'], queryFn: api.agentStats })

  return (
    <div className="p-8 space-y-8">
      <h1 className="text-2xl font-bold">Metrics</h1>

      {/* Cost summary */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
        <h2 className="font-semibold mb-4">Cost (all time)</h2>
        <div className="grid grid-cols-3 gap-4">
          <div>
            <p className="text-sm text-gray-500">Total USD</p>
            <p className="text-2xl font-bold">${(cost?.total_usd ?? 0).toFixed(4)}</p>
          </div>
          <div>
            <p className="text-sm text-gray-500">Input Tokens</p>
            <p className="text-2xl font-bold">{(cost?.total_input_tokens ?? 0).toLocaleString()}</p>
          </div>
          <div>
            <p className="text-sm text-gray-500">Output Tokens</p>
            <p className="text-2xl font-bold">{(cost?.total_output_tokens ?? 0).toLocaleString()}</p>
          </div>
        </div>
      </div>

      {/* Agent execution stats */}
      {agentStats.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
          <h2 className="font-semibold mb-4">Agent Execution Counts</h2>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={agentStats} margin={{ top: 4, right: 8, left: -8, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="agent_id" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip />
              <Bar dataKey="total" fill="#6366f1" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Trigger stats */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
        <h2 className="font-semibold mb-4">Trigger Stats (24h)</h2>
        <div className="grid grid-cols-4 gap-4">
          {[
            { label: 'Total', key: 'total', color: 'text-gray-700' },
            { label: 'Running', key: 'running', color: 'text-blue-600' },
            { label: 'Completed', key: 'done', color: 'text-green-600' },
            { label: 'Failed', key: 'failed', color: 'text-red-600' },
          ].map(({ label, key, color }) => (
            <div key={key}>
              <p className="text-sm text-gray-500">{label}</p>
              <p className={`text-2xl font-bold ${color}`}>{stats?.[key] ?? 0}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
