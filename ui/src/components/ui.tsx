/**
 * Shared UI primitives — dark AI agentic theme.
 */
import { ReactNode } from 'react'
import { LucideIcon } from 'lucide-react'

// ── Page shell ──────────────────────────────────────────────────────────────

export function PageHeader({
  title, subtitle, actions,
}: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="flex items-start justify-between mb-8">
      <div>
        <h1 className="text-xl font-bold text-slate-100 tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2 flex-shrink-0">{actions}</div>}
    </div>
  )
}

export function Page({ children }: { children: ReactNode }) {
  return <div className="p-8 max-w-screen-xl mx-auto">{children}</div>
}

// ── Card ─────────────────────────────────────────────────────────────────────

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`card overflow-hidden ${className}`}>
      {children}
    </div>
  )
}

export function CardHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.06]">
      <div>
        <p className="font-semibold text-slate-200 text-sm">{title}</p>
        {subtitle && <p className="text-xs text-slate-500 mt-0.5">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}

// ── Table ────────────────────────────────────────────────────────────────────

export function Table({ headers, children }: {
  headers: string[]; children: ReactNode
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/[0.06] bg-white/[0.02]">
            {headers.map(h => (
              <th key={h} className="px-5 py-3 text-left text-[11px] font-semibold text-slate-600 uppercase tracking-wider">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.04]">{children}</tbody>
      </table>
    </div>
  )
}

export function TableRow({ children, className = '', onClick }: { children: ReactNode; className?: string; onClick?: () => void }) {
  return <tr onClick={onClick} className={`hover:bg-white/[0.03] transition-colors ${className}`}>{children}</tr>
}

export function Td({ children, className = '' }: { children?: ReactNode; className?: string }) {
  return <td className={`px-5 py-3.5 ${className}`}>{children}</td>
}

export function EmptyState({ icon: Icon, title, desc }: { icon: LucideIcon; title: string; desc?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-6 text-center">
      <div className="w-12 h-12 rounded-2xl bg-white/[0.05] flex items-center justify-center mb-4">
        <Icon size={22} className="text-slate-600" />
      </div>
      <p className="text-sm font-medium text-slate-400">{title}</p>
      {desc && <p className="text-xs text-slate-600 mt-1 max-w-xs">{desc}</p>}
    </div>
  )
}

// ── Status badge ──────────────────────────────────────────────────────────────

const STATUS_MAP: Record<string, string> = {
  done:      'badge badge-green',
  running:   'badge badge-blue',
  failed:    'badge badge-red',
  queued:    'badge badge-yellow',
  pending:   'badge badge-yellow',
  standard:  'badge badge-gray',
  enterprise:'badge badge-purple',
  lite:      'badge badge-gray',
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={STATUS_MAP[status] ?? 'badge badge-gray'}>
      {status}
    </span>
  )
}

// ── Skeleton loader ───────────────────────────────────────────────────────────

export function Skeleton({ rows = 5, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="divide-y divide-white/[0.04]">
      {[...Array(rows)].map((_, i) => (
        <div key={i} className="px-5 py-4 flex gap-4">
          {[...Array(cols)].map((_, j) => (
            <div key={j} className={`h-4 bg-white/[0.06] rounded animate-pulse ${j === 0 ? 'w-32' : 'flex-1'}`} />
          ))}
        </div>
      ))}
    </div>
  )
}

// ── Button ────────────────────────────────────────────────────────────────────

export function Btn({
  children, onClick, variant = 'secondary', size = 'md', disabled, title, className = '',
}: {
  children: ReactNode; onClick?: () => void; variant?: 'primary' | 'secondary' | 'danger' | 'ghost'
  size?: 'sm' | 'md'; disabled?: boolean; title?: string; className?: string
}) {
  const base = 'inline-flex items-center gap-1.5 font-medium rounded-xl transition-all duration-150 focus:outline-none focus:ring-2 focus:ring-offset-1 focus:ring-offset-slate-900 disabled:opacity-40 disabled:cursor-not-allowed'
  const variants = {
    primary:   'bg-cyan-500 hover:bg-cyan-400 text-slate-950 focus:ring-cyan-500/50 shadow-sm shadow-cyan-500/20',
    secondary: 'bg-white/[0.05] hover:bg-white/[0.09] text-slate-300 border border-white/[0.08] focus:ring-slate-500/30',
    danger:    'bg-transparent hover:bg-red-500/10 text-red-400 border border-red-500/20 focus:ring-red-500/30',
    ghost:     'text-slate-500 hover:text-slate-300 hover:bg-white/[0.06] focus:ring-slate-500/30',
  }
  const sizes = { sm: 'px-3 py-1.5 text-xs', md: 'px-4 py-2 text-sm' }
  return (
    <button className={`${base} ${variants[variant]} ${sizes[size]} ${className}`}
      onClick={onClick} disabled={disabled} title={title}>
      {children}
    </button>
  )
}

// ── Mono ID ───────────────────────────────────────────────────────────────────

export function MonoId({ value, len = 18 }: { value?: string; len?: number }) {
  if (!value) return <span className="text-slate-700">—</span>
  return (
    <span className="font-mono text-xs text-slate-400 bg-white/[0.05] px-2 py-0.5 rounded-md border border-white/[0.06]">
      {String(value).slice(0, len)}
    </span>
  )
}

// ── Timestamp ─────────────────────────────────────────────────────────────────

export function Timestamp({ value }: { value?: string | number }) {
  if (!value) return <span className="text-slate-700 text-xs">—</span>
  const ms = typeof value === 'number' ? value * 1000 : new Date(value).getTime()
  const d = new Date(ms)
  return (
    <span className="text-xs text-slate-500">
      {d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' })}{' '}
      <span className="text-slate-600">{d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}</span>
    </span>
  )
}
