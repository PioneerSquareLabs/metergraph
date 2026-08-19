const FINISH_REASONS = new Set([
  'stop',
  'length',
  'content-filter',
  'tool-calls',
  'other',
])

export function callHealth(call) {
  if (['unset', 'ok', 'error'].includes(call.status_code)) return call.status_code
  return call.error || call.error_type || call.status === 'error' ? 'error' : 'unset'
}

export function callFinishReason(call) {
  if (call.finish_reason) return call.finish_reason
  const legacy = String(call.status || '').toLowerCase().replaceAll('_', '-')
  return FINISH_REASONS.has(legacy) ? legacy : null
}
