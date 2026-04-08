import { Routes, Route, NavLink, Navigate, useLocation } from 'react-router-dom'
import {
  LayoutDashboard, Bot, Zap, ShieldCheck, Users, BarChart2,
  Package, MessageSquare, LogOut, ChevronRight, Zap as ZapIcon,
} from 'lucide-react'
import clsx from 'clsx'

import { AuthProvider, useAuth } from './auth/AuthContext'
import Dashboard from './pages/Dashboard'
import Agents from './pages/Agents'
import Triggers from './pages/Triggers'
import Audit from './pages/Audit'
import Tenants from './pages/Tenants'
import Metrics from './pages/Metrics'
import Skills from './pages/Skills'
import Chat from './pages/Chat'
import Login from './pages/Login'

const NAV_GROUPS = [
  {
    label: '',
    items: [
      { to: '/chat',     icon: MessageSquare, label: 'Chat',      minLevel: 1 },
      { to: '/',         icon: LayoutDashboard, label: 'Dashboard', minLevel: 2 },
    ],
  },
  {
    label: 'Management',
    items: [
      { to: '/agents',   icon: Bot,         label: 'Agents',    minLevel: 2 },
      { to: '/triggers', icon: Zap,         label: 'Triggers',  minLevel: 2 },
      { to: '/skills',   icon: Package,     label: 'Skills',    minLevel: 3 },
    ],
  },
  {
    label: 'Enterprise',
    items: [
      { to: '/audit',    icon: ShieldCheck, label: 'Audit Log', minLevel: 4 },
      { to: '/tenants',  icon: Users,       label: 'Tenants',   minLevel: 4 },
      { to: '/metrics',  icon: BarChart2,   label: 'Metrics',   minLevel: 5 },
    ],
  },
]

const ROLE_LEVELS: Record<string, number> = {
  'end-user': 1, 'operator': 2, 'agent-author': 3, 'tenant-admin': 4, 'platform-admin': 5,
}

function userLevel(roles: string[]): number {
  return Math.max(0, ...roles.map(r => ROLE_LEVELS[r] ?? 0))
}

const ROLE_LABELS: Record<string, string> = {
  'end-user': 'End User',
  'operator': 'Operator',
  'agent-author': 'Agent Author',
  'tenant-admin': 'Tenant Admin',
  'platform-admin': 'Platform Admin',
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { token, loading } = useAuth()
  const location = useLocation()
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-900">
        <div className="flex flex-col items-center gap-4">
          <div className="w-10 h-10 rounded-xl bg-indigo-600 flex items-center justify-center animate-pulse">
            <ZapIcon size={20} className="text-white" />
          </div>
          <p className="text-slate-400 text-sm">Loading…</p>
        </div>
      </div>
    )
  }
  if (!token) return <Navigate to="/login" state={{ from: location }} replace />
  return <>{children}</>
}

function Sidebar() {
  const { identity, logout } = useAuth()
  const level = userLevel(identity?.roles ?? [])
  const primaryRole = identity?.roles?.[0] ?? 'end-user'
  const roleLabel = ROLE_LABELS[primaryRole] ?? primaryRole
  const initials = (identity?.name ?? identity?.email ?? 'U')
    .split(/[\s@]/).slice(0, 2).map((s: string) => s[0]?.toUpperCase()).join('')

  return (
    <aside className="w-60 flex flex-col shrink-0 bg-slate-900 border-r border-slate-800">
      {/* Logo */}
      <div className="px-5 py-5 border-b border-slate-800">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-indigo-600 flex items-center justify-center shadow-md shadow-indigo-900/50">
            <ZapIcon size={16} className="text-white" />
          </div>
          <div>
            <p className="text-white font-bold text-sm tracking-tight">Agentix</p>
            <p className="text-slate-500 text-[10px] tracking-wide uppercase">Admin Console</p>
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto py-4 px-3 space-y-6">
        {NAV_GROUPS.map(group => {
          const visible = group.items.filter(n => level >= n.minLevel)
          if (!visible.length) return null
          return (
            <div key={group.label}>
              {group.label && (
                <p className="px-3 mb-1.5 text-[10px] font-semibold text-slate-500 uppercase tracking-widest">
                  {group.label}
                </p>
              )}
              <div className="space-y-0.5">
                {visible.map(({ to, icon: Icon, label }) => (
                  <NavLink
                    key={to}
                    to={to}
                    end={to === '/'}
                    className={({ isActive }) =>
                      clsx(
                        'group flex items-center justify-between px-3 py-2 rounded-lg text-sm transition-all duration-150',
                        isActive
                          ? 'bg-indigo-600 text-white shadow-sm shadow-indigo-900/50'
                          : 'text-slate-400 hover:bg-slate-800 hover:text-slate-100',
                      )
                    }
                  >
                    {({ isActive }) => (
                      <>
                        <span className="flex items-center gap-2.5">
                          <Icon size={15} />
                          <span className="font-medium">{label}</span>
                        </span>
                        {!isActive && (
                          <ChevronRight size={12} className="opacity-0 group-hover:opacity-40 transition-opacity" />
                        )}
                      </>
                    )}
                  </NavLink>
                ))}
              </div>
            </div>
          )
        })}
      </nav>

      {/* User card */}
      <div className="p-3 border-t border-slate-800">
        <div className="flex items-center gap-3 px-2 py-2 rounded-lg hover:bg-slate-800 transition-colors group">
          {/* Avatar */}
          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center text-white text-xs font-bold flex-shrink-0 shadow">
            {initials}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-slate-200 text-xs font-medium truncate">
              {identity?.name ?? identity?.email ?? 'User'}
            </p>
            <p className="text-slate-500 text-[10px] truncate">{roleLabel}</p>
          </div>
          <button
            onClick={logout}
            title="Sign out"
            className="text-slate-500 hover:text-red-400 transition-colors flex-shrink-0"
          >
            <LogOut size={14} />
          </button>
        </div>
        <p className="mt-2 text-center text-[10px] text-slate-700">v1.0.0</p>
      </div>
    </aside>
  )
}

function Shell() {
  const { identity } = useAuth()
  const level = userLevel(identity?.roles ?? [])
  const defaultPath = level >= 2 ? '/' : '/chat'

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <main className="flex-1 overflow-y-auto bg-slate-50">
        <Routes>
          <Route path="/chat" element={<Chat />} />
          {level >= 2 && (
            <>
              <Route path="/" element={<Dashboard />} />
              <Route path="/agents/*" element={<Agents />} />
              <Route path="/triggers" element={<Triggers />} />
            </>
          )}
          {level >= 3 && <Route path="/skills" element={<Skills />} />}
          {level >= 4 && (
            <>
              <Route path="/audit" element={<Audit />} />
              <Route path="/tenants" element={<Tenants />} />
            </>
          )}
          {level >= 5 && <Route path="/metrics" element={<Metrics />} />}
          <Route path="*" element={<Navigate to={defaultPath} replace />} />
        </Routes>
      </main>
    </div>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/*" element={
          <RequireAuth>
            <Shell />
          </RequireAuth>
        } />
      </Routes>
    </AuthProvider>
  )
}
