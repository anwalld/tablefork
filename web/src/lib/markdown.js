import DOMPurify from 'dompurify'
import { marked } from 'marked'

export const PERSONA_LIMIT = 16384

// Persona previews come from user text, so the HTML is always sanitized.
export function renderMarkdown(text) {
  return DOMPurify.sanitize(marked.parse(text ?? '', { async: false, gfm: true, breaks: true }))
}

export function byteLength(text) {
  return new TextEncoder().encode(text ?? '').length
}

export const PERSONA_TEMPLATE = `# 角色
一句话说明这个 AI 是谁。

## 性格
- 例如：耐心、幽默

## 说话方式
- 例如：回答简短，先给结论

## 禁忌
- 例如：不编造事实
`
