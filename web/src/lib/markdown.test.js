// @vitest-environment happy-dom
import { expect, it } from 'vitest'
import { byteLength, renderMarkdown } from './markdown.js'

it('renders markdown and strips unsafe HTML', () => {
  const html = renderMarkdown('# 角色\n- 简洁\n\n<a href="javascript:alert(1)">x</a><iframe src="//e"></iframe>')
  expect(html).toContain('<h1>角色</h1>')
  expect(html).toContain('<li>简洁</li>')
  expect(html).not.toContain('javascript:')
  expect(html).not.toContain('<iframe')
})

it('counts UTF-8 bytes like the server limit', () => {
  expect(byteLength('ab')).toBe(2)
  expect(byteLength('人设')).toBe(6)
  expect(byteLength(undefined)).toBe(0)
})
