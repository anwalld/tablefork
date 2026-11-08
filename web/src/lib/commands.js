// "/" commands in the message box. The chat server parses the first word
// (English names, Chinese aliases); this list only drives the picker.

export const COMMANDS = [
  { name: '/vote', args: '议题', description: '发起主席团表决：主席（AI）各自独立判断，加权过半才通过', scope: 'room' },
  { name: '/breakout', args: '@成员 议题', description: '开一个小会（分桌）：继承当前上下文，结论回到这里', scope: 'room' },
  { name: '/continue', args: '', description: '确认执行正在等待的下一步', scope: 'any' },
  { name: '/cancel', args: '', description: '取消正在等待的下一步', scope: 'any' },
  { name: '/veto', args: '', description: '否决当前这轮表决的结论（群主、管理员）', scope: 'any', manager: true },
  { name: '/force-pass', args: '', description: '强制通过当前这轮表决（群主、管理员）', scope: 'any', manager: true },
  { name: '/pause', args: '', description: '暂停这个小会里的 AI 发言', scope: 'breakout' },
  { name: '/resume', args: '', description: '恢复这个小会里的 AI 发言', scope: 'breakout' },
  { name: '/end', args: '', description: '结束这个小会及其下级小会，结论发回上一级', scope: 'breakout' },
]

// The command being typed: the text from a leading "/" up to the caret, while
// the caret is still inside that first word. Null otherwise.
export function commandQuery(text, caret = text.length) {
  const before = text.slice(0, caret)
  return /^\/[^\s]*$/.test(before) ? before : null
}

// Commands that make sense here. They only act in collaboration rooms;
// breakout controls only inside a breakout, overrides only for managers.
export function matchingCommands(query, { collaboration, breakout, manager }) {
  if (query === null || !collaboration) return []
  const lowered = query.toLowerCase()
  return COMMANDS.filter((command) =>
    command.name.startsWith(lowered) &&
    (command.scope !== 'breakout' || breakout) &&
    (!command.manager || manager))
}

// Replaces the partial command with the chosen one and a trailing space.
export function insertCommand(text, caret, command) {
  const inserted = `${command.name} `
  return { text: inserted + text.slice(caret).replace(/^\S*\s?/, ''), caret: inserted.length }
}
