import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Bot, Trash2, RefreshCw, Cpu, Tag } from 'lucide-react'
import { api } from '../api/client'
import { Page, PageHeader, Card, Table, TableRow, Td, EmptyState, Skeleton, MonoId, Btn } from '../components/ui'

export default function Agents() {
  const qc = useQueryClient()
  const { data: agents = [], isLoading } = useQuery({ queryKey: ['agents'], queryFn: api.listAgents })
  const deleteMut = useMutation({
    mutationFn: api.deleteAgent,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agents'] }),
  })

  return (
    <Page>
      <PageHeader
        title="Agents"
        subtitle={`${agents.length} registered agent${agents.length !== 1 ? 's' : ''}`}
        actions={
          <Btn onClick={() => qc.invalidateQueries({ queryKey: ['agents'] })} size="sm">
            <RefreshCw size={13} /> Refresh
          </Btn>
        }
      />

      <Card>
        {isLoading ? <Skeleton rows={4} cols={5} /> : (
          <Table headers={['Agent', 'Description', 'Model', 'Skills', 'Version', '']}>
            {agents.length === 0 && (
              <tr><td colSpan={6}>
                <EmptyState icon={Bot} title="No agents registered"
                  desc="Register an agent YAML with `agentix agent register agents/my-agent.yaml`" />
              </td></tr>
            )}
            {agents.map((a: any) => {
              const id = a.id ?? a.name ?? a.agent_id
              const spec = a.spec ?? {}
              const meta = a.metadata ?? {}
              const skills: string[] = spec.skills ?? []
              const model = spec.llm?.model ?? spec.model ?? meta.model ?? null

              return (
                <TableRow key={id}>
                  <Td>
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-8 rounded-xl bg-indigo-50 flex items-center justify-center flex-shrink-0">
                        <Bot size={15} className="text-indigo-600" />
                      </div>
                      <div>
                        <p className="font-medium text-gray-900 text-sm">{id}</p>
                        <MonoId value={meta.name ?? id} len={22} />
                      </div>
                    </div>
                  </Td>
                  <Td className="max-w-xs">
                    <p className="text-xs text-gray-600 line-clamp-2 leading-relaxed">
                      {spec.description ?? meta.description ?? '—'}
                    </p>
                  </Td>
                  <Td>
                    {model ? (
                      <div className="flex items-center gap-1.5 text-xs text-gray-500">
                        <Cpu size={11} className="text-gray-400" />
                        {model}
                      </div>
                    ) : <span className="text-gray-300 text-xs">—</span>}
                  </Td>
                  <Td>
                    <div className="flex flex-wrap gap-1">
                      {skills.length === 0
                        ? <span className="text-gray-300 text-xs">—</span>
                        : skills.map((s: string) => (
                          <span key={s} className="inline-flex items-center gap-1 px-2 py-0.5 bg-violet-50 text-violet-700 rounded-full text-[10px] font-medium border border-violet-100">
                            <Tag size={9} />{s}
                          </span>
                        ))
                      }
                    </div>
                  </Td>
                  <Td>
                    <span className="text-xs text-gray-400">{meta.version ?? a.version ?? '—'}</span>
                  </Td>
                  <Td>
                    <button
                      onClick={() => { if (window.confirm(`Delete agent "${id}"?`)) deleteMut.mutate(id) }}
                      title="Delete agent"
                      className="p-1.5 text-gray-300 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors"
                    >
                      <Trash2 size={14} />
                    </button>
                  </Td>
                </TableRow>
              )
            })}
          </Table>
        )}
      </Card>
    </Page>
  )
}
