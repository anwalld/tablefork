import { ApiError } from './api.js'

export const REFRESH_INTERVAL_MS = 6 * 60 * 60 * 1000

// Keeps the session alive on a trusted device by refreshing it every few
// hours. Other devices get 403 and the timer stops; other failures are left
// to the next round. Returns a function that stops the timer.
export function keepSessionAlive(refresh, intervalMs = REFRESH_INTERVAL_MS) {
  let timer = null
  const stop = () => clearInterval(timer)
  timer = setInterval(async () => {
    try {
      await refresh()
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) stop()
    }
  }, intervalMs)
  return stop
}
