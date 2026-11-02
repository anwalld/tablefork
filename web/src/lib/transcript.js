import { api } from './api.js'

export async function fetchAllMessages(roomId, history = api.history) {
  const all = []
  let after = 0
  while (true) {
    const { messages } = await history(roomId, { after })
    if (messages.length === 0) return all
    all.push(...messages)
    after = messages[messages.length - 1].seq
  }
}

export function formatTranscript(roomName, messages) {
  const lines = messages.map((message) => {
    const time = new Date(message.created_at).toISOString()
    return `[${time}] ${message.member.name}: ${message.content}`
  })
  return [`# ${roomName}`, ...lines, ''].join('\n')
}

// The file stays on the user's machine; nothing is uploaded.
export async function exportRoom(room) {
  const messages = await fetchAllMessages(room.id)
  const blob = new Blob([formatTranscript(room.name, messages)], {
    type: 'text/plain;charset=utf-8',
  })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = `${room.name}-聊天记录.txt`
  link.click()
  URL.revokeObjectURL(link.href)
  return messages.length
}
