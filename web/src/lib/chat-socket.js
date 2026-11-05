import { queueOwner, randomId, queryTask, taskResult } from './queued-task.js'
const ACK_TIMEOUT_MS = 5000
const SEND_DEADLINE_MS = 60000
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
    if (this.closed) return
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
    this.ws = new WebSocket(`${scheme}://${location.host}/ws`)
    this.ws.onopen = () => {
      if (this.closed) return
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
        this.stop()
        this.handlers.onSessionEnded?.()
        return
      }
      this.reconnectTimer = setTimeout(() => this.connect(), this.backoff)
      this.backoff = Math.min(this.backoff * 2, MAX_BACKOFF_MS)
    }
  }

  dispatch(frame) {
    if (this.closed) return
    if (frame.type === 'message') {
      this.handlers.onMessage(frame)
    } else if (frame.type === 'subscribed') {
      this.handlers.onSubscribed(frame.room_id)
    } else if (frame.type === 'room_left') {
      this.rooms.delete(frame.room_id)
      this.handlers.onRoomLeft(frame.room_id, frame.reason ?? 'left')
    } else if (frame.type === 'queued') {
      const entry = this.pending.get(frame.client_msg_id)
      if (entry && !entry.taskId) {
        clearTimeout(entry.timer)
        entry.taskId = frame.task_id
        entry.capability = frame.claim_token ?? queueOwner()
        this.pollTask(entry)
      }
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
      // Preserve the server's waiting period across reconnects as well.
      clearTimeout(entry.timer)
      entry.taskId = null
      entry.queueAttempt = randomId()
      entry.notBefore = Date.now() + frame.error.retry_after * 1000
      this.handlers.onThrottled?.(entry.roomId, entry.clientMsgId, frame.error.retry_after)
      entry.timer = setTimeout(() => this.transmit(entry), frame.error.retry_after * 1000)
    } else if (frame.error.retryable) {
      clearTimeout(entry.timer)
      entry.timer = setTimeout(() => this.transmit(entry), Math.min(1000 * entry.attempts, ACK_TIMEOUT_MS))
    } else {
      this.settle(frame.client_msg_id, { ok: false, code: frame.error.code })
    }
  }

  settle(clientMsgId, result) {
    const entry = this.pending.get(clientMsgId)
    if (!entry) return
    clearTimeout(entry.timer)
    clearTimeout(entry.deadlineTimer)
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
    if (this.closed || this.pending.has(clientMsgId)) return
    const entry = {
      roomId, clientMsgId, content, attempts: 0, timer: null, notBefore: 0, queueAttempt: randomId(),
      expiresAt: Date.now() + SEND_DEADLINE_MS,
    }
    this.pending.set(clientMsgId, entry)
    entry.deadlineTimer = setTimeout(() => {
      this.settle(clientMsgId, { ok: false, code: 'timeout' })
    }, SEND_DEADLINE_MS)
    this.transmit(entry)
  }

  transmit(entry) {
    if (this.closed || this.pending.get(entry.clientMsgId) !== entry) return
    clearTimeout(entry.timer)
    if (entry.taskId) { this.pollTask(entry); return }
    if (Date.now() >= entry.expiresAt) {
      this.settle(entry.clientMsgId, { ok: false, code: 'timeout' })
      return
    }
    if (Date.now() < entry.notBefore) {
      entry.timer = setTimeout(() => this.transmit(entry), entry.notBefore - Date.now())
      return
    }
    const sent = this.write({
      type: 'send',
      room_id: entry.roomId,
      client_msg_id: entry.clientMsgId,
      content: entry.content,
      queue_owner: queueOwner(),
      queue_attempt: entry.queueAttempt,
    })
    // Offline messages wait for onopen; only a frame actually sent needs an ACK.
    if (!sent) return
    entry.attempts += 1
    entry.timer = setTimeout(() => this.transmit(entry), ACK_TIMEOUT_MS)
  }

  async pollTask(entry) {
    if (entry.polling || this.closed || this.pending.get(entry.clientMsgId) !== entry) return
    entry.polling = true
    try {
      const task = await queryTask(entry.taskId, entry.capability)
      if (this.closed || this.pending.get(entry.clientMsgId) !== entry) return
      const result = taskResult(task)
      if (result) {
        if (result.status < 400) this.dispatch(result.body)
        else this.handleError({ type: 'error', client_msg_id: entry.clientMsgId, error: result.body.error })
        return
      }
    } catch { /* Only retry result queries; never create a second business call. */ }
    finally { entry.polling = false }
    if (!this.closed && this.pending.get(entry.clientMsgId) === entry) {
      entry.timer = setTimeout(() => this.pollTask(entry), 1000)
    }
  }

  stop() {
    this.closed = true
    clearTimeout(this.reconnectTimer)
    for (const entry of this.pending.values()) {
      clearTimeout(entry.timer)
      clearTimeout(entry.deadlineTimer)
    }
    this.pending.clear()
  }

  close() {
    this.stop()
    this.ws.close()
  }
}
