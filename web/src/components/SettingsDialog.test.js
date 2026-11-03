// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import SettingsDialog from './SettingsDialog.vue'
import MembersPanel from './MembersPanel.vue'
import { api } from '../lib/api.js'

vi.mock('../lib/api.js', () => ({ api: {
  keys: vi.fn(), storeKey: vi.fn(), deleteKey: vi.fn(), aiAgents: vi.fn(), aiRequests: vi.fn(),
} }))

beforeEach(() => {
  vi.resetAllMocks()
  api.keys.mockResolvedValue({ keys: [] })
})

describe('API key settings', () => {
  it('disables the row while saving and displays masked metadata after success', async () => {
    let finish
    api.storeKey.mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const view = mount(SettingsDialog)
    await flushPromises()
    const input = view.get('input[aria-label="DeepSeek API key"]')
    await input.setValue('test-key')
    await view.findAll('form')[0].trigger('submit')
    expect(view.findAll('button')[0].text()).toBe('验证中')
    expect(view.findAll('button')[0].attributes('disabled')).toBeDefined()
    api.keys.mockResolvedValue({ keys: [{ provider: 'deepseek', last4: '1234', updated_at: 1 }] })
    finish({})
    await flushPromises()
    expect(input.element.value).toBe('')
    expect(view.text()).toContain('…1234')
    expect(view.emitted('changed')).toHaveLength(1)
    view.unmount()
  })

  it('shows a validation failure and makes saving available again', async () => {
    api.storeKey.mockRejectedValue({ code: 'api_key_invalid' })
    const view = mount(SettingsDialog)
    await flushPromises()
    await view.get('input[aria-label="DeepSeek API key"]').setValue('rejected')
    await view.findAll('form')[0].trigger('submit')
    await flushPromises()
    expect(view.text()).toContain('服务商拒绝了这个 key')
    expect(view.findAll('button')[0].attributes('disabled')).toBeUndefined()
    expect(view.emitted('changed')).toBeUndefined()
    view.unmount()
  })

  it('deletes a saved key and refreshes the displayed status', async () => {
    api.keys.mockResolvedValueOnce({ keys: [{ provider: 'deepseek', last4: '1234', updated_at: 1 }] })
    api.deleteKey.mockResolvedValue({})
    const view = mount(SettingsDialog)
    await flushPromises()
    await view.get('button.danger').trigger('click')
    await flushPromises()
    expect(api.deleteKey).toHaveBeenCalledWith('deepseek')
    expect(view.find('button.danger').exists()).toBe(false)
    expect(view.emitted('changed')).toHaveLength(1)
    view.unmount()
  })
})

it('disables inviting an AI whose provider key is missing', async () => {
  api.aiAgents.mockResolvedValue({ agents: [
    { slug: 'deepseek', name: 'DeepSeek', provider: 'deepseek', has_key: false },
    { slug: 'gpt', name: 'GPT', provider: 'openai', has_key: true },
  ] })
  api.aiRequests.mockResolvedValue({ requests: [] })
  const view = mount(MembersPanel, { props: {
    roomId: 1, me: { member_id: 10 }, members: [{ id: 10, name: 'alice', type: 'human', role: 'owner' }],
  } })
  await flushPromises()
  expect(view.get('option[value="deepseek"]').attributes('disabled')).toBeDefined()
  expect(view.get('option[value="gpt"]').attributes('disabled')).toBeUndefined()
  view.unmount()
})
