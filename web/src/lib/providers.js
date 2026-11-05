import { requestErrorText } from './request-errors.js'

export const PROVIDERS = [
  { id: 'deepseek', name: 'DeepSeek' },
  { id: 'openai', name: 'OpenAI' },
  { id: 'anthropic', name: 'Anthropic' },
  { id: 'gemini', name: 'Gemini' },
]

export function providerName(id) {
  return PROVIDERS.find((p) => p.id === id)?.name ?? id
}

// What the settings page shows after a failed save.
export function keyErrorText(error) {
  if (error?.code === 'api_key_invalid') return '服务商拒绝了这个 key，请检查后重试'
  if (error?.code === 'invalid_argument') return 'key 格式不正确'
  return requestErrorText(error, '暂时无法验证，请稍后再试')
}

// Adds a request announced over the socket, unless it is already listed.
export function addRequest(requests, frame) {
  if (requests.some((r) => r.slug === frame.slug)) return requests
  return [...requests, { slug: frame.slug, requested_by: { id: frame.requested_by } }]
}
