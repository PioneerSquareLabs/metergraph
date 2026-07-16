/** $0.0123 under $1, $12.34 above. */
export function fmtUsd(value) {
  if (value == null || Number.isNaN(value)) return '—'
  return '$' + (Math.abs(value) < 1 ? value.toFixed(4) : value.toFixed(2))
}

function trimFixed(n, digits = 1) {
  return n.toFixed(digits).replace(/\.0+$/, '')
}

/** Token counts with k/M suffixes: 1.2k, 34M. */
export function fmtTokens(n) {
  if (n == null || Number.isNaN(n)) return '—'
  if (n >= 1e6) return trimFixed(n / 1e6) + 'M'
  if (n >= 1e3) return trimFixed(n / 1e3) + 'k'
  return String(Math.round(n))
}

export function fmtInt(n) {
  if (n == null || Number.isNaN(n)) return '—'
  return Math.round(n).toLocaleString('en-US')
}

export function fmtPct(ratio, digits = 1) {
  if (ratio == null || Number.isNaN(ratio)) return '—'
  return (ratio * 100).toFixed(digits) + '%'
}

export function fmtMs(ms) {
  if (ms == null || Number.isNaN(ms)) return '—'
  if (ms >= 1000) return (ms / 1000).toFixed(2) + 's'
  return Math.round(ms) + 'ms'
}

/** "2026-07-14" or an ISO timestamp -> "Jul 14" (with hour for hourly buckets). */
export function fmtBucket(key) {
  const d = new Date(key.length <= 10 ? key + 'T00:00:00' : key)
  if (Number.isNaN(d.getTime())) return key
  const day = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  if (key.length > 10) {
    return day + ' ' + d.toLocaleTimeString('en-US', { hour: 'numeric', hour12: true })
  }
  return day
}

/** ISO timestamp -> "Jul 14, 3:42 PM" */
export function fmtTs(iso) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  })
}

/** Route keys like "template:9f2c41d7a0b8" -> { unnamed: true, label } */
export function routeLabel(key) {
  if (key && key.startsWith('template:')) {
    const hash = key.slice('template:'.length)
    const short = hash.length > 8 ? hash.slice(0, 8) + '…' : hash
    return { unnamed: true, label: `unnamed route (template:${short})` }
  }
  return { unnamed: false, label: key }
}
