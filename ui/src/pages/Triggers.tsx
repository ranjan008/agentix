import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, RotateCcw } from 'lucide-react'
import { api } from '../api/client'

export default function Triggers() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['triggers'],
    queryFn: () => api.listTriggers({ limit: 50 }),
    refetchInterval: 5000,
  })
  const replayMut = useMutation({
    mutationFn: api.replayTrigger,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['triggers'] }),
  })

  const triggers = data?.triggers ?? []

  const STATUS_COLORS: Record<string, string> = {
    running: 'bg-blue-100 text-blue-700',
    done: 'bg-green-100 text-green-700',
    failed: 'bg-red-100 text-red-700',
    queued: 'bg-yellow-100 text-yellow-700',
  }

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Triggers</h1>
        <button
          onClick={() => qc.invalidateQueries({ queryKey: ['triggers'] })}
          className="flex items-center gap-1 px-3 py-2 text-sm border rounded-lg hover:bg-gray-100"
        >
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
        {isLoading ? (
          <p className="p-6 text-gray-400">Loading…</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                {['Trigger ID', 'Agent', 'Channel', 'Identity', 'Status', 'Time', 'Actions'].map(h => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {triggers.map((t: any) => (
                <tr key={t.id} className="border-b last:border-0 hover:bg-gray-50">
                  <td className="px-4 py-3 font-mono text-xs text-gray-500">{t.id?.slice(0, 20)}</td>
                  <td className="px-4 py-3">{t.agent_id}</td>
                  <td className="px-4 py-3 text-gray-500">{t.channel}</td>
                  <td className="px-4 py-3 text-gray-500 text-xs">{t.caller?.identity_id}</td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_COLORS[t.status] ?? 'bg-gray-100 text-gray-600'}`}>
                      {t.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-400 text-xs">{t.timestamp?.slice(0, 19)}</td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => replayMut.mutate(t.id)}
                      title="Replay trigger"
                      className="text-indigo-400 hover:text-indigo-600 transition-colors"
                    >
                      <RotateCcw size={14} />
                    </button>
                  </td>
                </tr>
              ))}
              {triggers.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-8 text-center text-gray-400">No triggers yet</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
