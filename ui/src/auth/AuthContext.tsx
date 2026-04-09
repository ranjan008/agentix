/**
 * Auth context — wraps local JWT auth + optional Auth0 OIDC flow.
 * Token stored in localStorage under "agentix_token".
 * Identity (roles, email) stored in context for role-based rendering.
 */
import { createContext, useContext, useEffect, useState, ReactNode } from 'react'

export interface Identity {
  identity_id: string
  email: string
  name?: string
  roles: string[]
  tenant_id: string
}

interface AuthCtx {
  identity: Identity | null
  token: string | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  loginWithAuth0Token: (accessToken: string) => Promise<void>
  logout: () => void
  hasRole: (role: string) => boolean
  /** true if user has at least operator-level access */
  canOperate: boolean
  /** true if user can register agents / install skills */
  canAuthor: boolean
  /** true if tenant-admin or platform-admin */
  isAdmin: boolean
  /** true if platform-admin only */
  isPlatformAdmin: boolean
}

const AuthContext = createContext<AuthCtx | null>(null)

const ROLE_LEVELS: Record<string, number> = {
  'end-user': 1,
  'operator': 2,
  'agent-author': 3,
  'tenant-admin': 4,
  'platform-admin': 5,
}

function maxLevel(roles: string[]): number {
  return Math.max(0, ...roles.map(r => ROLE_LEVELS[r] ?? 0))
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('agentix_token'))
  const [identity, setIdentity] = useState<Identity | null>(null)
  const [loading, setLoading] = useState(true)

  // On mount: validate stored token and fetch /auth/me
  useEffect(() => {
    if (!token) { setLoading(false); return }
    fetch('/api/v1/auth/me', { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(data => setIdentity(data))
      .catch(() => { localStorage.removeItem('agentix_token'); setToken(null) })
      .finally(() => setLoading(false))
  }, [token])

  const _storeToken = (t: string, id: Identity) => {
    localStorage.setItem('agentix_token', t)
    setToken(t)
    setIdentity(id)
  }

  const login = async (email: string, password: string) => {
    const res = await fetch('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail ?? 'Login failed')
    }
    const data = await res.json()
    _storeToken(data.token, { identity_id: data.identity_id, email: data.email, roles: data.roles, tenant_id: data.tenant_id })
  }

  const loginWithAuth0Token = async (accessToken: string) => {
    const res = await fetch('/api/v1/auth/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ access_token: accessToken }),
    })
    if (!res.ok) throw new Error('Auth0 token exchange failed')
    const data = await res.json()
    _storeToken(data.token, { identity_id: data.identity_id, email: data.email, roles: data.roles, tenant_id: data.tenant_id })
  }

  const logout = () => {
    localStorage.removeItem('agentix_token')
    setToken(null)
    setIdentity(null)
  }

  const hasRole = (role: string) => identity?.roles.includes(role) ?? false
  const level = maxLevel(identity?.roles ?? [])

  return (
    <AuthContext.Provider value={{
      identity, token, loading,
      login, loginWithAuth0Token, logout, hasRole,
      canOperate: level >= 2,
      canAuthor: level >= 3,
      isAdmin: level >= 4,
      isPlatformAdmin: level >= 5,
    }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthCtx {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
