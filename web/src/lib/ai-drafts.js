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

  chunk({ room_id: roomId, generation_id: id, member_id: memberId, text }) {
    if (this.closed.has(id)) return
    const draft = this.byId.get(id) ?? { id, roomId, memberId, text: '' }
    draft.text += text
    this.byId.set(id, draft)
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
