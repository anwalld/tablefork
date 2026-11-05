let owner
export function randomId() {
  return Array.from(crypto.getRandomValues(new Uint8Array(32)), value => value.toString(16).padStart(2, '0')).join('')
}
export function queueOwner() {
  owner ??= randomId()
  return owner
}
export function rotateQueueOwner() { owner = null }
export async function queryTask(id, capability = queueOwner(), signal) {
  const response = await fetch(`/api/tasks/${encodeURIComponent(id)}`, {
    headers: { 'X-Queue-Owner': capability }, cache: 'no-store', signal,
  })
  if (!response.ok) throw new Error('任务查询暂时不可用')
  return response.json()
}
export function taskResult(task) {
  if (['waiting', 'running', 'recovering'].includes(task.state)) return null
  if (task.state === 'expired') return { status: 408, body: { error: { code: 'queue_timeout', message: '排队超时，请重试', retryable: false } } }
  if (task.state === 'unknown') return { status: 503, body: { error: { code: 'result_unknown', message: '执行结果尚无法确认，请勿重复提交', retryable: false } } }
  let body
  try { body = JSON.parse(task.response.body) } catch { body = {} }
  return { ...task.response, body }
}
// Query failures only retry the query. They never re-submit the business call.
export async function waitForTask(id, capability, { deadline = Date.now() + 65000, signal } = {}) {
  while (Date.now() < deadline) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
    let task
    try { task = await queryTask(id, capability, signal) } catch (error) {
      if (signal?.aborted) throw error
    }
    if (task) {
      const result = taskResult(task)
      if (result) return result
    }
    await new Promise(resolve => setTimeout(resolve, 1000))
  }
  return { status: 503, body: { error: { code: 'result_unknown', message: '暂时无法确认执行结果', retryable: false } } }
}
