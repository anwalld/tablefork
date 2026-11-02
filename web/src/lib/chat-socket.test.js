import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatSocket } from './chat-socket.js'

class FakeWebSocket {
  static OPEN = 1
  static all = []

  constructor(url) {
    this.url = url
    this.readyState = 0
    this.sent = []
    FakeWebSocket.all.push(this)
  }

  send(data) {
    this.sent.push(JSON.parse(data))
  }

  close() {
    this.readyState = 3
    this.onclose?.()
  }

  open() {
    this.readyState = FakeWebSocket.OPEN
    this.onopen()
  }

  drop() {
    this.readyState = 3
    this.onclose()
  }

  reply(frame) {
    this.onmessage({ data: JSON.stringify(frame) })
  }
}

const latest = () => FakeWebSocket.all.at(-1)

function makeSocket() {
  const handlers = {
    onMessage: vi.fn(),
    onSubscribed: vi.fn(),
    onRoomLeft: vi.fn(),
    onSendResult: vi.fn(),
    onThrottled: vi.fn(),
    onStatus: vi.fn(),
  }
  return { socket: new ChatSocket(handlers), handlers }
}

describe('ChatSocket', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    FakeWebSocket.all = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.stubGlobal('location', { protocol: 'http:', host: 'example.test' })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('reconnects, resubscribes and resends with the same client_msg_id', async () => {
    const { socket, handlers } = makeSocket()
    latest().open()
    socket.subscribe(7)
    socket.send(7, 'c1', 'hello')
    const first = latest()
    expect(first.sent.map((f) => f.type)).toEqual(['subscribe', 'send'])

    first.drop()
    expect(handlers.onStatus).toHaveBeenLastCalledWith(false)
    await vi.advanceTimersByTimeAsync(500)
    const second = latest()
    expect(second).not.toBe(first)
    second.open()

    expect(handlers.onStatus).toHaveBeenLastCalledWith(true)
    expect(second.sent).toEqual([
      { type: 'subscribe', room_id: 7 },
      { type: 'send', room_id: 7, client_msg_id: 'c1', content: 'hello' },
    ])
    second.reply({ type: 'ack', client_msg_id: 'c1', seq: 3 })
    expect(handlers.onSendResult).toHaveBeenCalledWith(7, 'c1', { ok: true, seq: 3 })
    socket.close()
  })

  it('backs off between failed reconnects', async () => {
    const { socket } = makeSocket()
    latest().drop()
    await vi.advanceTimersByTimeAsync(500)
    expect(FakeWebSocket.all).toHaveLength(2)
    latest().drop()
    await vi.advanceTimersByTimeAsync(500)
    expect(FakeWebSocket.all).toHaveLength(2)
    await vi.advanceTimersByTimeAsync(500)
    expect(FakeWebSocket.all).toHaveLength(3)
    socket.close()
  })

  it('does not resubscribe a room it was removed from', async () => {
    const { socket, handlers } = makeSocket()
    latest().open()
    socket.subscribe(7)
    latest().reply({ type: 'room_left', room_id: 7 })
    expect(handlers.onRoomLeft).toHaveBeenCalledWith(7)
    latest().drop()
    await vi.advanceTimersByTimeAsync(500)
    latest().open()
    expect(latest().sent).toEqual([])
    socket.close()
  })

  it('waits out rate_limited and resends without using up attempts', async () => {
    const { socket, handlers } = makeSocket()
    latest().open()
    socket.send(7, 'c1', 'hello')
    const ws = latest()
    const limited = {
      type: 'error',
      client_msg_id: 'c1',
      error: { code: 'rate_limited', retryable: true, retry_after: 3 },
    }
    // More refusals than MAX_ATTEMPTS: none of them may fail the message.
    for (let round = 0; round < 6; round++) {
      ws.reply(limited)
      expect(handlers.onThrottled).toHaveBeenLastCalledWith(7, 'c1', 3)
      const sent = ws.sent.length
      await vi.advanceTimersByTimeAsync(2999)
      expect(ws.sent).toHaveLength(sent)
      await vi.advanceTimersByTimeAsync(1)
      expect(ws.sent).toHaveLength(sent + 1)
      expect(ws.sent.at(-1).client_msg_id).toBe('c1')
    }
    expect(handlers.onSendResult).not.toHaveBeenCalled()
    ws.reply({ type: 'ack', client_msg_id: 'c1', seq: 9 })
    expect(handlers.onSendResult).toHaveBeenCalledWith(7, 'c1', { ok: true, seq: 9 })
    socket.close()
  })

  it('stops reconnecting once closed', async () => {
    const { socket } = makeSocket()
    latest().open()
    socket.close()
    await vi.advanceTimersByTimeAsync(20000)
    expect(FakeWebSocket.all).toHaveLength(1)
  })
})
