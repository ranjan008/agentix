/**
 * Login page — full dark split-panel design.
 * Left: Agentix feature marketing panel.
 * Right: Auth0 SSO button + email/password form.
 */
import { useState, useEffect, FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import {
  Bot, Zap, ShieldCheck, BarChart2, Globe2, Layers,
  ArrowRight, CheckCircle2,
} from 'lucide-react'

interface Auth0Config {
  domain: string
  client_id: string
  audience?: string
}

const FEATURES = [
  {
    icon: Bot,
    title: 'Multi-Agent Orchestration',
    desc: 'Deploy and manage fleets of AI agents across every channel — Telegram, webhook, cron, and more.',
  },
  {
    icon: ShieldCheck,
    title: 'Enterprise RBAC',
    desc: 'Five-tier role hierarchy: end-user → operator → author → tenant-admin → platform-admin.',
  },
  {
    icon: Zap,
    title: 'Real-time Triggers',
    desc: 'Event-driven architecture with full trigger history, replay, and live status polling.',
  },
  {
    icon: BarChart2,
    title: 'Metrics & Cost Ledger',
    desc: 'Track LLM token usage, cost per agent, and execution success rates in real time.',
  },
  {
    icon: Layers,
    title: 'Pluggable Skill Hub',
    desc: 'Extend agents with web search, Telegram, custom tools, and marketplace skills.',
  },
  {
    icon: Globe2,
    title: 'Immutable Audit Log',
    desc: 'HMAC-chained audit trail for every action — SOC 2 ready, tamper-evident.',
  },
]

export default function Login() {
  const { login, token } = useAuth()
  const navigate = useNavigate()

  const [auth0Cfg, setAuth0Cfg] = useState<Auth0Config | null>(null)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (token) navigate('/', { replace: true })
  }, [token, navigate])

  useEffect(() => {
    fetch('/api/v1/auth/config')
      .then(r => r.ok ? r.json() : null)
      .then(data => data?.auth0_domain ? setAuth0Cfg({
        domain: data.auth0_domain,
        client_id: data.auth0_client_id,
        audience: data.auth0_audience,
      }) : null)
      .catch(() => null)
  }, [])

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(email, password)
      navigate('/', { replace: true })
    } catch (err: any) {
      setError(err.message ?? 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  const handleAuth0 = () => {
    if (!auth0Cfg) return
    const params = new URLSearchParams({
      response_type: 'code',
      client_id: auth0Cfg.client_id,
      redirect_uri: `${window.location.origin}/ui/auth/callback`,
      scope: 'openid profile email',
      ...(auth0Cfg.audience ? { audience: auth0Cfg.audience } : {}),
    })
    window.location.href = `https://${auth0Cfg.domain}/authorize?${params}`
  }

  return (
    <div className="min-h-screen flex bg-[#050c18]">
      {/* ── Left panel: feature marketing ── */}
      <div className="hidden lg:flex lg:w-[58%] relative flex-col justify-between p-12 overflow-hidden"
           style={{ background: 'linear-gradient(135deg, #050c18 0%, #0a1628 40%, #0e1e38 100%)' }}>

        {/* Grid pattern overlay */}
        <div className="absolute inset-0 opacity-[0.04]"
             style={{ backgroundImage: 'linear-gradient(#fff 1px, transparent 1px), linear-gradient(90deg, #fff 1px, transparent 1px)', backgroundSize: '40px 40px' }} />

        {/* Glow blobs */}
        <div className="absolute top-1/4 -left-20 w-80 h-80 bg-cyan-600 rounded-full opacity-10 blur-3xl pointer-events-none" />
        <div className="absolute bottom-1/4 right-0 w-64 h-64 bg-indigo-600 rounded-full opacity-15 blur-3xl pointer-events-none" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-96 h-96 bg-violet-700 rounded-full opacity-5 blur-3xl pointer-events-none" />

        {/* Logo */}
        <div className="relative">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-cyan-500 to-indigo-600 flex items-center justify-center shadow-lg shadow-cyan-500/20">
              <Zap size={20} className="text-white" />
            </div>
            <div>
              <span className="text-white text-xl font-bold tracking-tight">Agentix</span>
              <span className="ml-2 text-xs px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-400 font-medium border border-cyan-500/20">v1.0</span>
            </div>
          </div>
        </div>

        {/* Hero text */}
        <div className="relative space-y-6">
          <div>
            <h1 className="text-4xl xl:text-5xl font-bold text-white leading-tight">
              Enterprise-grade<br />
              <span className="bg-gradient-to-r from-cyan-400 to-indigo-400 bg-clip-text text-transparent">
                Agentic Platform
              </span>
            </h1>
            <p className="mt-4 text-lg text-slate-400 leading-relaxed max-w-md">
              Build, deploy, and monitor AI agents at scale — with full RBAC, audit trails, and multi-channel routing built in.
            </p>
          </div>

          {/* Feature grid */}
          <div className="grid grid-cols-2 gap-4">
            {FEATURES.map(({ icon: Icon, title, desc }) => (
              <div key={title}
                   className="group p-4 rounded-xl border border-white/[0.06] bg-white/[0.03] hover:bg-white/[0.06] hover:border-cyan-500/30 transition-all duration-200 cursor-default">
                <div className="flex items-center gap-2.5 mb-2">
                  <div className="w-7 h-7 rounded-lg bg-cyan-500/10 flex items-center justify-center flex-shrink-0 border border-cyan-500/20">
                    <Icon size={14} className="text-cyan-400" />
                  </div>
                  <span className="text-sm font-semibold text-slate-200">{title}</span>
                </div>
                <p className="text-xs text-slate-500 leading-relaxed">{desc}</p>
              </div>
            ))}
          </div>

          {/* Trust badges */}
          <div className="flex flex-wrap items-center gap-3 pt-2">
            {['SQLite · Zero deps', 'Anthropic Claude', 'Open Source · MIT'].map(b => (
              <div key={b} className="flex items-center gap-1.5 text-xs text-slate-500">
                <CheckCircle2 size={13} className="text-emerald-400 flex-shrink-0" />
                {b}
              </div>
            ))}
          </div>
        </div>

        {/* Footer */}
        <div className="relative text-xs text-slate-700">
          © 2026 Agentix · MIT License
        </div>
      </div>

      {/* ── Right panel: login form ── */}
      <div className="flex-1 flex flex-col items-center justify-center px-6 py-12 bg-[#070e1c] border-l border-white/[0.04]">
        {/* Mobile logo */}
        <div className="lg:hidden mb-8 flex items-center gap-2">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-cyan-500 to-indigo-600 flex items-center justify-center shadow-md shadow-cyan-500/20">
            <Zap size={18} className="text-white" />
          </div>
          <span className="text-xl font-bold text-slate-100">Agentix</span>
        </div>

        <div className="w-full max-w-sm">
          <div className="mb-8">
            <h2 className="text-2xl font-bold text-slate-100">Welcome back</h2>
            <p className="mt-1.5 text-sm text-slate-500">Sign in to your Agentix console</p>
          </div>

          {/* Auth0 SSO */}
          {auth0Cfg && (
            <>
              <button onClick={handleAuth0}
                className="w-full flex items-center justify-between gap-3 border border-white/[0.08] hover:border-cyan-500/40 hover:bg-cyan-500/[0.04] rounded-xl px-4 py-3 text-sm font-medium text-slate-300 transition-all duration-150 mb-5 group">
                <div className="flex items-center gap-3">
                  <svg className="w-5 h-5 flex-shrink-0" viewBox="0 0 24 24" fill="#EB5424">
                    <path d="M21.98 7.448 19.62 0H4.347L2.02 7.448c-1.352 4.312.03 9.206 3.815 12.015L12.007 24l6.157-4.552c3.755-2.81 5.182-7.688 3.815-12.015zM12 19.056l-4.868-3.538 1.775-5.58H15.1l1.775 5.58L12 19.056z"/>
                  </svg>
                  Continue with Auth0 SSO
                </div>
                <ArrowRight size={15} className="text-slate-600 group-hover:text-cyan-400 transition-colors" />
              </button>

              <div className="relative mb-5">
                <div className="absolute inset-0 flex items-center">
                  <div className="w-full border-t border-white/[0.06]" />
                </div>
                <div className="relative flex justify-center">
                  <span className="px-3 bg-[#070e1c] text-xs text-slate-600">or continue with email</span>
                </div>
              </div>
            </>
          )}

          {/* Email/password form */}
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-slate-400">Email address</label>
              <input
                type="email" required autoComplete="email"
                value={email} onChange={e => setEmail(e.target.value)}
                className="input"
                placeholder="admin@agentix.local"
              />
            </div>
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-slate-400">Password</label>
              <input
                type="password" required autoComplete="current-password"
                value={password} onChange={e => setPassword(e.target.value)}
                className="input"
                placeholder="••••••••"
              />
            </div>

            {error && (
              <div className="flex items-start gap-2.5 bg-red-500/10 border border-red-500/20 text-red-400 rounded-xl px-4 py-3 text-sm">
                <span className="mt-0.5">⚠</span>
                {error}
              </div>
            )}

            <button type="submit" disabled={loading} className="btn-primary w-full py-3">
              {loading
                ? <><span className="w-4 h-4 border-2 border-slate-700 border-t-slate-950 rounded-full animate-spin" />Signing in…</>
                : <>Sign in <ArrowRight size={15} /></>
              }
            </button>
          </form>

          {/* Role hint */}
          <p className="mt-6 text-center text-xs text-slate-700">
            Role-based access · 5-tier RBAC · JWT secured
          </p>
        </div>
      </div>
    </div>
  )
}
