import { describe, expect, it, vi } from 'vitest'
import { fetchAllMessages, formatTranscript } from './transcript.js'

describe('fetchAllMessages', () => {
  it('walks every page', async () => {
    const server = Array.from({ length: 5 }, (_, i) => ({ seq: i + 1 }))
    const history = vi.fn(async (_room, { after }) => ({
      messages: server.filter((m) => m.seq > after).slice(0, 2),
    }))
    const messages = await fetchAllMessages(1, history)
    expect(messages.map((m) => m.seq)).toEqual([1, 2, 3, 4, 5])
  })
})

describe('formatTranscript', () => {
  it('writes one line per message', () => {
    const text = formatTranscript('房间', [
      { created_at: 0, member: { name: '小明' }, content: '你好' },
    ])
    expect(text).toBe('# 房间\n[1970-01-01T00:00:00.000Z] 小明: 你好\n')
  })
})
