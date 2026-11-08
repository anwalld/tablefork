import { expect, it } from 'vitest'
import { commandQuery, insertCommand, matchingCommands } from './commands.js'

const room = { collaboration: true, breakout: false, manager: false }

it('only opens while the caret is in a leading slash word', () => {
  expect(commandQuery('/')).toBe('/')
  expect(commandQuery('/vo')).toBe('/vo')
  expect(commandQuery('/vote 方案')).toBe(null)
  expect(commandQuery('hello /vote')).toBe(null)
  expect(commandQuery('/vote 方案', 3)).toBe('/vo')
})

it('filters by prefix and by where the user is', () => {
  expect(matchingCommands('/', room).map(c => c.name)).toEqual(['/vote', '/breakout', '/continue', '/cancel'])
  expect(matchingCommands('/b', room).map(c => c.name)).toEqual(['/breakout'])
  expect(matchingCommands('/', { ...room, breakout: true }).map(c => c.name)).toContain('/end')
  expect(matchingCommands('/', { ...room, manager: true }).map(c => c.name)).toContain('/veto')
  expect(matchingCommands('/', { ...room, collaboration: false })).toEqual([])
  expect(matchingCommands(null, room)).toEqual([])
})

it('replaces the partial command and keeps what follows', () => {
  expect(insertCommand('/vo', 3, { name: '/vote' })).toEqual({ text: '/vote ', caret: 6 })
  expect(insertCommand('/b 核对日志', 2, { name: '/breakout' })).toEqual({ text: '/breakout 核对日志', caret: 10 })
})
