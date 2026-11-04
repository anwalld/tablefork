// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ChatView from './ChatView.vue'
import { api } from '../lib/api.js'

const state = vi.hoisted(() => ({ handlers: null, sent: [] }))
vi.mock('../lib/api.js', () => ({
  ApiError: class extends Error {},
  api: { rooms: vi.fn(), members: vi.fn(), history: vi.fn(), invite: vi.fn() },
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
  api.rooms.mockResolvedValue({ rooms: [removed, other] })
  api.members.mockResolvedValue({ members: [{ id: 1, name: 'alice', type: 'human', role: 'member' }] })
  api.history.mockImplementation(async (id, options) => ({ messages: id === 7 && !options ? [saved] : [], last_seq: 1 }))
})
afterEach(() => {
  views.forEach(view => view.unmount())
  vi.clearAllTimers()
  vi.useRealTimers()
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
  await view.get('input[aria-label="邀请用户名"]').setValue('outside')
  await view.get('.chat-head form').trigger('submit')
  await flushPromises()
  expect(view.text()).toContain('该用户已被此房间封禁，需要先解封')
})
