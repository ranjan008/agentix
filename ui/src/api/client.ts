/**
 * Agentix API client — thin wrapper over fetch.
 * Auth token stored in localStorage under "agentix_token".
 */

const BASE = '/api/v1'

function getToken(): string | null {
  return localStorage.getItem('agentix_token')
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> ?? {}),
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const res = await fetch(`${BASE}${path}`, { ...options, headers })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`API ${res.status}: ${body}`)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

export const api = {
  // Agents
  listAgents: () => request<any[]>('/agents'),
  getAgent: (id: string) => request<any>(`/agents/${id}`),
  createAgent: (body: any) => request<any>('/agents', { method: 'POST', body: JSON.stringify(body) }),
  updateAgent: (id: string, body: any) => request<any>(`/agents/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteAgent: (id: string) => request<void>(`/agents/${id}`, { method: 'DELETE' }),

  // Triggers
  listTriggers: (params?: { agent_id?: string; status?: string; limit?: number }) => {
    const q = new URLSearchParams(params as any).toString()
    return request<any>(`/triggers${q ? '?' + q : ''}`)
  },
  getTrigger: (id: string) => request<any>(`/triggers/${id}`),
  replayTrigger: (id: string) => request<any>(`/triggers/${id}/replay`, { method: 'POST' }),

  // Skills
  listSkills: () => request<any[]>('/skills'),
  searchMarketplace: (q: string) => request<any[]>(`/skills/marketplace?q=${encodeURIComponent(q)}`),
  installSkill: (name: string, body: any) => request<any>(`/skills/${name}/install`, { method: 'POST', body: JSON.stringify(body) }),

  // Audit
  listAudit: (params?: { tenant_id?: string; action?: string; limit?: number }) => {
    const q = new URLSearchParams(params as any).toString()
    return request<any>(`/audit${q ? '?' + q : ''}`)
  },
  verifyAuditChain: () => request<any>('/audit/verify'),

  // Tenants
  listTenants: () => request<any[]>('/tenants'),
  createTenant: (body: any) => request<any>('/tenants', { method: 'POST', body: JSON.stringify(body) }),
  deleteTenant: (id: string) => request<void>(`/tenants/${id}`, { method: 'DELETE' }),
  createServiceAccount: (tenantId: string, body: any) =>
    request<any>(`/tenants/${tenantId}/service-accounts`, { method: 'POST', body: JSON.stringify(body) }),

  // Metrics
  costSummary: (params?: { tenant_id?: string; agent_id?: string }) => {
    const q = new URLSearchParams(params as any).toString()
    return request<any>(`/metrics/cost${q ? '?' + q : ''}`)
  },
  triggerStats: (hours = 24) => request<any>(`/metrics/triggers?hours=${hours}`),
  agentStats: () => request<any[]>('/metrics/agents'),
}
