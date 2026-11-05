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

  drop(code = 1006) {
    this.readyState = 3
    this.onclose({ code })
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
    onEvent: vi.fn(),
    onSessionEnded: vi.fn(),
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
    expect(handlers.onRoomLeft).toHaveBeenCalledWith(7, 'left')
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
    // Repeated throttling must remain retryable within the total deadline.
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
    expect(handlers.onSendResult).toHaveBeenCalledWith(7, 'c1', { ok: true, seq: 9, created_at: undefined })
    socket.close()
  })

  it('keeps an unsent message offline for 30 seconds and sends its original ID on recovery', async () => {
    const { socket, handlers } = makeSocket()
    const ws = latest()
    socket.send(7, 'offline', 'waiting for recovery')
    await vi.advanceTimersByTimeAsync(30000)
    expect(ws.sent).toEqual([])
    expect(handlers.onSendResult).not.toHaveBeenCalled()

    ws.open()
    expect(ws.sent).toEqual([
      { type: 'send', room_id: 7, client_msg_id: 'offline', content: 'waiting for recovery' },
    ])
    ws.reply({ type: 'ack', client_msg_id: 'offline', seq: 8 })
    await vi.advanceTimersByTimeAsync(60000)
    expect(ws.sent).toHaveLength(1)
    expect(handlers.onSendResult).toHaveBeenCalledExactlyOnceWith(7, 'offline', { ok: true, seq: 8 })
    expect(vi.getTimerCount()).toBe(0)
    socket.close()
  })

  it('retries transient errors for more than 30 seconds before accepting the ACK', async () => {
    const { socket, handlers } = makeSocket()
    const ws = latest()
    ws.open()
    socket.send(7, 'recovering', 'same message')
    for (let round = 0; round < 7; round++) {
      ws.reply({ type: 'error', client_msg_id: 'recovering', error: { code: 'unavailable', retryable: true } })
      await vi.advanceTimersByTimeAsync(5000)
      expect(handlers.onSendResult).not.toHaveBeenCalled()
    }
    expect(ws.sent.length).toBeGreaterThan(4)
    expect(ws.sent.every(frame => frame.client_msg_id === 'recovering' && frame.content === 'same message')).toBe(true)
    ws.reply({ type: 'ack', client_msg_id: 'recovering', seq: 10 })
    ws.reply({ type: 'ack', client_msg_id: 'recovering', seq: 10 })
    const sent = ws.sent.length
    await vi.advanceTimersByTimeAsync(60000)
    expect(ws.sent).toHaveLength(sent)
    expect(handlers.onSendResult).toHaveBeenCalledExactlyOnceWith(7, 'recovering', { ok: true, seq: 10 })
    socket.close()
  })

  it('resends a lost ACK with the same payload beyond the old four-attempt limit', async () => {
    const { socket, handlers } = makeSocket()
    latest().open()
    socket.send(7, 'lost-ack', 'hello')
    await vi.advanceTimersByTimeAsync(30000)
    expect(latest().sent).toHaveLength(7)
    expect(new Set(latest().sent.map(frame => JSON.stringify(frame))).size).toBe(1)
    expect(handlers.onSendResult).not.toHaveBeenCalled()
    latest().reply({ type: 'ack', client_msg_id: 'lost-ack', seq: 11 })
    expect(vi.getTimerCount()).toBe(0)
    socket.close()
  })

  it.each([false, true])('expires at 60 seconds with the socket open=%s and never resends afterwards', async (open) => {
    const { socket, handlers } = makeSocket()
    if (open) latest().open()
    socket.send(7, 'expires', 'hello')
    await vi.advanceTimersByTimeAsync(59999)
    expect(handlers.onSendResult).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(handlers.onSendResult).toHaveBeenCalledExactlyOnceWith(7, 'expires', { ok: false, code: 'timeout' })
    const sent = latest().sent.length
    latest().open()
    latest().reply({ type: 'ack', client_msg_id: 'expires', seq: 12 })
    await vi.advanceTimersByTimeAsync(60000)
    expect(latest().sent).toHaveLength(sent)
    expect(handlers.onSendResult).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
    socket.close()
  })

  it('does not extend the deadline when reconnecting', async () => {
    const { socket, handlers } = makeSocket()
    socket.send(7, 'deadline', 'hello')
    await vi.advanceTimersByTimeAsync(30000)
    latest().drop()
    await vi.advanceTimersByTimeAsync(500)
    latest().open()
    expect(latest().sent[0].client_msg_id).toBe('deadline')
    await vi.advanceTimersByTimeAsync(29499)
    expect(handlers.onSendResult).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(handlers.onSendResult).toHaveBeenCalledExactlyOnceWith(7, 'deadline', { ok: false, code: 'timeout' })
    socket.close()
  })

  it('does not send on resume when background timers have not yet processed expiry', () => {
    const { socket, handlers } = makeSocket()
    socket.send(7, 'background', 'hello')
    vi.setSystemTime(Date.now() + 60001)
    latest().open()
    expect(latest().sent).toEqual([])
    expect(handlers.onSendResult).toHaveBeenCalledExactlyOnceWith(7, 'background', { ok: false, code: 'timeout' })
    expect(vi.getTimerCount()).toBe(0)
    socket.close()
  })

  it('keeps retry_after in force across reconnects', async () => {
    const { socket, handlers } = makeSocket()
    latest().open()
    socket.send(7, 'throttled', 'hello')
    latest().reply({ type: 'error', client_msg_id: 'throttled', error: { code: 'rate_limited', retryable: true, retry_after: 30 } })
    latest().drop()
    await vi.advanceTimersByTimeAsync(500)
    latest().open()
    await vi.advanceTimersByTimeAsync(29499)
    expect(latest().sent).toEqual([])
    await vi.advanceTimersByTimeAsync(1)
    expect(latest().sent.map(frame => frame.client_msg_id)).toEqual(['throttled'])
    expect(handlers.onSendResult).not.toHaveBeenCalled()
    latest().reply({ type: 'ack', client_msg_id: 'throttled', seq: 13 })
    expect(vi.getTimerCount()).toBe(0)
    socket.close()
  })

  it('bounds even a long server throttling delay by the original deadline', async () => {
    const { socket, handlers } = makeSocket()
    latest().open()
    socket.send(7, 'long-throttle', 'hello')
    latest().reply({ type: 'error', client_msg_id: 'long-throttle', error: { code: 'rate_limited', retryable: true, retry_after: 120 } })
    await vi.advanceTimersByTimeAsync(120000)
    expect(latest().sent).toHaveLength(1)
    expect(handlers.onSendResult).toHaveBeenCalledExactlyOnceWith(7, 'long-throttle', { ok: false, code: 'timeout' })
    expect(vi.getTimerCount()).toBe(0)
    socket.close()
  })

  it('settles a terminal rejection immediately and cancels later sends', async () => {
    const { socket, handlers } = makeSocket()
    latest().open()
    socket.send(7, 'rejected', 'hello')
    latest().reply({ type: 'error', client_msg_id: 'rejected', error: { code: 'forbidden', retryable: false } })
    await vi.advanceTimersByTimeAsync(60000)
    expect(latest().sent).toHaveLength(1)
    expect(handlers.onSendResult).toHaveBeenCalledExactlyOnceWith(7, 'rejected', { ok: false, code: 'forbidden' })
    expect(vi.getTimerCount()).toBe(0)
    socket.close()
  })

  it.each(['close', 'session-ended'])('cancels pending work on %s', async (reason) => {
    const { socket, handlers } = makeSocket()
    const first = latest()
    first.open()
    socket.send(7, 'stopped', 'hello')
    if (reason === 'close') {
      first.drop()
      socket.close()
    } else {
      first.drop(4401)
      expect(handlers.onSessionEnded).toHaveBeenCalledTimes(1)
    }
    expect(vi.getTimerCount()).toBe(0)
    first.open()
    await vi.advanceTimersByTimeAsync(120000)
    expect(first.sent).toHaveLength(1)
    expect(FakeWebSocket.all).toHaveLength(1)
    expect(handlers.onSendResult).not.toHaveBeenCalled()
    socket.close()
  })

  it('hands AI and room events to the view', () => {
    const { socket, handlers } = makeSocket()
    latest().open()
    const chunk = { type: 'ai_chunk', room_id: 7, generation_id: 'g', member_id: 3, text: 'hi', cumulative: true, snapshot: 'hi', attempt: 1, sequence: 1 }
    latest().reply(chunk)
    latest().reply({ type: 'role_changed', room_id: 7, member_id: 2, role: 'admin' })
    expect(handlers.onEvent.mock.calls.map(([frame]) => frame.type)).toEqual(['ai_chunk', 'role_changed'])
    expect(handlers.onEvent).toHaveBeenCalledWith(chunk)
    socket.close()
  })

  it('stops for good when the session has ended', async () => {
    const { socket, handlers } = makeSocket()
    latest().open()
    latest().drop(4401)
    expect(handlers.onSessionEnded).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(20000)
    expect(FakeWebSocket.all).toHaveLength(1)
    socket.close()
  })

  it('reconnects after an ordinary close', async () => {
    const { socket, handlers } = makeSocket()
    latest().open()
    latest().drop(1006)
    await vi.advanceTimersByTimeAsync(500)
    expect(FakeWebSocket.all).toHaveLength(2)
    expect(handlers.onSessionEnded).not.toHaveBeenCalled()
    socket.close()
  })

  it('stops reconnecting once closed', async () => {
    const { socket } = makeSocket()
    latest().open()
    socket.close()
    await vi.advanceTimersByTimeAsync(20000)
    expect(FakeWebSocket.all).toHaveLength(1)
  })

  it.each(['kicked', 'banned'])('passes the %s reason and removes the subscription', (reason) => {
    const { socket, handlers } = makeSocket()
    latest().open()
    socket.subscribe(7)
    latest().reply({ type: 'room_left', room_id: 7, reason })
    expect(handlers.onRoomLeft).toHaveBeenCalledWith(7, reason)
    expect(socket.rooms.has(7)).toBe(false)
    socket.close()
  })

it('forwards the acknowledgement creation time', () => {
  const { socket, handlers } = makeSocket()
  latest().open()
  socket.send(7, 'dated', 'hello')
  latest().reply({ type: 'ack', client_msg_id: 'dated', seq: 4, created_at: 1700000000000 })
  expect(handlers.onSendResult).toHaveBeenCalledWith(7, 'dated', { ok: true, seq: 4, created_at: 1700000000000 })
  socket.close()
})

})
