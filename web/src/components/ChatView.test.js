// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ChatView from './ChatView.vue'
import { api } from '../lib/api.js'

const state = vi.hoisted(() => ({ handlers: null, sent: [] }))
vi.mock('../lib/api.js', () => ({
  ApiError: class extends Error {},
  api: { rooms: vi.fn(), members: vi.fn(), history: vi.fn(), invite: vi.fn(), createRoom: vi.fn(), markRead: vi.fn(), search: vi.fn(), aiState: vi.fn(), pauseAi: vi.fn(), stopAi: vi.fn() },
}))
vi.mock('../lib/chat-socket.js', () => ({ ChatSocket: class {
  constructor(handlers) { state.handlers = handlers; this.handlers = handlers; this.rooms = new Set() }
  subscribe(id) { this.rooms.add(id); this.handlers.onSubscribed(id) }
  unsubscribe(id) { this.rooms.delete(id) }
  send(roomId, clientMsgId, content) { state.sent.push({ roomId, clientMsgId, content }) }
  close() {}
} }))

const removed = { id: 7, name: '测试房间', status: 'joined', member_count: 2, last_seq: 1 }
const other = { id: 8, name: '另一个房间', status: 'joined', member_count: 1, last_seq: 0 }
const saved = { type: 'message', room_id: 7, seq: 1, content: '已经显示的消息', created_at: 1700000000000,
  member: { id: 2, name: 'bob', type: 'human' } }
let views
async function chat() {
  const view = mount(ChatView, { props: { user: { username: 'alice', member_id: 1 } },
    global: { stubs: { MembersPanel: true, SettingsDialog: true, LeaveDialog: true } } })
  views.push(view)
  await flushPromises()
  return view
}
async function select(view, name) {
  await view.findAll('.room').find(room => room.text().includes(name)).trigger('click')
  await flushPromises()
}
async function poll() {
  await vi.advanceTimersByTimeAsync(10000)
  await flushPromises()
}
beforeEach(() => {
  views = []
  state.sent = []
  vi.useFakeTimers()
  vi.resetAllMocks()
  api.markRead.mockResolvedValue({})
  api.aiState.mockResolvedValue({ paused: false, generations: [] })
  api.pauseAi.mockResolvedValue({})
  api.stopAi.mockResolvedValue({})
  api.search.mockResolvedValue({ messages: [], next_before: null })
  api.rooms.mockResolvedValue({ rooms: [removed, other] })
  api.members.mockResolvedValue({ members: [{ id: 1, name: 'alice', type: 'human', role: 'member' }] })
  api.history.mockImplementation(async (id, options) => ({ messages: id === 7 && !options ? [saved] : [], last_seq: 1 }))
})
afterEach(() => {
  views.forEach(view => view.unmount())
  vi.clearAllTimers()
  vi.useRealTimers()
})

it('keeps the room draft and warns against repeating an uncertain creation', async () => {
  const view = await chat()
  api.createRoom.mockRejectedValue({ code: 'result_unknown' })
  expect(view.find('input[aria-label="新房间名称"]').exists()).toBe(false)
  await view.findAll('button').find(b => b.text() === '新建房间').trigger('click')
  await view.get('input[aria-label="新房间名称"]').setValue('new-room')
  await view.get('form[aria-label="新建房间"]').trigger('submit')
  await flushPromises()
  expect(view.get('.toast[role="status"]').text()).toBe('执行结果尚无法确认，请勿重复提交')
  expect(view.get('input[aria-label="新房间名称"]').element.value).toBe('new-room')
  expect(api.createRoom).toHaveBeenCalledTimes(1)
})

