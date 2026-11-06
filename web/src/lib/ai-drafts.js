// Replies being generated, shown until the finished message arrives.
//
// Chunks and the finished message travel separately, so a late chunk may
// arrive after its message; generations already finished or failed are
// remembered and their chunks ignored.
export class AiDrafts {
  constructor() {
    this.byId = new Map() // generation id -> { roomId, memberId, text }
    this.closed = new Set()
  }

  chunk(frame) {
    const { room_id: roomId, generation_id: id, member_id: memberId, text } = frame
    if (this.closed.has(id)) return
    const current = this.byId.get(id)
    const attempt = frame.attempt ?? 0
    const sequence = frame.sequence ?? 0
    if (current && (attempt < current.attempt ||
        (attempt === current.attempt && sequence <= current.sequence))) return
    this.byId.set(id, { id, roomId, memberId, text: frame.snapshot ?? text, attempt, sequence, ...(frame.admission_id ? { admissionId: frame.admission_id } : {}) })
  }

  clear(roomId) {
    for (const [id, draft] of this.byId) {
      if (roomId === undefined || draft.roomId === roomId) this.byId.delete(id)
    }
  }

  // A stored message or a failure ends the generation.
  close(id) {
    this.byId.delete(id)
    this.closed.add(id)
    if (this.closed.size > 500) {
      this.closed.delete(this.closed.values().next().value)
    }
  }

  forRoom(roomId) {
    return [...this.byId.values()].filter((d) => d.roomId === roomId)
  }
}
