import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2, RefreshCw } from 'lucide-react'
import { api } from '../api/client'

export default function Agents() {
  const qc = useQueryClient()
  const { data: agents = [], isLoading } = useQuery({ queryKey: ['agents'], queryFn: api.listAgents })
  const deleteMut = useMutation({
    mutationFn: api.deleteAgent,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agents'] }),
  })

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Agents</h1>
        <div className="flex gap-2">
          <button
            onClick={() => qc.invalidateQueries({ queryKey: ['agents'] })}
            className="flex items-center gap-1 px-3 py-2 text-sm border rounded-lg hover:bg-gray-100"
          >
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </div>

      {isLoading ? (
        <p className="text-gray-400">Loading…</p>
      ) : (
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                {['Agent ID', 'Description', 'Skills', 'Version', 'Actions'].map(h => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {agents.map((a: any) => (
                <tr key={a.agent_id} className="border-b last:border-0 hover:bg-gray-50">
                  <td className="px-4 py-3 font-mono text-xs">{a.agent_id}</td>
                  <td className="px-4 py-3 text-gray-600">{a.spec?.description || a.metadata?.description || '—'}</td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {(a.spec?.skills || []).map((s: string) => (
                        <span key={s} className="px-1.5 py-0.5 bg-indigo-50 text-indigo-600 rounded text-xs">{s}</span>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-gray-400 text-xs">{a.metadata?.version || '—'}</td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => {
                        if (confirm(`Delete agent "${a.agent_id}"?`)) deleteMut.mutate(a.agent_id)
                      }}
                      className="text-red-400 hover:text-red-600 transition-colors"
                    >
                      <Trash2 size={14} />
                    </button>
                  </td>
                </tr>
              ))}
              {agents.length === 0 && (
                <tr><td colSpan={5} className="px-4 py-8 text-center text-gray-400">No agents registered</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
