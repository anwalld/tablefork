// "@" suggestions in the message box.

// The partial name being typed after an "@" right before the caret, or null.
export function mentionQuery(text, caret = text.length) {
  const before = text.slice(0, caret)
  const match = /(^|\s)@([^\s@]*)$/.exec(before)
  return match ? match[2] : null
}

export function matchingMembers(members, query) {
  if (query === null) return []
  const lowered = query.toLowerCase()
  return members.filter((m) => m.name.toLowerCase().startsWith(lowered))
}

export function matchingAis(members, query) {
  if (query === null) return []
  const lowered = query.toLowerCase()
  return members.filter((m) => m.type === 'ai' && m.name.toLowerCase().startsWith(lowered))
}

// Replaces the partial "@name" before the caret with the chosen name.
export function insertMention(text, caret, name) {
  const before = text.slice(0, caret).replace(/@([^\s@]*)$/, `@${name} `)
  return { text: before + text.slice(caret), caret: before.length }
}
