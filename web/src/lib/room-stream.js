// One room's message list. Keeps messages sorted by seq and deduplicated, and
// fetches what is missing, whichever way a message arrived.
export class RoomStream {
  // fetchAfter(seq) resolves to the next page of messages after seq.
  constructor({ fetchAfter, onChange = () => {}, gapTimeoutMs = 800 }) {
    this.fetchAfter = fetchAfter
    this.onChange = onChange
    this.gapTimeoutMs = gapTimeoutMs
    this.bySeq = new Map()
    // Everything from the loaded window's start up to here has arrived.
    this.contiguous = 0
    this.gapTimer = null
    this.syncing = null
  }

  get messages() {
    return [...this.bySeq.values()].sort((a, b) => a.seq - b.seq)
  }

  // Adds a page of history, which may start anywhere.
  addPage(messages) {
    if (messages.length === 0) return
    for (const message of messages) this.bySeq.set(message.seq, message)
    const newest = messages[messages.length - 1].seq
    if (this.contiguous < messages[0].seq - 1) {
      // First page, or a page past a hole too old to matter.
      this.contiguous = newest
    }
    this.advance()
    this.onChange()
  }

  receive(message) {
    if (this.bySeq.has(message.seq)) return
    this.bySeq.set(message.seq, message)
    this.advance()
    this.onChange()
    this.watchForGap()
  }

  advance() {
    while (this.bySeq.has(this.contiguous + 1)) this.contiguous += 1
  }

  hasGap() {
    return [...this.bySeq.keys()].some((seq) => seq > this.contiguous)
  }

  // A gap often closes by itself because pushes can arrive out of order, so
  // wait briefly before asking the server.
  watchForGap() {
    if (!this.hasGap()) {
      clearTimeout(this.gapTimer)
      this.gapTimer = null
    } else if (!this.gapTimer) {
      this.gapTimer = setTimeout(() => {
        this.gapTimer = null
        if (this.hasGap()) this.sync()
      }, this.gapTimeoutMs)
    }
  }

  // Fetches everything after the last contiguous message.
  sync() {
    this.syncing ??= this.fetchMissing().finally(() => {
      this.syncing = null
      this.watchForGap()
    })
    return this.syncing
  }

  async fetchMissing() {
    while (true) {
      const page = await this.fetchAfter(this.contiguous)
      if (page.length === 0) return
      for (const message of page) this.bySeq.set(message.seq, message)
      this.advance()
      this.onChange()
    }
  }

  dispose() {
    clearTimeout(this.gapTimer)
  }
}
