import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RoomStream } from './room-stream.js'

const message = (seq) => ({ seq, content: `m${seq}` })
const seqs = (stream) => stream.messages.map((m) => m.seq)

function serverWith(...available) {
  return vi.fn(async (after) =>
    available.filter((seq) => seq > after).slice(0, 2).map(message),
  )
}

describe('RoomStream', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('shows out-of-order pushes in seq order without fetching', async () => {
    const fetchAfter = serverWith()
    const stream = new RoomStream({ fetchAfter })
    stream.addPage([message(9)])
    stream.receive(message(11))
    stream.receive(message(10))
    await vi.advanceTimersByTimeAsync(5000)
    expect(seqs(stream)).toEqual([9, 10, 11])
    expect(fetchAfter).not.toHaveBeenCalled()
  })

  it('drops duplicates', () => {
    const stream = new RoomStream({ fetchAfter: serverWith() })
    stream.receive(message(1))
    stream.receive({ ...message(1), content: 'again' })
    expect(stream.messages).toEqual([message(1)])
  })

  it('backfills a gap that does not close by itself', async () => {
    const fetchAfter = serverWith(10, 11)
    const stream = new RoomStream({ fetchAfter, gapTimeoutMs: 100 })
    stream.addPage([message(9)])
    stream.receive(message(11))
    await vi.advanceTimersByTimeAsync(50)
    expect(fetchAfter).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(100)
    expect(fetchAfter).toHaveBeenCalledWith(9)
    expect(seqs(stream)).toEqual([9, 10, 11])
  })

  it('catches up across several pages after reconnecting', async () => {
    const stream = new RoomStream({ fetchAfter: serverWith(4, 5, 6, 7, 8) })
    stream.addPage([message(2), message(3)])
    await stream.sync()
    expect(seqs(stream)).toEqual([2, 3, 4, 5, 6, 7, 8])
  })

  it('does not treat the first page as a gap', async () => {
    const fetchAfter = serverWith()
    const stream = new RoomStream({ fetchAfter, gapTimeoutMs: 100 })
    stream.addPage([message(50), message(51)])
    stream.receive(message(52))
    await vi.advanceTimersByTimeAsync(1000)
    expect(fetchAfter).not.toHaveBeenCalled()
  })

  it('keeps older pages loaded later', () => {
    const stream = new RoomStream({ fetchAfter: serverWith() })
    stream.addPage([message(50), message(51)])
    stream.addPage([message(48), message(49)])
    stream.receive(message(52))
    expect(seqs(stream)).toEqual([48, 49, 50, 51, 52])
    expect(stream.hasGap()).toBe(false)
  })

  it('shares one fetch between concurrent syncs', async () => {
    const fetchAfter = serverWith(1)
    const stream = new RoomStream({ fetchAfter })
    await Promise.all([stream.sync(), stream.sync()])
    expect(fetchAfter).toHaveBeenCalledTimes(2)
  })
})
