import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Trash2, Key, Building2, Shield, Copy, Check } from 'lucide-react'
import { api } from '../api/client'
import { Page, PageHeader, Card, CardHeader, Table, TableRow, Td, EmptyState, Skeleton, Btn } from '../components/ui'

function CopyableKey({ value }: { value: string }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard.writeText(value)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }
  return (
    <div className="flex items-center gap-2 mt-2">
      <code className="flex-1 text-xs bg-amber-500/10 text-amber-300 px-3 py-1.5 rounded-lg font-mono break-all border border-amber-500/20">
        {value}
      </code>
      <button onClick={copy}
        className="flex-shrink-0 p-1.5 rounded-lg hover:bg-amber-500/10 text-amber-400 transition-colors">
        {copied ? <Check size={14} className="text-emerald-400" /> : <Copy size={14} />}
      </button>
    </div>
  )
}

const TIER_STYLE: Record<string, string> = {
  standard:   'bg-slate-500/10 text-slate-400 border border-slate-500/20',
  enterprise: 'bg-violet-500/10 text-violet-400 border border-violet-500/20',
  lite:       'bg-cyan-500/10 text-cyan-400 border border-cyan-500/20',
}

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
    <Page>
      <PageHeader
        title="Tenants"
        subtitle={`${tenants.length} tenant${tenants.length !== 1 ? 's' : ''} in this instance`}
      />

      {newKey && (
        <div className="mb-6 p-5 bg-amber-500/10 border border-amber-500/20 rounded-2xl">
          <div className="flex items-start gap-3">
            <div className="w-8 h-8 rounded-xl bg-amber-500/10 flex items-center justify-center flex-shrink-0 mt-0.5">
              <Key size={15} className="text-amber-400" />
            </div>
            <div className="flex-1">
              <p className="text-sm font-semibold text-amber-300">New API Key generated</p>
              <p className="text-xs text-amber-500 mt-0.5">Save this now — it won't be shown again.</p>
              <CopyableKey value={newKey} />
            </div>
            <button onClick={() => setNewKey(null)}
              className="text-amber-500 hover:text-amber-400 text-xs font-medium flex-shrink-0 transition-colors">
              Dismiss
            </button>
          </div>
        </div>
      )}

      <Card>
        <CardHeader title="All Tenants" subtitle="Multi-tenant access control" />
        {isLoading ? <Skeleton rows={3} cols={4} /> : (
          <Table headers={['Tenant', 'Name', 'Tier', 'Actions']}>
            {tenants.length === 0 && (
              <tr><td colSpan={4}>
                <EmptyState icon={Building2} title="No tenants configured"
                  desc="Tenants allow you to isolate agent access and billing per organisation." />
              </td></tr>
            )}
            {tenants.map((t: any) => (
              <TableRow key={t.tenant_id}>
                <Td>
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-xl bg-white/[0.05] flex items-center justify-center flex-shrink-0">
                      <Building2 size={14} className="text-slate-400" />
                    </div>
                    <code className="text-xs text-slate-400 font-mono">{t.tenant_id}</code>
                  </div>
                </Td>
                <Td>
                  <span className="text-sm font-medium text-slate-200">{t.name}</span>
                </Td>
                <Td>
                  <span className={`text-[10px] font-semibold px-2.5 py-1 rounded-full uppercase tracking-wide ${TIER_STYLE[t.tier] ?? TIER_STYLE.standard}`}>
                    {t.tier}
                  </span>
                </Td>
                <Td>
                  <div className="flex items-center gap-1.5">
                    <button
                      title="Create service account API key"
                      onClick={() => createSAMut.mutate({
                        tenantId: t.tenant_id,
                        body: { name: `sa-${Date.now()}`, roles: ['operator'] }
                      })}
                      className="p-1.5 text-slate-700 hover:text-cyan-400 hover:bg-cyan-500/10 rounded-lg transition-colors"
                    >
                      <Key size={14} />
                    </button>
                    <button
                      title="Delete tenant"
                      onClick={() => {
                        if (window.confirm(`Delete tenant "${t.tenant_id}"? This cannot be undone.`))
                          deleteMut.mutate(t.tenant_id)
                      }}
                      className="p-1.5 text-slate-700 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-colors"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </Td>
              </TableRow>
            ))}
          </Table>
        )}
      </Card>

      {/* RBAC info panel */}
      <div className="mt-6 p-5 card flex items-start gap-4">
        <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-cyan-500 to-indigo-600 flex items-center justify-center flex-shrink-0 shadow-md shadow-cyan-500/20">
          <Shield size={16} className="text-white" />
        </div>
        <div>
          <p className="text-sm font-semibold text-slate-200">Role-based Access Control</p>
          <p className="text-xs text-slate-500 mt-1 leading-relaxed">
            Each tenant is isolated. Service accounts use API keys prefixed <code className="text-cyan-400">sk-agentix-</code>.
            Roles: <span className="text-slate-400">end-user → operator → agent-author → tenant-admin → platform-admin</span>
          </p>
        </div>
      </div>
    </Page>
  )
}
