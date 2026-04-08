import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Package, Search, Download, CheckCircle2, Blocks } from 'lucide-react'
import { api } from '../api/client'
import { Page, PageHeader, Card, CardHeader, EmptyState, Skeleton, Btn } from '../components/ui'

export default function Skills() {
  const qc = useQueryClient()
  const [query, setQuery] = useState('')
  const [installed, setInstalled] = useState<string | null>(null)

  const { data: skills = [], isLoading } = useQuery({ queryKey: ['skills'], queryFn: api.listSkills })
  const { data: marketplace = [], isFetching: searching } = useQuery({
    queryKey: ['marketplace', query],
    queryFn: () => api.searchMarketplace(query),
    enabled: query.length > 1,
  })

  const installMut = useMutation({
    mutationFn: (name: string) => api.installSkill(name, { verify_signature: true }),
    onSuccess: (_, name) => {
      setInstalled(name)
      qc.invalidateQueries({ queryKey: ['skills'] })
    },
  })

  const SOURCE_COLORS: Record<string, string> = {
    builtin: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
    hub:     'bg-indigo-500/10 text-indigo-400 border-indigo-500/20',
    local:   'bg-amber-500/10 text-amber-400 border-amber-500/20',
    git:     'bg-violet-500/10 text-violet-400 border-violet-500/20',
  }

  return (
    <Page>
      <PageHeader
        title="Skills"
        subtitle="Extend agents with tools and capabilities"
      />

      {installed && (
        <div className="mb-6 flex items-center gap-3 p-4 bg-emerald-500/10 border border-emerald-500/20 rounded-2xl text-sm text-emerald-400">
          <CheckCircle2 size={16} className="flex-shrink-0" />
          <span>Skill <strong>{installed}</strong> installed successfully.</span>
          <button onClick={() => setInstalled(null)} className="ml-auto text-xs text-emerald-500 hover:text-emerald-400 transition-colors">Dismiss</button>
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        {/* Installed skills */}
        <Card>
          <CardHeader title="Installed Skills" subtitle={`${skills.length} available`} />
          <div className="p-5">
            {isLoading ? <Skeleton rows={3} cols={3} /> : skills.length === 0 ? (
              <EmptyState icon={Blocks} title="No skills installed"
                desc="Install from the marketplace or register a local skill." />
            ) : (
              <div className="grid grid-cols-1 gap-2">
                {skills.map((s: any) => (
                  <div key={s.name}
                    className="flex items-center gap-3 p-3 rounded-xl border border-white/[0.06] hover:border-cyan-500/20 hover:bg-cyan-500/[0.03] transition-all group">
                    <div className="w-9 h-9 rounded-xl bg-white/[0.06] group-hover:bg-cyan-500/10 flex items-center justify-center flex-shrink-0 transition-colors">
                      <Package size={16} className="text-slate-500 group-hover:text-cyan-400 transition-colors" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-semibold text-slate-200">{s.name}</p>
                      {s.description && <p className="text-xs text-slate-500 mt-0.5 truncate">{s.description}</p>}
                    </div>
                    <div className="flex items-center gap-2 flex-shrink-0">
                      <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full border ${SOURCE_COLORS[s.source] ?? 'bg-slate-500/10 text-slate-400 border-slate-500/20'}`}>
                        {s.source}
                      </span>
                      <span className="text-[10px] text-slate-600">v{s.version}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </Card>

        {/* Marketplace */}
        <Card>
          <CardHeader title="Marketplace" subtitle="Discover and install new skills" />
          <div className="p-5 space-y-4">
            <div className="relative">
              <Search size={14} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-600 pointer-events-none" />
              <input
                type="text"
                placeholder="Search skills…"
                value={query}
                onChange={e => setQuery(e.target.value)}
                className="input pl-9"
              />
            </div>

            {searching ? (
              <Skeleton rows={3} cols={2} />
            ) : marketplace.length > 0 ? (
              <div className="space-y-2">
                {marketplace.map((s: any) => (
                  <div key={s.name}
                    className="flex items-center gap-3 p-3 rounded-xl border border-white/[0.06] hover:border-cyan-500/20 transition-all">
                    <div className="w-9 h-9 rounded-xl bg-cyan-500/10 flex items-center justify-center flex-shrink-0">
                      <Package size={15} className="text-cyan-400" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-semibold text-slate-200">{s.name}</p>
                      {s.description && <p className="text-xs text-slate-500 mt-0.5 truncate">{s.description}</p>}
                    </div>
                    <Btn
                      variant="primary" size="sm"
                      onClick={() => installMut.mutate(s.name)}
                      disabled={installMut.isPending}
                    >
                      <Download size={12} /> Install
                    </Btn>
                  </div>
                ))}
              </div>
            ) : (
              <div className="py-10 text-center">
                <Search size={28} className="mx-auto text-slate-700 mb-3" />
                <p className="text-sm text-slate-500">
                  {query ? `No results for "${query}"` : 'Type to search the marketplace'}
                </p>
              </div>
            )}
          </div>
        </Card>
      </div>
    </Page>
  )
}
