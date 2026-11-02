import { describe, expect, it, vi } from 'vitest'
import { ApiError } from './api.js'
import { leave, prepareLeave } from './leave.js'

const room = { id: 7, name: 'r' }
const human = { type: 'human' }
const ai = { type: 'ai' }

function fakeApi({ members = [], leave: leaveImpl = async () => ({}) } = {}) {
  return { members: vi.fn(async () => ({ members })), leave: vi.fn(leaveImpl) }
}

describe('prepareLeave', () => {
  it('gives the ordinary prompt when other humans remain', async () => {
    const prompt = await prepareLeave(fakeApi({ members: [human, human] }), room)
    expect(prompt).toEqual({ room, isLastHuman: false })
  })

  it('warns the last human, even with AI members left', async () => {
    const prompt = await prepareLeave(fakeApi({ members: [human, ai] }), room)
    expect(prompt).toEqual({ room, isLastHuman: true })
  })
})

describe('leave', () => {
  it('leaves without deleting after the ordinary prompt', async () => {
    const api = fakeApi()
    expect(await leave(api, { room, isLastHuman: false })).toBeNull()
    expect(api.leave).toHaveBeenCalledWith(7, false)
  })

  it('confirms deletion after the last-human prompt', async () => {
    const api = fakeApi()
    expect(await leave(api, { room, isLastHuman: true })).toBeNull()
    expect(api.leave).toHaveBeenCalledWith(7, true)
  })

  it('turns a stale ordinary prompt into the deletion warning', async () => {
    const api = fakeApi({
      leave: async () => {
        throw new ApiError('confirmation_required', 'x', 409)
      },
    })
    const prompt = await leave(api, { room, isLastHuman: false })
    expect(prompt).toEqual({ room, isLastHuman: true })
  })

  it('passes other failures on', async () => {
    const api = fakeApi({
      leave: async () => {
        throw new ApiError('unavailable', 'x', 503)
      },
    })
    await expect(leave(api, { room, isLastHuman: false })).rejects.toThrow()
  })
})
