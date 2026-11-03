import { describe, expect, it } from 'vitest'
import { ApiError } from './api.js'
import { addRequest, keyErrorText, providerName } from './providers.js'

describe('providers', () => {
  it('names providers', () => {
    expect(providerName('anthropic')).toBe('Anthropic')
    expect(providerName('other')).toBe('other')
  })

  it('explains failed saves', () => {
    expect(keyErrorText(new ApiError('api_key_invalid', 'x', 400))).toContain('拒绝')
    expect(keyErrorText(new ApiError('unavailable', 'x', 503))).toContain('稍后')
  })

  it('counts each pending AI once', () => {
    let requests = []
    requests = addRequest(requests, { slug: 'gpt', requested_by: 5 })
    requests = addRequest(requests, { slug: 'gpt', requested_by: 6 })
    requests = addRequest(requests, { slug: 'claude', requested_by: 6 })
    expect(requests.map((r) => r.slug)).toEqual(['gpt', 'claude'])
  })
})
