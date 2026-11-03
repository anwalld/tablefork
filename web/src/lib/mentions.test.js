import { describe, expect, it } from 'vitest'
import { insertMention, matchingAis, mentionQuery } from './mentions.js'

const members = [
  { type: 'human', name: 'alice' },
  { type: 'ai', name: 'Claude' },
  { type: 'ai', name: 'GPT' },
  { type: 'ai', name: 'Gemini' },
]

describe('mentions', () => {
  it('finds the partial name after @', () => {
    expect(mentionQuery('hi @cl')).toBe('cl')
    expect(mentionQuery('@')).toBe('')
    expect(mentionQuery('hi there')).toBe(null)
    expect(mentionQuery('mail a@b')).toBe(null)
    expect(mentionQuery('@cl and more', 3)).toBe('cl')
  })

  it('suggests only AIs, by prefix, ignoring case', () => {
    expect(matchingAis(members, 'g').map((m) => m.name)).toEqual(['GPT', 'Gemini'])
    expect(matchingAis(members, '').map((m) => m.name)).toEqual(['Claude', 'GPT', 'Gemini'])
    expect(matchingAis(members, 'al')).toEqual([])
    expect(matchingAis(members, null)).toEqual([])
  })

  it('completes the name and moves the caret after it', () => {
    expect(insertMention('hi @cl', 6, 'Claude')).toEqual({ text: 'hi @Claude ', caret: 11 })
    expect(insertMention('@g rest', 2, 'GPT')).toEqual({ text: '@GPT  rest', caret: 5 })
  })
})
