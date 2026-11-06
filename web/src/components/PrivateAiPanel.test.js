// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import MembersPanel from './MembersPanel.vue'
import { api } from '../lib/api.js'

vi.mock('../lib/api.js', () => ({ api: {
  aiAgents: vi.fn(), aiModels: vi.fn(), aiRequests: vi.fn(), createAiRole: vi.fn(),
  editAiPersona: vi.fn(), requestAi: vi.fn(),
} }))
let view
const role = { id: 501, slug: 'role_501', model_slug: 'claude', name: 'Claude A', provider: 'anthropic', has_key: true }
function panel(extra = []) {
  view = mount(MembersPanel, { props: { roomId: 7, me: { id: 10, member_id: 1 },
    members: [{ id: 1, name: 'owner', role: 'owner', type: 'human' }, ...extra] } })
  return view
}
beforeEach(() => {
  vi.resetAllMocks()
  api.aiAgents.mockResolvedValue({ agents: [role] })
  api.aiModels.mockResolvedValue({ models: [{ model_slug: 'claude', name: 'Claude' }] })
  api.aiRequests.mockResolvedValue({ requests: [] })
})
afterEach(() => view?.unmount())

it('creates a private role through My AI and reloads the private catalog', async () => {
  panel(); await flushPromises()
  await view.findAll('button').find(b => b.text() === '我的AI').trigger('click')
  await flushPromises()
  await view.get('[aria-label="AI角色名称"]').setValue('Claude B')
  api.createAiRole.mockResolvedValue({ id: 502 })
  await view.get('[aria-label="我的AI"] form').trigger('submit')
  await flushPromises()
  expect(api.createAiRole).toHaveBeenCalledWith('claude', 'Claude B')
  expect(api.aiAgents).toHaveBeenCalledTimes(2)
})

it('submits the actual role member ID and this room persona', async () => {
  panel(); await flushPromises()
  await view.get('[aria-label="选择 AI"]').setValue('role_501')
  await view.get('[aria-label="邀请房间人设"]').setValue('严谨')
  api.requestAi.mockResolvedValue({ status: 'joined' })
  await view.get('form.inline-form').trigger('submit')
  await flushPromises()
  expect(api.requestAi).toHaveBeenCalledWith(7, 501, '严谨')
})

it('room owner has no edit button for another creators role', async () => {
  panel([{ ...role, type: 'ai', role: 'member', creator_user_id: 20, creator_name: 'other' }])
  await flushPromises()
  expect(view.text()).toContain('created by other')
  expect(view.text()).not.toContain('编辑人设')
})

it('creator submits expected persona version and does not claim success on unknown', async () => {
  panel([{ ...role, type: 'ai', role: 'member', creator_user_id: 10, creator_name: 'owner', persona: '旧版', persona_version: 2 }])
  await flushPromises()
  await view.findAll('button').find(b => b.text() === '编辑人设').trigger('click')
  await view.get('[aria-label="编辑人设正文"]').setValue('新版')
  api.editAiPersona.mockRejectedValue({ code: 'result_unknown' })
  await view.get('[aria-label="编辑房间人设"]').trigger('submit')
  await flushPromises()
  expect(api.editAiPersona).toHaveBeenCalledWith(7, 501, '新版', 2)
  expect(view.emitted('changed')).toBeUndefined()
  expect(view.get('[aria-label="编辑房间人设"]').exists()).toBe(true)
})
