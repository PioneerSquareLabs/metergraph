// Dev-only mock backend. Enable with: localStorage.setItem('mg_mock', '1')
// Every /v1 endpoint returns deterministic, realistic fake data so the views
// can be previewed without a running server.

function hashStr(s) {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

function mulberry32(seed) {
  let a = seed
  return function () {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const FUNCS = [
  { func: 'billing.summarize_invoice', module: 'billing', route: 'invoice-summary', model: 'claude-sonnet-4-5', weight: 9 },
  { func: 'support.triage_ticket', module: 'support', route: 'ticket-triage', model: 'gpt-4.1-mini', weight: 7 },
  { func: 'search.rerank_results', module: 'search', route: 'template:9f2c41d7a0b8', model: 'gemini-2.5-flash', weight: 6 },
  { func: 'docs.answer_question', module: 'docs', route: 'docs-qa', model: 'claude-sonnet-4-5', weight: 5 },
  { func: 'onboarding.extract_profile', module: 'onboarding', route: 'profile-extract', model: 'gpt-4.1', weight: 3 },
  { func: 'notifications.draft_email', module: 'notifications', route: 'template:4be09cc217f5', model: 'gpt-4.1-mini', weight: 2.4 },
  { func: 'support.summarize_thread', module: 'support', route: 'thread-summary', model: 'gemini-2.5-flash', weight: 2 },
  { func: 'billing.classify_dispute', module: 'billing', route: 'dispute-classify', model: 'ft:gpt-4.1-mini:acme', weight: 1.2 },
  { func: 'search.expand_query', module: 'search', route: 'query-expand', model: 'gpt-4.1-mini', weight: 0.8 },
  { func: 'internal.eval_harness', module: 'internal', route: 'template:c07d33e18a44', model: 'gpt-4.1', weight: 0.3 },
]

const MODELS = [
  { model: 'claude-sonnet-4-5', provider: 'anthropic', weight: 11, costPerCall: 0.021 },
  { model: 'gpt-4.1', provider: 'openai', weight: 4, costPerCall: 0.017 },
  { model: 'gpt-4.1-mini', provider: 'openai', weight: 10, costPerCall: 0.0031 },
  { model: 'gemini-2.5-flash', provider: 'google', weight: 8, costPerCall: 0.0018 },
  { model: 'ft:gpt-4.1-mini:acme', provider: 'openai', weight: 1.2, costPerCall: 0, unpriced: true },
]

function uniq(list) {
  return [...new Set(list)]
}

function keysFor(groupBy, days) {
  switch (groupBy) {
    case 'func':
      return FUNCS.map((f) => ({ key: f.func, weight: f.weight }))
    case 'module':
      return uniq(FUNCS.map((f) => f.module)).map((m) => ({
        key: m,
        weight: FUNCS.filter((f) => f.module === m).reduce((s, f) => s + f.weight, 0),
      }))
    case 'route':
      return uniq(FUNCS.map((f) => f.route)).map((r) => ({
        key: r,
        weight: FUNCS.filter((f) => f.route === r).reduce((s, f) => s + f.weight, 0),
      }))
    case 'model':
      return MODELS.map((m) => ({ key: m.model, weight: m.weight, meta: m }))
    case 'provider':
      return uniq(MODELS.map((m) => m.provider)).map((p) => ({
        key: p,
        weight: MODELS.filter((m) => m.provider === p).reduce((s, m) => s + m.weight, 0),
      }))
    case 'day': {
      const out = []
      const end = new Date()
      for (let i = days - 1; i >= 0; i--) {
        const d = new Date(end.getTime() - i * 86400000)
        out.push({ key: d.toISOString().slice(0, 10), weight: 30 + 8 * Math.sin(i / 2) })
      }
      return out
    }
    default:
      return []
  }
}

function parseRange(params) {
  const to = params.to ? new Date(params.to) : new Date()
  const from = params.from ? new Date(params.from) : new Date(to.getTime() - 7 * 86400000)
  const ms = Math.max(3600000, to.getTime() - from.getTime())
  return { from, to, days: Math.max(1, Math.round(ms / 86400000)), ms }
}

function envScale(params) {
  const env = (params.environment || '').trim()
  if (!env || env === 'production' || env === 'prod') return 1
  return 0.12
}

function usageItem(key, weight, days, scale, meta) {
  const rng = mulberry32(hashStr(key))
  const calls = Math.max(1, Math.round(weight * 210 * days * (0.8 + rng() * 0.4) * scale))
  const inPerCall = Math.round(700 + rng() * 1600)
  const outPerCall = Math.round(140 + rng() * 460)
  const input_tokens = calls * inPerCall
  const output_tokens = calls * outPerCall
  const cache_read_tokens = Math.round(input_tokens * (0.08 + rng() * 0.42))
  const reasoning_tokens = Math.round(output_tokens * rng() * 0.35)
  const unpriced = meta && meta.unpriced
  const perCall = meta ? meta.costPerCall : 0.0015 + rng() * 0.02
  const cost_usd = unpriced ? 0 : +(calls * perCall * (0.9 + rng() * 0.2)).toFixed(4)
  const avg = Math.round(380 + rng() * 1300)
  return {
    key,
    calls,
    cost_usd,
    input_tokens,
    output_tokens,
    cache_read_tokens,
    reasoning_tokens,
    avg_latency_ms: avg,
    p95_latency_ms: Math.round(avg * (2.2 + rng())),
    error_rate: +(rng() * 0.03).toFixed(4),
    unpriced_calls: unpriced ? Math.round(calls * 0.92) : 0,
  }
}

// Roll up per-key items into coarser buckets so a demo reconciles across views:
// provider totals equal the sum of their models, module/route totals equal the
// sum of their functions. Without this each group_by is generated independently
// and the same window shows different totals depending on the grouping.
function rollup(items, keyOf) {
  const groups = new Map()
  for (const item of items) {
    const key = keyOf(item.key)
    if (!key) continue
    const g = groups.get(key) || {
      key,
      calls: 0,
      cost_usd: 0,
      input_tokens: 0,
      output_tokens: 0,
      cache_read_tokens: 0,
      reasoning_tokens: 0,
      unpriced_calls: 0,
      _latWeighted: 0,
      _p95: 0,
      _errWeighted: 0,
    }
    g.calls += item.calls
    g.cost_usd += item.cost_usd
    g.input_tokens += item.input_tokens
    g.output_tokens += item.output_tokens
    g.cache_read_tokens += item.cache_read_tokens
    g.reasoning_tokens += item.reasoning_tokens
    g.unpriced_calls += item.unpriced_calls
    g._latWeighted += (item.avg_latency_ms || 0) * item.calls
    g._p95 = Math.max(g._p95, item.p95_latency_ms || 0)
    g._errWeighted += (item.error_rate || 0) * item.calls
    groups.set(key, g)
  }
  return [...groups.values()].map((g) => ({
    key: g.key,
    calls: g.calls,
    cost_usd: +g.cost_usd.toFixed(4),
    input_tokens: g.input_tokens,
    output_tokens: g.output_tokens,
    cache_read_tokens: g.cache_read_tokens,
    reasoning_tokens: g.reasoning_tokens,
    avg_latency_ms: g.calls ? Math.round(g._latWeighted / g.calls) : null,
    p95_latency_ms: g._p95 || null,
    error_rate: g.calls ? +(g._errWeighted / g.calls).toFixed(4) : 0,
    unpriced_calls: g.unpriced_calls,
  }))
}

function mockUsage(params) {
  const { days } = parseRange(params)
  const scale = envScale(params)
  const groupBy = params.group_by || 'func'

  // Provider rolls up from models; module/route roll up from functions.
  const rollups = {
    provider: () => {
      const models = keysFor('model', days).map(({ key, weight, meta }) => usageItem(key, weight, days, scale, meta))
      const providerOf = Object.fromEntries(MODELS.map((m) => [m.model, m.provider]))
      return rollup(models, (key) => providerOf[key])
    },
    module: () => {
      const funcs = keysFor('func', days).map(({ key, weight }) => usageItem(key, weight, days, scale))
      const moduleOf = Object.fromEntries(FUNCS.map((f) => [f.func, f.module]))
      return rollup(funcs, (key) => moduleOf[key])
    },
    route: () => {
      const funcs = keysFor('func', days).map(({ key, weight }) => usageItem(key, weight, days, scale))
      const routeOf = Object.fromEntries(FUNCS.map((f) => [f.func, f.route]))
      return rollup(funcs, (key) => routeOf[key])
    },
  }

  const items = rollups[groupBy]
    ? rollups[groupBy]()
    : keysFor(groupBy, days).map(({ key, weight, meta }) => usageItem(key, weight, days, scale, meta))
  items.sort((a, b) => b.cost_usd - a.cost_usd)
  return { items }
}

function bucketsFor(params) {
  const { from, to } = parseRange(params)
  const hourly = params.bucket === 'hour'
  const stepMs = hourly ? 3600000 : 86400000
  const out = []
  const start = new Date(Math.floor(from.getTime() / stepMs) * stepMs)
  for (let t = start.getTime(); t <= to.getTime() && out.length < 120; t += stepMs) {
    const d = new Date(t)
    out.push(hourly ? d.toISOString().slice(0, 13) + ':00:00Z' : d.toISOString().slice(0, 10))
  }
  return out
}

function mockTimeseries(params) {
  const buckets = bucketsFor(params)
  const { days } = parseRange(params)
  const scale = envScale(params)
  const top = Number(params.top) || 8
  let keys = keysFor(params.group_by || 'model', days)
  if (params.func) keys = keys.filter((k) => k.key === params.func)
  if (params.route) keys = keys.filter((k) => k.key === params.route)
  keys.sort((a, b) => b.weight - a.weight)

  const head = keys.slice(0, top)
  const tail = keys.slice(top)
  const perBucket = params.bucket === 'hour' ? 1 / 24 : 1

  const seriesFor = ({ key, weight, meta }) => {
    const rng = mulberry32(hashStr('ts:' + key))
    const perCall = meta ? meta.costPerCall : 0.0015 + rng() * 0.02
    const base = weight * 210 * perBucket * perCall * scale
    return {
      key,
      values: buckets.map((_, i) => {
        const trend = 1 + 0.25 * Math.sin(i / 3 + weight)
        const v = (meta && meta.unpriced ? 0 : base) * trend * (0.6 + rng() * 0.8)
        return +v.toFixed(4)
      }),
    }
  }

  const series = head.map(seriesFor)
  if (tail.length) {
    const rest = tail.map(seriesFor)
    series.push({
      key: 'other',
      values: buckets.map((_, i) => +rest.reduce((s, sr) => s + sr.values[i], 0).toFixed(4)),
    })
  }
  return { buckets, series }
}

function mockCalls(params) {
  const limit = Math.min(Number(params.limit) || 50, 200)
  let pool = FUNCS
  if (params.func) pool = pool.filter((f) => f.func === params.func)
  if (params.route) pool = pool.filter((f) => f.route === params.route)
  if (!pool.length) return { items: [] }

  const before = params.before ? new Date(params.before) : new Date()
  const rng = mulberry32(hashStr('calls:' + (params.func || '') + (params.route || '')))
  const items = []
  let t = before.getTime() - 30000
  for (let i = 0; i < limit; i++) {
    const f = pool[Math.floor(rng() * pool.length)]
    const m = MODELS.find((x) => x.model === f.model) || MODELS[0]
    const input = Math.round(600 + rng() * 2200)
    const output = Math.round(120 + rng() * 600)
    const failed = rng() < 0.02
    items.push({
      ts: new Date(t).toISOString(),
      func: f.func,
      module: f.module,
      route: f.route,
      provider: m.provider,
      model: m.model,
      input_tokens: input,
      output_tokens: failed ? 0 : output,
      cache_read_tokens: Math.round(input * rng() * 0.5),
      reasoning_tokens: Math.round(output * rng() * 0.3),
      cost_usd: m.unpriced ? 0 : +((input * 1.1 + output * 4.4) * (m.costPerCall / 4) * 0.001).toFixed(6),
      cost_status: m.unpriced ? 'unpriced' : 'priced',
      latency_ms: Math.round(300 + rng() * 2400),
      status: failed ? 'error' : 'ok',
      error_type: failed ? (rng() < 0.5 ? 'rate_limit' : 'timeout') : null,
      stream: rng() < 0.4,
      session_id: 'sess_' + Math.floor(rng() * 1e8).toString(16),
      environment: (params.environment || '').trim() || 'production',
    })
    t -= Math.round(20000 + rng() * 900000)
  }
  return { items }
}

function mockCatalog() {
  return {
    version: '2026-07-15',
    models: MODELS.filter((m) => !m.unpriced).map((m) => ({
      canonical_id: m.model,
      publisher: m.provider,
      aliases: [m.model + '-latest'],
      prices: [
        { unit: 'input_tokens_1m', usd: +(m.costPerCall * 180).toFixed(2) },
        { unit: 'output_tokens_1m', usd: +(m.costPerCall * 720).toFixed(2) },
      ],
    })),
  }
}

export function mockApi(path, params = {}) {
  let result
  if (path === '/v1/usage') result = mockUsage(params)
  else if (path === '/v1/usage/timeseries') result = mockTimeseries(params)
  else if (path === '/v1/calls') result = mockCalls(params)
  else if (path === '/v1/catalog') result = mockCatalog()
  else result = { items: [] }
  return new Promise((resolve) => setTimeout(() => resolve(result), 180))
}
