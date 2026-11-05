// A failed query/receipt must never imply that an uncertain write did not run.
export function requestErrorText(error, fallback) {
  if (error?.code === 'queue_timeout') return '排队超时，请重试'
  if (error?.code === 'result_unknown') return '执行结果尚无法确认，请勿重复提交'
  return fallback
}
