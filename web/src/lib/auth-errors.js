import { ApiError } from './api.js'
import { requestErrorText } from './request-errors.js'

const messages = {
  invalid_credentials: '用户名或密码错误',
  invalid_answer: '用户名或安全问题答案错误',
  username_taken: '用户名已被占用',
  password_mismatch: '两次输入的密码不一致',
  invalid_argument: '用户名需 3 到 32 个字符，密码至少 10 个字符',
  // Only reached when a limit error carries no retry_after.
  too_many_attempts: '尝试次数过多，请稍后再试',
  rate_limited: '尝试次数过多，请稍后再试',
}

// What the login or registration form shows for an error.
export function authErrorText(error) {
  if (!(error instanceof ApiError)) return '服务暂时不可用，请稍后再试'
  if (error.code === 'weak_password') {
    return error.message.includes('username') ? '密码不能与用户名相同' : '这个密码太常见，请换一个'
  }
  return requestErrorText(error, messages[error.code] ?? '服务暂时不可用，请稍后再试')
}

// Whether the error asks the user to wait before trying again.
export function waitSeconds(error) {
  const limited = error?.code === 'too_many_attempts' || error?.code === 'rate_limited'
  return limited && error.retryAfter > 0 ? error.retryAfter : 0
}

export function waitText(seconds) {
  return `尝试次数过多，请 ${seconds} 秒后再试`
}

// Calls onTick with the seconds left, now and once a second until 0. Returns a
// function that stops it early.
export function countdown(seconds, onTick) {
  let left = seconds
  onTick(left)
  const timer = setInterval(() => {
    left -= 1
    onTick(left)
    if (left <= 0) clearInterval(timer)
  }, 1000)
  return () => clearInterval(timer)
}
