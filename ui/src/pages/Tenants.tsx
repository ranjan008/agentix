import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2, Key } from 'lucide-react'
import { api } from '../api/client'

export default function Tenants() {
  const qc = useQueryClient()
  const { data: tenants = [], isLoading } = useQuery({ queryKey: ['tenants'], queryFn: api.listTenants })
  const [newKey, setNewKey] = useState<string | null>(null)

  const deleteMut = useMutation({
    mutationFn: api.deleteTenant,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tenants'] }),
  })

  const createSAMut = useMutation({
    mutationFn: ({ tenantId, body }: { tenantId: string; body: any }) =>
      api.createServiceAccount(tenantId, body),
    onSuccess: (data) => setNewKey(data.api_key),
  })

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Tenants</h1>
      </div>

      {newKey && (
        <div className="mb-4 p-4 bg-yellow-50 border border-yellow-200 rounded-xl">
          <p className="text-sm font-medium text-yellow-800 mb-1">New API Key — save this, it won't be shown again:</p>
          <code className="text-xs bg-yellow-100 px-2 py-1 rounded font-mono break-all">{newKey}</code>
          <button onClick={() => setNewKey(null)} className="ml-4 text-xs text-yellow-600 underline">Dismiss</button>
        </div>
      )}

      <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
        {isLoading ? (
          <p className="p-6 text-gray-400">Loading…</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                {['Tenant ID', 'Name', 'Tier', 'Actions'].map(h => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tenants.map((t: any) => (
                <tr key={t.tenant_id} className="border-b last:border-0 hover:bg-gray-50">
                  <td className="px-4 py-3 font-mono text-xs">{t.tenant_id}</td>
                  <td className="px-4 py-3">{t.name}</td>
                  <td className="px-4 py-3">
                    <span className="px-2 py-0.5 bg-gray-100 text-gray-600 rounded text-xs">{t.tier}</span>
                  </td>
                  <td className="px-4 py-3 flex gap-2">
                    <button
                      title="Create service account"
                      onClick={() => createSAMut.mutate({ tenantId: t.tenant_id, body: { name: `sa-${Date.now()}`, roles: ['operator'] } })}
                      className="text-indigo-400 hover:text-indigo-600"
                    >
                      <Key size={14} />
                    </button>
                    <button
                      onClick={() => {
                        if (confirm(`Delete tenant "${t.tenant_id}"?`)) deleteMut.mutate(t.tenant_id)
                      }}
                      className="text-red-400 hover:text-red-600"
                    >
                      <Trash2 size={14} />
                    </button>
                  </td>
                </tr>
              ))}
              {tenants.length === 0 && (
                <tr><td colSpan={4} className="px-4 py-8 text-center text-gray-400">No tenants</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
