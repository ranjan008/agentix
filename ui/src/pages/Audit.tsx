import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { ShieldCheck, ShieldAlert } from 'lucide-react'

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

  const entries = auditData?.entries ?? []

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Audit Log</h1>
        <div className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium ${
          chainStatus?.chain_valid === false
            ? 'bg-red-50 text-red-700'
            : 'bg-green-50 text-green-700'
        }`}>
          {chainStatus?.chain_valid === false
            ? <><ShieldAlert size={16} /> Chain tampered</>
            : <><ShieldCheck size={16} /> Chain valid</>
          }
        </div>
      </div>

      <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
        {isLoading ? (
          <p className="p-6 text-gray-400">Loading…</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                {['Action', 'Trigger ID', 'Agent', 'Identity', 'Tenant', 'Timestamp'].map(h => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {entries.map((e: any, i: number) => (
                <tr key={i} className="border-b last:border-0 hover:bg-gray-50">
                  <td className="px-4 py-2 font-mono text-xs text-indigo-600">{e.action}</td>
                  <td className="px-4 py-2 font-mono text-xs text-gray-400">{e.trigger_id?.slice(0, 16)}</td>
                  <td className="px-4 py-2 text-xs">{e.agent_id}</td>
                  <td className="px-4 py-2 text-xs text-gray-500">{e.identity_id}</td>
                  <td className="px-4 py-2 text-xs text-gray-400">{e.tenant_id}</td>
                  <td className="px-4 py-2 text-xs text-gray-400">{e.timestamp?.slice(0, 19)}</td>
                </tr>
              ))}
              {entries.length === 0 && (
                <tr><td colSpan={6} className="px-4 py-8 text-center text-gray-400">No audit entries</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
