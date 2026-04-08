/**
 * Shared UI primitives used across all pages.
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
        <h1 className="text-xl font-bold text-gray-900 tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-gray-500">{subtitle}</p>}
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
    <div className={`bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden ${className}`}>
      {children}
    </div>
  )
}

export function CardHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
      <div>
        <p className="font-semibold text-gray-900 text-sm">{title}</p>
        {subtitle && <p className="text-xs text-gray-400 mt-0.5">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}

// ── Table ────────────────────────────────────────────────────────────────────

export function Table({ headers, children, colSpan }: {
  headers: string[]; children: ReactNode; colSpan?: number
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-100 bg-gray-50/50">
            {headers.map(h => (
              <th key={h} className="px-5 py-3 text-left text-[11px] font-semibold text-gray-400 uppercase tracking-wider">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-50">{children}</tbody>
      </table>
    </div>
  )
}

export function TableRow({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <tr className={`hover:bg-slate-50/80 transition-colors ${className}`}>{children}</tr>
}

export function Td({ children, className = '' }: { children?: ReactNode; className?: string }) {
  return <td className={`px-5 py-3.5 ${className}`}>{children}</td>
}

export function EmptyState({ icon: Icon, title, desc }: { icon: LucideIcon; title: string; desc?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-6 text-center">
      <div className="w-12 h-12 rounded-2xl bg-gray-100 flex items-center justify-center mb-4">
        <Icon size={22} className="text-gray-400" />
      </div>
      <p className="text-sm font-medium text-gray-700">{title}</p>
      {desc && <p className="text-xs text-gray-400 mt-1 max-w-xs">{desc}</p>}
    </div>
  )
}

// ── Status badge ──────────────────────────────────────────────────────────────

const STATUS_MAP: Record<string, string> = {
  done:     'badge badge-green',
  running:  'badge badge-blue',
  failed:   'badge badge-red',
  queued:   'badge badge-yellow',
  pending:  'badge badge-yellow',
  standard: 'badge badge-gray',
  enterprise:'badge badge-purple',
  lite:     'badge badge-gray',
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
    <div className="divide-y divide-gray-50">
      {[...Array(rows)].map((_, i) => (
        <div key={i} className="px-5 py-4 flex gap-4">
          {[...Array(cols)].map((_, j) => (
            <div key={j} className={`h-4 bg-gray-100 rounded animate-pulse ${j === 0 ? 'w-32' : 'flex-1'}`} />
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
  const base = 'inline-flex items-center gap-1.5 font-medium rounded-xl transition-all duration-150 focus:outline-none focus:ring-2 focus:ring-offset-1 disabled:opacity-40 disabled:cursor-not-allowed'
  const variants = {
    primary:   'bg-indigo-600 hover:bg-indigo-700 text-white focus:ring-indigo-500 shadow-sm',
    secondary: 'bg-white hover:bg-gray-50 text-gray-700 border border-gray-200 focus:ring-gray-300',
    danger:    'bg-white hover:bg-red-50 text-red-600 border border-red-200 focus:ring-red-300',
    ghost:     'text-gray-500 hover:text-gray-700 hover:bg-gray-100 focus:ring-gray-300',
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
  if (!value) return <span className="text-gray-300">—</span>
  return (
    <span className="font-mono text-xs text-gray-400 bg-gray-50 px-2 py-0.5 rounded-md border border-gray-100">
      {String(value).slice(0, len)}
    </span>
  )
}

// ── Timestamp ─────────────────────────────────────────────────────────────────

export function Timestamp({ value }: { value?: string | number }) {
  if (!value) return <span className="text-gray-300 text-xs">—</span>
  const ms = typeof value === 'number' ? value * 1000 : new Date(value).getTime()
  const d = new Date(ms)
  return (
    <span className="text-xs text-gray-400">
      {d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' })}{' '}
      <span className="text-gray-300">{d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}</span>
    </span>
  )
}