it.each([
  ['kicked', '已被踢出', '你已被踢出房间 测试房间'],
  ['banned', '已被封禁', '你已被禁止进入房间 测试房间'],
])('keeps the active room and loaded history after %s', async (reason, tag, message) => {
  const view = await chat()
  expect(view.text()).toContain(saved.content)
  state.handlers.onRoomLeft(7, reason)
  await flushPromises()
  expect(view.findAll('.room-name').map(item => item.text())).toEqual(['测试房间', '另一个房间'])
  expect(view.findAll('.room')[0].text()).toContain(tag)
  expect(view.get('.chat-head h1').text()).toBe('测试房间')
  expect(view.text()).toContain(saved.content)
  expect(view.text()).toContain(message)
  expect(view.get('textarea').element.disabled).toBe(true)
  expect(view.get('.composer button').element.disabled).toBe(true)
  expect(view.find('members-panel-stub').exists()).toBe(false)
  api.members.mockClear()
  api.history.mockClear()
  state.handlers.onSubscribed(7)
  state.handlers.onEvent({ type: 'members_changed', room_id: 7 })
  state.handlers.onMessage({ ...saved, seq: 2, content: 'late unwanted push' })
  api.rooms.mockResolvedValue({ rooms: [other] })
  await poll()
  expect(api.members).not.toHaveBeenCalled()
  expect(api.history).not.toHaveBeenCalled()
  expect(view.text()).not.toContain('late unwanted push')
  expect(view.get('.chat-head h1').text()).toBe('测试房间')
  expect(view.text()).toContain(message)
})

it('opens an inactive removed room using only its local messages', async () => {
  const view = await chat()
  await select(view, '另一个房间')
  state.handlers.onRoomLeft(7, 'kicked')
  await flushPromises()
  api.members.mockClear()
  api.history.mockClear()
  await select(view, '测试房间')
  expect(view.text()).toContain(saved.content)
  expect(view.text()).toContain('你已被踢出房间 测试房间')
  expect(api.members).not.toHaveBeenCalled()
  expect(api.history).not.toHaveBeenCalled()
})

it('retains removed rooms across refresh but accepts a new server invitation', async () => {
  const view = await chat()
  state.handlers.onRoomLeft(7, 'kicked')
  api.rooms.mockResolvedValue({ rooms: [other] })
  await poll()
  expect(view.findAll('.room').map(room => room.text()).join(' ')).toContain('已被踢出')
  api.rooms.mockResolvedValue({ rooms: [{ ...removed, status: 'invited' }, other] })
  await poll()
  expect(view.text()).not.toContain('已被踢出')
  expect(view.get('.invite-panel').text()).toContain('你被邀请加入这个房间')
})

it('does not restore an old list response after revocation', async () => {
  const view = await chat()
  let finish
  api.rooms.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  await vi.advanceTimersByTimeAsync(10000)
  state.handlers.onRoomLeft(7, 'kicked')
  finish({ rooms: [removed, other] })
  await flushPromises()
  expect(view.findAll('.room')[0].text()).toContain('已被踢出')
})

it.each([undefined, 'left'])('keeps the legacy removal behavior for %s', async reason => {
  const view = await chat()
  state.handlers.onRoomLeft(7, reason)
  await flushPromises()
  expect(view.findAll('.room-name')).toHaveLength(2)
  expect(view.find('.chat-head').exists()).toBe(false)
  expect(view.text()).not.toContain('你已被踢出')
  expect(view.text()).not.toContain(saved.content)
})

it('shows an acknowledged local message when history fails and replaces it with the server copy', async () => {
  const view = await chat()
  await view.get('textarea').setValue('刚刚发送的消息')
  await view.get('.composer').trigger('submit')
  const sent = state.sent.at(-1)
  api.history.mockRejectedValueOnce(new Error('history unavailable'))
  state.handlers.onSendResult(7, sent.clientMsgId, { ok: true, seq: 2, created_at: 1700000002000 })
  await flushPromises()
  expect(view.findAll('.message')).toHaveLength(2)
  const local = view.findAll('.message')[1]
  expect(local.text()).toContain('刚刚发送的消息')
  expect(local.text()).toContain('alice')
  expect(local.classes()).toContain('mine')
  expect(view.vm.$.setupState.timeline[1]).toMatchObject({ local: true, seq: 2,
    content: sent.content, member: { id: 1, name: 'alice', type: 'human' } })
  state.handlers.onMessage({ type: 'message', room_id: 7, seq: 2, created_at: 1700000002000,
    client_msg_id: sent.clientMsgId, content: '服务端正式版本', member: { id: 1, name: 'alice', type: 'human' } })
  await flushPromises()
  expect(view.findAll('.message')).toHaveLength(2)
  expect(view.findAll('.message')[1].text()).toContain('服务端正式版本')
  expect(view.text()).not.toContain('刚刚发送的消息')
  expect(view.vm.$.setupState.timeline[1].local).toBeUndefined()
})

