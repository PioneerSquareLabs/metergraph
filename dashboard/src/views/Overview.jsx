import { api, useApi } from '../api.js'
import { fmtInt, fmtPct, fmtTokens, fmtUsd } from '../format.js'
import StatCard from '../components/StatCard.jsx'
import BarChart from '../components/BarChart.jsx'

export default function Overview({ query }) {
  const deps = [query.from, query.to, query.environment]

  const usage = useApi(
    () =>
      api('/v1/usage', {
        group_by: 'model',
        from: query.from,
        to: query.to,
        environment: query.environment,
      }),
    deps,
  )
  const ts = useApi(
    () =>
      api('/v1/usage/timeseries', {
        group_by: 'model',
        bucket: query.bucket,
        from: query.from,
        to: query.to,
        top: 8,
        environment: query.environment,
      }),
    deps,
  )

  const items = usage.data ? usage.data.items : null
  const totals = items
    ? items.reduce(
        (acc, it) => ({
          cost: acc.cost + (it.cost_usd || 0),
          calls: acc.calls + (it.calls || 0),
          tokens: acc.tokens + (it.input_tokens || 0) + (it.output_tokens || 0),
          errors: acc.errors + (it.error_rate || 0) * (it.calls || 0),
        }),
        { cost: 0, calls: 0, tokens: 0, errors: 0 },
      )
    : null
  const errorRate = totals && totals.calls > 0 ? totals.errors / totals.calls : null
  const unpriced = items ? items.filter((it) => (it.unpriced_calls || 0) > 0) : []

  if (usage.error) return <div className="error-banner">Failed to load usage: {usage.error.message}</div>

  return (
    <>
      {unpriced.length > 0 ? (
        <div className="banner-warn">
          <strong>
            {fmtInt(unpriced.reduce((s, it) => s + it.unpriced_calls, 0))} calls could not be priced
          </strong>{' '}
          — update prices.yaml. Affected models:{' '}
          {unpriced.map((it) => `${it.key} (${fmtInt(it.unpriced_calls)})`).join(', ')}
        </div>
      ) : null}

      <div className="metrics">
        <StatCard
          label="Total cost"
          tone="emerald"
          value={usage.loading ? '…' : fmtUsd(totals.cost)}
          detail={`${query.rangeLabel}`}
        />
        <StatCard
          label="LLM calls"
          value={usage.loading ? '…' : fmtInt(totals.calls)}
          detail={items && items.length ? `across ${items.length} models` : ' '}
        />
        <StatCard
          label="Tokens in + out"
          value={usage.loading ? '…' : fmtTokens(totals.tokens)}
          detail="input + output"
        />
        <StatCard
          label="Error rate"
          tone={errorRate != null && errorRate > 0.02 ? 'amber' : 'green'}
          value={usage.loading ? '…' : errorRate == null ? '—' : fmtPct(errorRate, 2)}
          detail="weighted by calls"
        />
      </div>

      <section className="panel">
        <div className="section-heading">
          <h2>Cost over time by model</h2>
          <span className="live-pill">{query.bucket === 'hour' ? 'hourly' : 'daily'} · USD</span>
        </div>
        <div className="panel-body">
          {ts.loading ? <div className="table-loading" /> : null}
          {ts.error ? <div className="error-banner">Failed to load timeseries: {ts.error.message}</div> : null}
          {!ts.loading && !ts.error && ts.data ? (
            ts.data.buckets.length && ts.data.series.length ? (
              <BarChart buckets={ts.data.buckets} series={ts.data.series} />
            ) : (
              <div className="empty">No data yet — point your SDK at this server</div>
            )
          ) : null}
        </div>
      </section>

      {!usage.loading && items && items.length === 0 ? (
        <div className="empty panel">No data yet — point your SDK at this server</div>
      ) : null}
    </>
  )
}
