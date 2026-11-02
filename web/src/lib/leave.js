// Leaving a room takes two steps: find out whether the user is the last human
// member, which decides the warning shown, then leave with that answer.

export async function prepareLeave(api, room) {
  const { members } = await api.members(room.id)
  return { room, isLastHuman: members.filter((m) => m.type === 'human').length === 1 }
}

// Resolves to null once the user has left, or to an updated prompt when the
// server says the room would be deleted but the prompt did not warn about it.
export async function leave(api, prompt) {
  try {
    await api.leave(prompt.room.id, prompt.isLastHuman)
    return null
  } catch (e) {
    if (e.code === 'confirmation_required') {
      // Everyone else left while the dialog was open.
      return { ...prompt, isLastHuman: true }
    }
    throw e
  }
}
