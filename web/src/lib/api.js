export class ApiError extends Error {
  constructor(code, message, status, retryAfter = null) {
    super(message)
    this.code = code
    this.status = status
    // Seconds to wait before retrying, when the server says.
    this.retryAfter = retryAfter
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

  rooms: (limit, offset) => request('GET', `/api/rooms?limit=${limit}&offset=${offset}`),
  createRoom: (name) => request('POST', '/api/rooms', { name }),
  invite: (roomId, username) =>
    request('POST', `/api/rooms/${roomId}/invites`, { username }),
  join: (roomId) => request('POST', `/api/rooms/${roomId}/join`),
  leave: (roomId, confirmDelete) =>
    request('POST', `/api/rooms/${roomId}/leave`, { confirm_delete: confirmDelete }),
  members: (roomId) => request('GET', `/api/rooms/${roomId}/members`),
  history: (roomId, params = {}) => {
    const query = new URLSearchParams({ limit: 100, ...params })
    return request('GET', `/api/rooms/${roomId}/messages?${query}`)
  },
}
