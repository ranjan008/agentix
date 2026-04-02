import { Routes, Route, NavLink } from 'react-router-dom'
import { LayoutDashboard, Bot, Zap, ShieldCheck, Users, BarChart2, Package } from 'lucide-react'
import clsx from 'clsx'

import Dashboard from './pages/Dashboard'
import Agents from './pages/Agents'
import Triggers from './pages/Triggers'
import Audit from './pages/Audit'
import Tenants from './pages/Tenants'
import Metrics from './pages/Metrics'
import Skills from './pages/Skills'

const NAV = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/agents', icon: Bot, label: 'Agents' },
  { to: '/triggers', icon: Zap, label: 'Triggers' },
  { to: '/skills', icon: Package, label: 'Skills' },
  { to: '/audit', icon: ShieldCheck, label: 'Audit' },
  { to: '/tenants', icon: Users, label: 'Tenants' },
  { to: '/metrics', icon: BarChart2, label: 'Metrics' },
]

export default function App() {
  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside className="w-56 bg-gray-900 text-white flex flex-col shrink-0">
        <div className="px-4 py-5 border-b border-gray-700">
          <span className="text-lg font-bold tracking-tight">⚡ Agentix</span>
          <p className="text-xs text-gray-400 mt-0.5">Admin Console</p>
        </div>
        <nav className="flex-1 overflow-y-auto py-4 space-y-1 px-2">
          {NAV.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                clsx(
                  'flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors',
                  isActive ? 'bg-indigo-600 text-white' : 'text-gray-300 hover:bg-gray-800 hover:text-white',
                )
              }
            >
              <Icon size={16} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="p-4 text-xs text-gray-500 border-t border-gray-700">v4.0.0</div>
      </aside>

      {/* Main */}
      <main className="flex-1 overflow-y-auto bg-gray-50">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/agents/*" element={<Agents />} />
          <Route path="/triggers" element={<Triggers />} />
          <Route path="/skills" element={<Skills />} />
          <Route path="/audit" element={<Audit />} />
          <Route path="/tenants" element={<Tenants />} />
          <Route path="/metrics" element={<Metrics />} />
        </Routes>
      </main>
    </div>
  )
}
