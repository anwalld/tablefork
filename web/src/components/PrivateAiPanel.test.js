// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import MembersPanel from './MembersPanel.vue'
import { api } from '../lib/api.js'

vi.mock('../lib/api.js', () => ({ api: {
  aiAgents: vi.fn(), aiModels: vi.fn(), aiRequests: vi.fn(), createAiRole: vi.fn(),
  editAiPersona: vi.fn(), requestAi: vi.fn(), editRolePersona: vi.fn(),
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
  expect(view.find('[aria-label="AI角色名称"]').exists()).toBe(false)
  await view.findAll('button').find(b => b.text() === '创建私人AI').trigger('click')
  await flushPromises()
  await view.get('[aria-label="AI角色名称"]').setValue('Claude B')
  await view.get('textarea[aria-label="默认人设"]').setValue('# 角色\n- 简洁')
  api.createAiRole.mockResolvedValue({ id: 502 })
  await view.get('form[aria-label="创建私人AI"]').trigger('submit')
  await flushPromises()
  expect(api.createAiRole).toHaveBeenCalledWith('claude', 'Claude B', '# 角色\n- 简洁')
  expect(view.find('form[aria-label="创建私人AI"]').exists()).toBe(false)
  expect(api.aiAgents).toHaveBeenCalledTimes(2)
})

it('submits the actual role member ID and this room persona', async () => {
  panel(); await flushPromises()
  // The only AI is shown by name; there is nothing to choose.
  expect(view.find('[aria-label="选择 AI"]').exists()).toBe(false)
  expect(view.get('.invite-name').text()).toBe('Claude A')
  await view.get('form[aria-label="邀请AI"]').trigger('submit')
  await view.get('textarea[aria-label="邀请房间人设"]').setValue('严谨')
  api.requestAi.mockResolvedValue({ status: 'joined' })
  await view.get('form[aria-label="邀请AI人设"]').trigger('submit')
  await flushPromises()
  expect(api.requestAi).toHaveBeenCalledWith(7, 501, '严谨')
})

it('room owner has no edit button for another creators role', async () => {
  panel([{ ...role, type: 'ai', role: 'member', creator_user_id: 20, creator_name: 'other' }])
  await flushPromises()
  expect(view.text()).toContain('created by other')
  expect(view.findAll('.member-list button').some(b => b.text() === '人设')).toBe(false)
})

it('creator submits expected persona version and does not claim success on unknown', async () => {
  panel([{ ...role, type: 'ai', role: 'member', creator_user_id: 10, creator_name: 'owner', persona: '旧版', persona_version: 2 }])
  await flushPromises()
  await view.findAll('.member-list button').find(b => b.text() === '人设').trigger('click')
  await view.get('[aria-label="编辑人设正文"]').setValue('新版')
  api.editAiPersona.mockRejectedValue({ code: 'result_unknown' })
  await view.get('[aria-label="编辑房间人设"]').trigger('submit')
  await flushPromises()
  expect(api.editAiPersona).toHaveBeenCalledWith(7, 501, '新版', 2)
  expect(view.emitted('changed')).toBeUndefined()
  expect(view.get('[aria-label="编辑房间人设"]').exists()).toBe(true)
})


it('preselects the first invitable AI and prefills its default persona', async () => {
  api.aiAgents.mockResolvedValue({ agents: [
    { ...role, id: 600, slug: 'role_600', name: 'No Key', has_key: false },
    { ...role, persona: '# 默认\n- 温和' },
    { ...role, id: 502, slug: 'role_502', name: 'Claude B' },
  ] })
  panel(); await flushPromises()
  const select = view.get('[aria-label="选择 AI"]')
  expect(select.element.value).toBe('role_501')
  expect(select.findAll('option').map(o => o.text())).not.toContain('邀请 AI')
  await view.get('form[aria-label="邀请AI"]').trigger('submit')
  expect(view.get('textarea[aria-label="邀请房间人设"]').element.value).toBe('# 默认\n- 温和')
})

it('edits a role default persona from My AI', async () => {
  api.aiAgents.mockResolvedValue({ agents: [{ ...role, persona: '旧' }] })
  panel(); await flushPromises()
  await view.findAll('button').find(b => b.text() === '我的AI').trigger('click')
  await view.get('button[aria-label="Claude A 人设"]').trigger('click')
  expect(view.get('textarea[aria-label="默认人设"]').element.value).toBe('旧')
  await view.get('textarea[aria-label="默认人设"]').setValue('## 新')
  api.editRolePersona.mockResolvedValue({ id: 501, persona: '## 新' })
  await view.get('form[aria-label="编辑默认人设"]').trigger('submit')
  await flushPromises()
  expect(api.editRolePersona).toHaveBeenCalledWith(501, '## 新')
  expect(view.find('form[aria-label="编辑默认人设"]').exists()).toBe(false)
  expect(api.aiAgents).toHaveBeenCalledTimes(2)
})

it('previews persona markdown without running injected HTML', async () => {
  panel(); await flushPromises()
  await view.get('form[aria-label="邀请AI"]').trigger('submit')
  await view.get('textarea[aria-label="邀请房间人设"]').setValue('# 标题\n<img src=x onerror="alert(1)"><script>alert(2)</script>')
  await view.findAll('[role="tab"]').find(b => b.text() === '预览').trigger('click')
  const preview = view.get('[aria-label="人设预览"]')
  expect(preview.find('h1').text()).toBe('标题')
  expect(preview.html()).not.toContain('onerror')
  expect(preview.html()).not.toContain('<script')
})

it('disables a remove button while the request runs', async () => {
  let finish
  api.removeAi = vi.fn(() => new Promise(resolve => { finish = resolve }))
  panel([{ ...role, type: 'ai', role: 'member', creator_user_id: 10, creator_name: 'owner' }])
  await flushPromises()
  const remove = () => view.findAll('.member-list button').find(b => b.text().startsWith('移出'))
  await remove().trigger('click')
  expect(remove().text()).toBe('移出中…')
  expect(remove().element.disabled).toBe(true)
  finish(); await flushPromises()
  expect(remove().text()).toBe('移出')
})
