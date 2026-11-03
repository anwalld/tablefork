import { describe, expect, it } from 'vitest'
import { AiDrafts } from './ai-drafts.js'

const chunk = (id, text, room = 1) => ({ room_id: room, generation_id: id, member_id: 9, text })

describe('AiDrafts', () => {
  it('appends chunks of one generation', () => {
    const drafts = new AiDrafts()
    drafts.chunk(chunk('g1', '你'))
    drafts.chunk(chunk('g1', '好'))
    expect(drafts.forRoom(1)).toEqual([{ id: 'g1', roomId: 1, memberId: 9, text: '你好' }])
  })

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