it('keeps an ack arriving after removal and never backfills its sequence gap', async () => {
  const view = await chat()
  await view.get('textarea').setValue('移出前已保存')
  await view.get('.composer').trigger('submit')
  const sent = state.sent.at(-1)
  state.handlers.onRoomLeft(7, 'banned')
  await flushPromises()
  api.history.mockClear()
  state.handlers.onSendResult(7, sent.clientMsgId, { ok: true, seq: 4, created_at: 1700000004000 })
  await flushPromises()
  await vi.advanceTimersByTimeAsync(2000)
  expect(api.history).not.toHaveBeenCalled()
  expect(view.text()).toContain('移出前已保存')
  expect(view.vm.$.setupState.timeline.map(message => message.seq)).toEqual([1, 4])
  expect(view.get('textarea').element.disabled).toBe(true)
})

it('explains that a banned invitee must be unbanned first', async () => {
  const view = await chat()
  api.invite.mockRejectedValue({ code: 'user_banned' })
  await view.findAll('.chat-head button').find(b => b.text() === '邀请').trigger('click')
  await view.get('input[aria-label="邀请用户名"]').setValue('outside')
  await view.get('form[aria-label="邀请用户"]').trigger('submit')
  await flushPromises()
  expect(view.text()).toContain('该用户已被此房间封禁，需要先解封')
})


it('shows unread and mention indicators and includes human mention candidates', async () => {
  api.rooms.mockResolvedValue({ rooms: [{ ...removed, unread_count: 4, mention_seq: 1 }] })
  api.members.mockResolvedValue({ members: [
    { id: 1, name: 'alice', type: 'human', role: 'member' },
    { id: 2, name: 'bob', type: 'human', role: 'member' },
  ] })
  const view = await chat()
  expect(view.get('[aria-label="未读消息"]').text()).toBe('4')
  expect(view.text()).toContain('有人@我')
  await view.get('textarea').setValue('@bo')
  const textarea = view.get('textarea').element
  textarea.selectionStart = 3
  await view.get('textarea').trigger('keyup')
  expect(view.get('[aria-label="提及成员"]').text()).toContain('@bob')
})

it('renders a stopped reply from history and lets managers pause AI', async () => {
  api.members.mockResolvedValue({ members: [{ id: 1, name: 'alice', type: 'human', role: 'owner' }] })
  api.history.mockImplementation(async (id, options) => ({ messages: id === 7 && !options ? [{ ...saved, stopped: true }] : [] }))
  const view = await chat()
  expect(view.text()).toContain('已停止')
  const button = view.findAll('button').find(b => b.text() === '暂停AI')
  await button.trigger('click')
  await flushPromises()
  expect(api.pauseAi).toHaveBeenCalledWith(7, true)
})

it('searches through the authenticated API and renders returned matches', async () => {
  api.search.mockResolvedValue({ messages: [saved], next_before: 1 })
  const view = await chat()
  await view.findAll('button').find(b => b.text() === '搜索').trigger('click')
  await view.get('[aria-label="搜索关键词"]').setValue('匹配')
  await view.get('.search-panel form').trigger('submit')
  await flushPromises()
  expect(api.search).toHaveBeenCalledWith(7, { q: '匹配' })
  expect(view.get('.search-panel').text()).toContain(saved.content)
})


it('marks only displayed latest messages when visible and does not mark in background', async () => {
  const originalVisibility = Object.getOwnPropertyDescriptor(document, 'visibilityState')
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
  const view = await chat()
  await vi.advanceTimersByTimeAsync(200)
  expect(api.markRead).toHaveBeenCalledWith(7, 1)
  api.markRead.mockClear()
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' })
  state.handlers.onMessage({ ...saved, seq: 2, content: 'while hidden' })
  await flushPromises()
  await vi.advanceTimersByTimeAsync(200)
  expect(api.markRead).not.toHaveBeenCalled()
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
  document.dispatchEvent(new Event('visibilitychange'))
  await vi.advanceTimersByTimeAsync(200)
  expect(api.markRead).toHaveBeenCalledWith(7, 2)
  if (originalVisibility) Object.defineProperty(document, 'visibilityState', originalVisibility)
  else delete document.visibilityState
})


