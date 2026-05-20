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

  // Chat
  chatSend: (agent_id: string, message: string) =>
    request<{ trigger_id: string; status: string }>('/chat/send', {
      method: 'POST',
      body: JSON.stringify({ agent_id, message }),
    }),
  chatPoll: (trigger_id: string) =>
    request<{ trigger_id: string; status: string; response?: string; error?: string }>(`/chat/${trigger_id}`),

  // HITL
  getCheckpoint: (trigger_id: string) => request<any>(`/triggers/${trigger_id}/checkpoint`),
  resumeTrigger: (trigger_id: string, action: 'approve' | 'reject', edit?: Record<string, any>) =>
    request<any>(`/triggers/${trigger_id}/resume`, {
      method: 'POST',
      body: JSON.stringify({ action, edit }),
    }),

  // Traces
  listTraces: (params: Record<string, string | number> = {}) => {
    const q = new URLSearchParams(params as any).toString()
    return request<any>(`/traces${q ? '?' + q : ''}`)
  },
  getTrace: (traceId: string) => request<any>(`/traces/${traceId}`),
  getTriggerTrace: (triggerId: string) => request<any>(`/triggers/${triggerId}/trace`),
  deleteTrace: (traceId: string) => request<void>(`/traces/${traceId}`, { method: 'DELETE' }),

  // Auth
  authConfig: () => request<any>('/auth/config'),
  authLogin: (email: string, password: string) =>
    request<any>('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }),
  authMe: () => request<any>('/auth/me'),

  // Compliance — remediation
  listRemediation: (params?: { tenant_id?: string; severity?: string; include_resolved?: boolean }) => {
    const q = new URLSearchParams(params as any).toString()
    return request<any>(`/compliance/remediation${q ? '?' + q : ''}`)
  },
  openRemediation: (body: any) =>
    request<any>('/compliance/remediation', { method: 'POST', body: JSON.stringify(body) }),
  updateRemediation: (id: number, body: any) =>
    request<any>(`/compliance/remediation/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),

  // Compliance — GDPR
  gdprExport: (identityId: string, tenantId = 'default') =>
    request<any>(`/compliance/gdpr/export/${encodeURIComponent(identityId)}?tenant_id=${encodeURIComponent(tenantId)}`),
  gdprErasure: (identityId: string, tenantId = 'default') =>
    request<any>(`/compliance/gdpr/${encodeURIComponent(identityId)}?tenant_id=${encodeURIComponent(tenantId)}`, { method: 'DELETE' }),

  // Compliance — file downloads (blob)
  downloadOecdReport: async (periodDays = 90): Promise<void> => {
    const token = localStorage.getItem('agentix_token')
    const res = await fetch(`${BASE}/compliance/oecd/export?period_days=${periodDays}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`)
    const blob = await res.blob()
    const cd = res.headers.get('Content-Disposition') ?? ''
    const name = cd.match(/filename=([^\s;]+)/)?.[1] ?? 'oecd-report.zip'
    _triggerDownload(blob, name)
  },

  downloadSoc2Report: async (): Promise<void> => {
    const token = localStorage.getItem('agentix_token')
    const res = await fetch(`${BASE}/compliance/soc2/export`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`)
    const blob = await res.blob()
    const cd = res.headers.get('Content-Disposition') ?? ''
    const name = cd.match(/filename=([^\s;]+)/)?.[1] ?? 'soc2-evidence.zip'
    _triggerDownload(blob, name)
  },
}

function _triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
