const ACK_TIMEOUT_MS = 5000
const MAX_ATTEMPTS = 4
const MAX_BACKOFF_MS = 10000
// The server closes with this code when the session has ended (logged out or
// expired). Reconnecting would only be refused, so the client stops.
export const SESSION_ENDED = 4401

// The WebSocket connection: reconnects, resubscribes, and resends unacknowledged
// messages with their original client_msg_id so that the server can deduplicate.
export class ChatSocket {
  constructor({ onMessage, onSubscribed, onRoomLeft, onSendResult, onThrottled, onStatus, onEvent, onSessionEnded }) {
    this.handlers = { onMessage, onSubscribed, onRoomLeft, onSendResult, onThrottled, onStatus, onEvent, onSessionEnded }
    this.rooms = new Set()
    this.pending = new Map()
    this.backoff = 500
    this.closed = false
    this.connect()
  }

  connect() {
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
    this.ws = new WebSocket(`${scheme}://${location.host}/ws`)
    this.ws.onopen = () => {
      this.backoff = 500
      this.handlers.onStatus(true)
      for (const roomId of this.rooms) this.write({ type: 'subscribe', room_id: roomId })
      for (const entry of this.pending.values()) this.transmit(entry)
    }
    this.ws.onmessage = (event) => this.dispatch(JSON.parse(event.data))
    this.ws.onclose = (event) => {
      this.handlers.onStatus(false)
      if (this.closed) return
      if (event?.code === SESSION_ENDED) {
        this.closed = true
        this.handlers.onSessionEnded?.()
        return
      }
      setTimeout(() => this.connect(), this.backoff)
      this.backoff = Math.min(this.backoff * 2, MAX_BACKOFF_MS)
    }
  }

  dispatch(frame) {
    if (frame.type === 'message') {
      this.handlers.onMessage(frame)
    } else if (frame.type === 'subscribed') {
      this.handlers.onSubscribed(frame.room_id)
    } else if (frame.type === 'room_left') {
      this.rooms.delete(frame.room_id)
      this.handlers.onRoomLeft(frame.room_id, frame.reason ?? 'left')
    } else if (frame.type === 'ack') {
      this.settle(frame.client_msg_id, { ok: true, seq: frame.seq, created_at: frame.created_at })
    } else if (frame.type === 'error') {
      this.handleError(frame)
    } else {
      // AI output, role changes and the like: the view decides.
      this.handlers.onEvent?.(frame)
    }
  }

  handleError(frame) {
    const entry = this.pending.get(frame.client_msg_id)
    if (!entry) return
    if (frame.error.code === 'rate_limited' && frame.error.retry_after) {
      // Sending too fast: wait as told and resend. Waiting is not a failed
      // attempt, so it does not count towards MAX_ATTEMPTS.
      clearTimeout(entry.timer)
      entry.attempts -= 1
      this.handlers.onThrottled?.(entry.roomId, entry.clientMsgId, frame.error.retry_after)
      entry.timer = setTimeout(() => this.transmit(entry), frame.error.retry_after * 1000)
    } else if (frame.error.retryable && entry.attempts < MAX_ATTEMPTS) {
      clearTimeout(entry.timer)
      entry.timer = setTimeout(() => this.transmit(entry), 1000 * entry.attempts)
    } else {
      this.settle(frame.client_msg_id, { ok: false, code: frame.error.code })
    }
  }

  settle(clientMsgId, result) {
    const entry = this.pending.get(clientMsgId)
    if (!entry) return
    clearTimeout(entry.timer)
    this.pending.delete(clientMsgId)
    this.handlers.onSendResult(entry.roomId, clientMsgId, result)
  }

  write(frame) {
    if (this.ws.readyState !== WebSocket.OPEN) return false
    this.ws.send(JSON.stringify(frame))
    return true
  }

  subscribe(roomId) {
    this.rooms.add(roomId)
    this.write({ type: 'subscribe', room_id: roomId })
  }

  unsubscribe(roomId) {
    this.rooms.delete(roomId)
    this.write({ type: 'unsubscribe', room_id: roomId })
  }

  send(roomId, clientMsgId, content) {
    const entry = { roomId, clientMsgId, content, attempts: 0, timer: null }
    this.pending.set(clientMsgId, entry)
    this.transmit(entry)
  }

  transmit(entry) {
    clearTimeout(entry.timer)
    if (entry.attempts >= MAX_ATTEMPTS) {
      this.settle(entry.clientMsgId, { ok: false, code: 'timeout' })
      return
    }
    entry.attempts += 1
    this.write({
      type: 'send',
      room_id: entry.roomId,
      client_msg_id: entry.clientMsgId,
      content: entry.content,
    })
    entry.timer = setTimeout(() => this.transmit(entry), ACK_TIMEOUT_MS)
  }

  close() {
    this.closed = true
    for (const entry of this.pending.values()) clearTimeout(entry.timer)
    this.ws.close()
  }
}