it('keeps search results across background room polling', async () => {
  api.rooms.mockImplementation(async () => ({ rooms: [{ ...removed }, { ...other }] }))
  api.search.mockResolvedValue({ messages: [saved], next_before: null })
  const view = await chat()
  await view.findAll('button').find(b => b.text() === '搜索').trigger('click')
  await view.get('.search-panel form').trigger('submit')
  await flushPromises()
  await poll()
  expect(view.get('.search-panel').text()).toContain(saved.content)
})


it('refreshes room attention promptly after messages and another page reading', async () => {
  const view = await chat()
  const previous = api.rooms.mock.calls.length
  api.rooms.mockResolvedValue({ rooms: [removed, { ...other, unread_count: 1, mention_seq: 1 }] })
  state.handlers.onMessage({ ...saved, room_id: 8, seq: 1 })
  await vi.advanceTimersByTimeAsync(250)
  await flushPromises()
  expect(api.rooms.mock.calls.length).toBeGreaterThan(previous)
  expect(view.get('[aria-label="未读消息"]').text()).toBe('1')
  api.rooms.mockResolvedValue({ rooms: [removed, other] })
  state.handlers.onEvent({ type: 'read_updated', room_id: 8 })
  await vi.advanceTimersByTimeAsync(250)
  await flushPromises()
  expect(view.find('[aria-label="未读消息"]').exists()).toBe(false)
})


it('discards late context loading after room membership is revoked', async () => {
  const target = { ...saved, seq: 3, content: 'late revoked context' }
  api.search.mockResolvedValue({ messages: [target], next_before: null })
  const view = await chat()
  await view.findAll('button').find(b => b.text() === '搜索').trigger('click')
  await view.get('.search-panel form').trigger('submit')
  await flushPromises()
  let resolvePage
  api.history.mockImplementation(async (id, params) => {
    if (params?.before === 4) return await new Promise(resolve => { resolvePage = resolve })
    return { messages: [] }
  })
  await view.findAll('.search-panel button').find(b => b.text().includes(target.content)).trigger('click')
  await flushPromises()
  state.handlers.onRoomLeft(7, 'kicked')
  resolvePage({ messages: [target] })
  await flushPromises()
  expect(view.findAll('article').some(article => article.text().includes(target.content))).toBe(false)
})

it('ignores an old admission chunk after the same AI member rejoined', async () => {
  api.members.mockResolvedValue({ members: [
    { id: 1, name: 'alice', type: 'human', role: 'owner' },
    { id: 501, name: 'Claude A', type: 'ai', role: 'member', admission_id: 'NEW' },
  ] })
  const view = await chat()
  state.handlers.onEvent({ type: 'ai_chunk', room_id: 7, member_id: 501,
    generation_id: 'old-draft', admission_id: 'OLD', snapshot: 'retired admission must stay hidden' })
  await flushPromises()
  expect(view.text()).not.toContain('retired admission must stay hidden')
  state.handlers.onEvent({ type: 'ai_chunk', room_id: 7, member_id: 501,
    generation_id: 'new-draft', admission_id: 'NEW', snapshot: 'current admission is visible' })
  await flushPromises()
  expect(view.text()).toContain('current admission is visible')
})


it.each(['partial retained', ''])('renders durable failed history with body %j', async (content) => {
  api.history.mockImplementation(async (id, options) => ({ messages: id === 7 && !options
    ? [{ ...saved, content, generation_outcome: 'failed', generation_id: 'failed-generation' }] : [] }))
  const view = await chat()
  expect(view.text()).toContain('生成失败')
  expect(view.findAll('.messages .message')).toHaveLength(1)
  expect(view.get('.messages .bubble').text()).toBe(content)
})


it('creates a room from the dialog and shows progress while it runs', async () => {
  const view = await chat()
  let finish
  api.createRoom.mockReturnValue(new Promise(resolve => { finish = resolve }))
  await view.findAll('button').find(b => b.text() === '新建房间').trigger('click')
  await view.get('input[aria-label="新房间名称"]').setValue('fresh')
  await view.get('form[aria-label="新建房间"]').trigger('submit')
  const submit = view.get('form[aria-label="新建房间"] button.primary')
  expect(submit.text()).toBe('创建中…')
  expect(submit.element.disabled).toBe(true)
  await view.get('form[aria-label="新建房间"]').trigger('submit')
  expect(api.createRoom).toHaveBeenCalledTimes(1)
  finish({ id: 99 })
  await flushPromises()
  expect(view.find('form[aria-label="新建房间"]').exists()).toBe(false)
})
