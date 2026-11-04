import { describe, expect, it } from 'vitest'
import { AiDrafts } from './ai-drafts.js'

const chunk = (id, text, room = 1) => ({ room_id: room, generation_id: id, member_id: 9, text, snapshot: text, cumulative: true, attempt: 1, sequence: 1 })

describe('AiDrafts', () => {
  it('drops the draft when its message arrives', () => {
    const drafts = new AiDrafts()
    drafts.chunk(chunk('g1', '你'))
    drafts.close('g1')
    expect(drafts.forRoom(1)).toEqual([])
  })

  it('ignores a chunk arriving after its message', () => {
    const drafts = new AiDrafts()
    drafts.chunk(chunk('g1', '你'))
    drafts.close('g1')
    drafts.chunk(chunk('g1', '好'))
    expect(drafts.forRoom(1)).toEqual([])
  })

  it('removes a failed generation', () => {
    const drafts = new AiDrafts()
    drafts.chunk(chunk('g1', 'x'))
    drafts.close('g1')
    expect(drafts.forRoom(1)).toEqual([])
  })

  it('keeps rooms apart', () => {
    const drafts = new AiDrafts()
    drafts.chunk(chunk('g1', 'a', 1))
    drafts.chunk(chunk('g2', 'b', 2))
    expect(drafts.forRoom(2).map((d) => d.id)).toEqual(['g2'])
  })
})


describe('recovery snapshots', () => {
  it('replaces an old attempt and ignores late or duplicate snapshots', () => {
    const drafts = new AiDrafts()
    const frame = (attempt, sequence, text) => ({ ...chunk('g', 'delta'), snapshot: text, attempt, sequence, cumulative: true })
    drafts.chunk(frame(1, 1, 'old'))
    drafts.chunk(frame(2, 1, ''))
    drafts.chunk(frame(1, 9, 'late old'))
    drafts.chunk(frame(2, 3, 'new complete'))
    drafts.chunk(frame(2, 2, 'new'))
    drafts.chunk(frame(2, 3, 'duplicate'))
    expect(drafts.forRoom(1).map(d => d.text)).toEqual(['new complete'])
    drafts.close('g')
    drafts.chunk(frame(2, 4, 'late'))
    expect(drafts.forRoom(1)).toEqual([])
  })

  it('clears disconnected drafts and accepts a complete snapshot after reconnect', () => {
    const drafts = new AiDrafts()
    drafts.chunk(chunk('g', 'old partial'))
    drafts.clear()
    expect(drafts.forRoom(1)).toEqual([])
    drafts.chunk({ ...chunk('g', 'last delta'), snapshot: 'full current text', cumulative: true, attempt: 1, sequence: 5 })
    expect(drafts.forRoom(1)[0].text).toBe('full current text')
  })
})
