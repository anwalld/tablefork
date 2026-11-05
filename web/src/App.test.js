// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import App from './App.vue'
import { api, ApiError } from './lib/api.js'

vi.mock('./lib/api.js', async (original) => {
  const module = await original()
  return { ...module, api: { me: vi.fn(), logout: vi.fn() } }
})
vi.mock('./lib/session-refresh.js', () => ({ keepSessionAlive: () => () => {} }))

let view
beforeEach(() => {
  vi.clearAllMocks()
  api.me.mockResolvedValue({ id: 1, username: 'alice' })
  api.logout.mockResolvedValue({})
})
afterEach(() => view?.unmount())
async function open() {
  view = mount(App, { global: { stubs: {
    ChatView: { props: ['user', 'logoutError', 'loggingOut'], emits: ['logout'],
      template: '<section data-testid="chat"><p v-if="logoutError" role="alert">{{ logoutError }}</p><button :disabled="loggingOut" @click="$emit(\'logout\')">退出登录</button></section>' },
    AuthView: { template: '<h1>登录页面</h1>' },
  } } })
  await flushPromises()
}

it.each([
  ['result_unknown', '执行结果尚无法确认，请勿重复提交'],
  ['queue_timeout', '排队超时，请重试'],
])('does not report logout success after %s', async (code, message) => {
  await open()
  api.logout.mockRejectedValue(new ApiError(code, message, 503))
  await view.get('button').trigger('click')
  await flushPromises()
  expect(view.find('[data-testid="chat"]').exists()).toBe(true)
  expect(view.get('[role="alert"]').text()).toBe(message)
  expect(view.text()).not.toContain('登录页面')
})

it('shows the login page only after a confirmed logout', async () => {
  await open()
  await view.get('button').trigger('click')
  await flushPromises()
  expect(view.text()).toContain('登录页面')
  expect(view.find('[data-testid="chat"]').exists()).toBe(false)
})

it('keeps only one logout in flight until its result arrives', async () => {
  await open()
  let finish
  api.logout.mockImplementation(() => new Promise(resolve => { finish = resolve }))
  await view.get('button').trigger('click')
  await flushPromises()
  expect(view.get('button').element.disabled).toBe(true)
  await view.get('button').trigger('click')
  expect(api.logout).toHaveBeenCalledTimes(1)
  finish({})
  await flushPromises()
  expect(view.text()).toContain('登录页面')
})
