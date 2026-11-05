import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './api.js'
import { authErrorText, countdown, waitSeconds, waitText } from './auth-errors.js'

describe('authErrorText', () => {
  it('explains which password rule failed', () => {
    expect(authErrorText(new ApiError('weak_password', 'password is too common', 400))).toBe(
      '这个密码太常见，请换一个',
    )
    expect(
      authErrorText(new ApiError('weak_password', 'password must differ from the username', 400)),
    ).toBe('密码不能与用户名相同')
  })

  it('states the new minimum length', () => {
    expect(authErrorText(new ApiError('invalid_argument', 'x', 400))).toContain('至少 10 个字符')
  })

  it('explains limit errors that carry no wait time', () => {
    expect(authErrorText(new ApiError('rate_limited', 'x', 429))).toBe('尝试次数过多，请稍后再试')
    expect(authErrorText(new ApiError('too_many_attempts', 'x', 429))).toBe(
      '尝试次数过多，请稍后再试',
    )
  })

  it('falls back for unknown errors', () => {
    expect(authErrorText(new Error('network'))).toBe('服务暂时不可用，请稍后再试')
  })

  it('distinguishes a task that never started from an uncertain execution', () => {
    expect(authErrorText(new ApiError('queue_timeout', 'x', 408))).toBe('排队超时，请重试')
    expect(authErrorText(new ApiError('result_unknown', 'x', 503))).toBe(
      '执行结果尚无法确认，请勿重复提交',
    )
  })
})

describe('waitSeconds', () => {
  it('reads retry_after from limit errors only', () => {
    expect(waitSeconds(new ApiError('too_many_attempts', 'x', 429, 4))).toBe(4)
    expect(waitSeconds(new ApiError('rate_limited', 'x', 429, 30))).toBe(30)
    expect(waitSeconds(new ApiError('rate_limited', 'x', 429))).toBe(0)
    expect(waitSeconds(new ApiError('invalid_credentials', 'x', 401, 4))).toBe(0)
  })
})

describe('countdown', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('ticks down to zero and stops', () => {
    const ticks = []
    countdown(3, (left) => ticks.push(left))
    vi.advanceTimersByTime(10000)
    expect(ticks).toEqual([3, 2, 1, 0])
    expect(waitText(ticks[1])).toBe('尝试次数过多，请 2 秒后再试')
  })

  it('can be stopped early', () => {
    const ticks = []
    const stop = countdown(3, (left) => ticks.push(left))
    vi.advanceTimersByTime(1000)
    stop()
    vi.advanceTimersByTime(5000)
    expect(ticks).toEqual([3, 2])
  })
})
