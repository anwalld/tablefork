import { queueOwner, randomId, waitForTask, rotateQueueOwner } from './queued-task.js'
export class ApiError extends Error {
  constructor(code, message, status, retryAfter = null, provider = null) {
    super(message)
    this.code = code
    this.status = status
    // Seconds to wait before retrying, when the server says.
    this.retryAfter = retryAfter
    // The model provider an error is about, such as a missing API key.
    this.provider = provider
  }
}

async function request(method, path, body) {
  const capability = queueOwner()
  const requestId = randomId()
  const response = await fetch(path, {
    method,
    headers: { 'X-Queue-Owner': capability, 'X-Request-Id': requestId, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  let data = await response.json().catch(() => ({}))
  let status = response.status ?? (response.ok ? 200 : 503)
  if (status === 202 && data.task_id) {
    const result = await waitForTask(data.task_id, data.claim_token ?? capability)
    data = result.body
    status = result.status
  }
  if (status >= 400) {
    throw new ApiError(
      data.error?.code ?? 'unavailable',
      data.error?.message ?? '服务暂时不可用',
      status,
      data.error?.retry_after ?? null,
      data.error?.provider ?? null,
    )
  }
  if (method === 'POST' && ['/api/auth/login', '/api/auth/logout'].includes(path)) rotateQueueOwner()
  return data
}

export const api = {
  me: () => request('GET', '/api/auth/me'),
  register: (username, password, extra = {}) =>
    request('POST', '/api/auth/register', { username, password, ...extra }),
  login: (username, password) =>
    request('POST', '/api/auth/login', { username, password }),
  logout: () => request('POST', '/api/auth/logout'),
  refresh: () => request('POST', '/api/auth/refresh'),
  passwordRecoveryQuestion: (username) =>
    request('POST', '/api/auth/password-recovery/question', { username }),
  passwordRecoveryReset: (username, answer, password, passwordConfirm) =>
    request('POST', '/api/auth/password-recovery/reset', {
      username,
      answer,
      password,
      password_confirm: passwordConfirm,
    }),

  rooms: (limit, offset) => request('GET', `/api/rooms?limit=${limit}&offset=${offset}`),
  createRoom: (name) => request('POST', '/api/rooms', { name }),
  invite: (roomId, username) =>
    request('POST', `/api/rooms/${roomId}/invites`, { username }),
  join: (roomId) => request('POST', `/api/rooms/${roomId}/join`),
  leave: (roomId, confirmDelete) =>
    request('POST', `/api/rooms/${roomId}/leave`, { confirm_delete: confirmDelete }),
  members: (roomId) => request('GET', `/api/rooms/${roomId}/members`),
  transferOwner: (roomId, username) =>
    request('POST', `/api/rooms/${roomId}/owner`, { username }),
  appointAdmin: (roomId, username) =>
    request('PUT', `/api/rooms/${roomId}/admins/${encodeURIComponent(username)}`),
  dismissAdmin: (roomId, username) =>
    request('DELETE', `/api/rooms/${roomId}/admins/${encodeURIComponent(username)}`),

  kickMember: (roomId, username) =>
    request('DELETE', `/api/rooms/${roomId}/members/${encodeURIComponent(username)}`),
  banUser: (roomId, username) =>
    request('PUT', `/api/rooms/${roomId}/bans/${encodeURIComponent(username)}`),
  unbanUser: (roomId, username) =>
    request('DELETE', `/api/rooms/${roomId}/bans/${encodeURIComponent(username)}`),
  listBans: (roomId) => request('GET', `/api/rooms/${roomId}/bans`),

  aiAgents: () => request('GET', '/api/ai/roles'),
  aiModels: () => request('GET', '/api/ai/models'),
  createAiRole: (model_slug, name, persona = '') => request('POST', '/api/ai/roles', { model_slug, name, persona }),
  editRolePersona: (memberId, persona) => request('PUT', `/api/ai/roles/${memberId}/persona`, { persona }),
  editAiPersona: (roomId, memberId, persona, persona_version) => request('PUT', `/api/rooms/${roomId}/ais/${memberId}/persona`, { persona, persona_version }),
  requestAi: (roomId, member, persona = '') => request('POST', `/api/rooms/${roomId}/ai-members`, typeof member === 'number' ? { member_id: member, persona } : { slug: member, persona }),
  removeAi: (roomId, slug) => request('DELETE', `/api/rooms/${roomId}/ai-members/${slug}`),
  aiRequests: (roomId) => request('GET', `/api/rooms/${roomId}/ai-requests`),
  approveAi: (roomId, slug) => request('POST', `/api/rooms/${roomId}/ai-requests/${slug}/approve`),
  rejectAi: (roomId, slug) => request('POST', `/api/rooms/${roomId}/ai-requests/${slug}/reject`),

  keys: () => request('GET', '/api/keys'),
  storeKey: (provider, key) => request('PUT', `/api/keys/${provider}`, { key }),
  deleteKey: (provider) => request('DELETE', `/api/keys/${provider}`),

  aiState: (roomId) => request('GET', `/api/rooms/${roomId}/ai-state`),
  pauseAi: (roomId, paused) => request('PUT', `/api/rooms/${roomId}/ai-state`, { paused }),
  stopAi: (roomId, id) => request('POST', `/api/rooms/${roomId}/ai-generations/${encodeURIComponent(id)}/stop`),
  markRead: (roomId, seq) => request('PUT', `/api/rooms/${roomId}/read`, { seq }),
  search: (roomId, params = {}) => request('GET', `/api/rooms/${roomId}/search?${new URLSearchParams({ limit: 50, ...params })}`),

  history: (roomId, params = {}) => {
    const query = new URLSearchParams({ limit: 100, ...params })
    return request('GET', `/api/rooms/${roomId}/messages?${query}`)
  },
}
