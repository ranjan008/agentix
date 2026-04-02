import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Search, Download } from 'lucide-react'
import { api } from '../api/client'

export default function Skills() {
  const [query, setQuery] = useState('')
  const [installed, setInstalled] = useState<string | null>(null)

  const { data: skills = [], isLoading } = useQuery({ queryKey: ['skills'], queryFn: api.listSkills })
  const { data: marketplace = [], isFetching: searching } = useQuery({
    queryKey: ['marketplace', query],
    queryFn: () => api.searchMarketplace(query),
    enabled: query.length > 0,
  })

  const installMut = useMutation({
    mutationFn: (name: string) => api.installSkill(name, { verify_signature: true }),
    onSuccess: (_, name) => setInstalled(name),
  })

  return (
    <div className="p-8 space-y-6">
      <h1 className="text-2xl font-bold">Skills</h1>

      {installed && (
        <div className="p-4 bg-green-50 border border-green-200 rounded-xl text-sm text-green-800">
          Skill <strong>{installed}</strong> installed successfully.{' '}
          <button onClick={() => setInstalled(null)} className="underline">Dismiss</button>
        </div>
      )}

      {/* Installed skills */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-5">
        <h2 className="font-semibold mb-3">Available Skills</h2>
        {isLoading ? (
          <p className="text-gray-400 text-sm">Loading…</p>
        ) : (
          <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
            {skills.map((s: any) => (
              <div key={s.name} className="border rounded-lg p-3 hover:border-indigo-300 transition-colors">
                <p className="font-medium text-sm">{s.name}</p>
                <p className="text-xs text-gray-400 mt-0.5 truncate">{s.description}</p>
                <p className="text-xs text-gray-300 mt-1">v{s.version}</p>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Marketplace */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-5">
        <h2 className="font-semibold mb-3">Marketplace</h2>
        <div className="relative mb-4">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            placeholder="Search skills…"
            value={query}
            onChange={e => setQuery(e.target.value)}
            className="w-full pl-8 pr-4 py-2 text-sm border rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-300"
          />
        </div>

        {searching ? (
          <p className="text-gray-400 text-sm">Searching…</p>
        ) : marketplace.length > 0 ? (
          <div className="space-y-2">
            {marketplace.map((s: any) => (
              <div key={s.name} className="flex items-center justify-between p-3 border rounded-lg hover:bg-gray-50">
                <div>
                  <p className="text-sm font-medium">{s.name}</p>
                  <p className="text-xs text-gray-400">{s.description}</p>
                </div>
                <button
                  onClick={() => installMut.mutate(s.name)}
                  disabled={installMut.isPending}
                  className="flex items-center gap-1 px-3 py-1.5 bg-indigo-600 text-white text-xs rounded-lg hover:bg-indigo-700 disabled:opacity-50"
                >
                  <Download size={12} /> Install
                </button>
              </div>
            ))}
          </div>
        ) : query ? (
          <p className="text-gray-400 text-sm">No results for "{query}"</p>
        ) : (
          <p className="text-gray-400 text-sm">Type to search the marketplace…</p>
        )}
      </div>
    </div>
  )
}
