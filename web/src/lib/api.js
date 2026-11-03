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
  const response = await fetch(path, {
    method,
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new ApiError(
      data.error?.code ?? 'unavailable',
      data.error?.message ?? '服务暂时不可用',
      response.status,
      data.error?.retry_after ?? null,
      data.error?.provider ?? null,
    )
  }
  return data
}

export const api = {
  me: () => request('GET', '/api/auth/me'),
  register: (username, password) =>
    request('POST', '/api/auth/register', { username, password }),
  login: (username, password) =>
    request('POST', '/api/auth/login', { username, password }),
  logout: () => request('POST', '/api/auth/logout'),
  refresh: () => request('POST', '/api/auth/refresh'),

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

  aiAgents: () => request('GET', '/api/ai-agents'),
  requestAi: (roomId, slug) => request('POST', `/api/rooms/${roomId}/ai-members`, { slug }),
  removeAi: (roomId, slug) => request('DELETE', `/api/rooms/${roomId}/ai-members/${slug}`),
  aiRequests: (roomId) => request('GET', `/api/rooms/${roomId}/ai-requests`),
  approveAi: (roomId, slug) => request('POST', `/api/rooms/${roomId}/ai-requests/${slug}/approve`),
  rejectAi: (roomId, slug) => request('POST', `/api/rooms/${roomId}/ai-requests/${slug}/reject`),

  keys: () => request('GET', '/api/keys'),
  storeKey: (provider, key) => request('PUT', `/api/keys/${provider}`, { key }),
  deleteKey: (provider) => request('DELETE', `/api/keys/${provider}`),

  history: (roomId, params = {}) => {
    const query = new URLSearchParams({ limit: 100, ...params })
    return request('GET', `/api/rooms/${roomId}/messages?${query}`)
  },
}
