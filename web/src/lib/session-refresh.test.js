import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './api.js'
import { keepSessionAlive, REFRESH_INTERVAL_MS } from './session-refresh.js'

describe('keepSessionAlive', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('refreshes every six hours', async () => {
    const refresh = vi.fn().mockResolvedValue({})
    const stop = keepSessionAlive(refresh)
    await vi.advanceTimersByTimeAsync(REFRESH_INTERVAL_MS - 1)
    expect(refresh).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    await vi.advanceTimersByTimeAsync(REFRESH_INTERVAL_MS)
    expect(refresh).toHaveBeenCalledTimes(2)
    stop()
  })

  it('stops on an untrusted device', async () => {
    const refresh = vi.fn().mockRejectedValue(new ApiError('device_required', 'x', 403))
    keepSessionAlive(refresh)
    await vi.advanceTimersByTimeAsync(REFRESH_INTERVAL_MS * 3)
    expect(refresh).toHaveBeenCalledTimes(1)
  })

  it('keeps trying after other failures', async () => {
    const refresh = vi.fn().mockRejectedValue(new ApiError('unavailable', 'x', 503))
    const stop = keepSessionAlive(refresh)
    await vi.advanceTimersByTimeAsync(REFRESH_INTERVAL_MS * 2)
    expect(refresh).toHaveBeenCalledTimes(2)
    stop()
  })

  it('can be stopped', async () => {
    const refresh = vi.fn().mockResolvedValue({})
    keepSessionAlive(refresh)()
    await vi.advanceTimersByTimeAsync(REFRESH_INTERVAL_MS * 2)
    expect(refresh).not.toHaveBeenCalled()
  })
})
