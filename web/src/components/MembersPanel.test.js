// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import MembersPanel from './MembersPanel.vue'
import { api } from '../lib/api.js'

vi.mock('../lib/api.js', () => ({ api: {
  aiAgents: vi.fn(), aiRequests: vi.fn(), listBans: vi.fn(), kickMember: vi.fn(),
  banUser: vi.fn(), unbanUser: vi.fn(),
} }))

const members = [
  { id: 1, name: 'alice', role: 'owner', type: 'human' },
  { id: 2, name: 'bob', role: 'admin', type: 'human' },
  { id: 3, name: 'carol', role: 'member', type: 'human' },
  { id: 4, name: 'dana', role: 'admin', type: 'human' },
  { id: 5, name: 'Claude', role: 'member', type: 'ai', slug: 'claude' },
]
let views
function panel(memberId = 1) {
  const view = mount(MembersPanel, { props: { roomId: 7, me: { member_id: memberId }, members } })
  views.push(view)
  return view
}

beforeEach(() => {
  views = []
  vi.resetAllMocks()
  api.aiAgents.mockResolvedValue({ agents: [] })
  api.aiRequests.mockResolvedValue({ requests: [] })
  api.listBans.mockResolvedValue({ bans: [] })
  for (const action of [api.kickMember, api.banUser, api.unbanUser]) action.mockResolvedValue({})
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})
afterEach(() => {
  views.forEach(view => view.unmount())
  vi.restoreAllMocks()
})

describe('human room management', () => {
  it.each([
    [1, ['bob', 'carol', 'dana'], true], [2, ['carol'], true], [3, [], false],
  ])('shows only permitted targets for member %s', async (id, targets, canManage) => {
    const view = panel(id)
    await flushPromises()
    for (const member of members) {
      expect(view.find(`button[aria-label="踢出 ${member.name}"]`).exists()).toBe(targets.includes(member.name))
      expect(view.find(`button[aria-label="封禁 ${member.name}"]`).exists()).toBe(targets.includes(member.name))
    }
    expect(view.findAll('button').some(button => button.text() === '封禁列表')).toBe(canManage)
  })

  it('requires confirmation and includes the target username', async () => {
    const view = panel()
    await flushPromises()
    window.confirm.mockReturnValueOnce(false)
    await view.get('button[aria-label="踢出 carol"]').trigger('click')
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('carol'))
    expect(api.kickMember).not.toHaveBeenCalled()
    await view.get('button[aria-label="封禁 bob"]').trigger('click')
    await flushPromises()
    expect(window.confirm).toHaveBeenLastCalledWith(expect.stringContaining('bob'))
    expect(api.banUser).toHaveBeenCalledWith(7, 'bob')
    expect(view.emitted('changed')).toHaveLength(1)
  })

  it('lists actor and time, bans an outside user and unbans without restoring membership', async () => {
    const view = panel()
    await flushPromises()
    api.listBans.mockResolvedValue({ bans: [{ username: 'outside', banned_by: 'alice', created_at: 1700000000000 }] })
    await view.findAll('button').find(button => button.text() === '封禁列表').trigger('click')
    await flushPromises()
    const section = view.get('section[aria-label="封禁列表"]')
    expect(section.text()).toContain('outside')
    expect(section.text()).toContain('执行者：alice')
    expect(section.find('time').text()).not.toBe('')
    await section.get('input').setValue('new-user')
    await section.get('form').trigger('submit')
    await flushPromises()
    expect(api.banUser).toHaveBeenCalledWith(7, 'new-user')
    api.listBans.mockResolvedValue({ bans: [] })
    await view.get('button[aria-label="解封 outside"]').trigger('click')
    await flushPromises()
    expect(api.unbanUser).toHaveBeenCalledWith(7, 'outside')
    expect(view.get('section[aria-label="封禁列表"]').text()).toContain('暂无封禁记录')
  })

  it('reports a server permission change', async () => {
    const view = panel()
    await flushPromises()
    api.kickMember.mockRejectedValue({ code: 'forbidden' })
    await view.get('button[aria-label="踢出 carol"]').trigger('click')
    await flushPromises()
    expect(view.emitted('notice').at(-1)).toEqual(['你没有权限管理该成员'])
    expect(view.emitted('changed')).toBeUndefined()
  })
})
