import { api, useApi } from '../api.js'
import { fmtInt, fmtMs, fmtPct, fmtTokens, fmtUsd, routeLabel } from '../format.js'
import Table from '../components/Table.jsx'

export default function Routes({ query }) {
  const deps = [query.from, query.to, query.environment]
  const usage = useApi(
    () =>
      api('/v1/usage', { group_by: 'route', from: query.from, to: query.to, environment: query.environment }),
    deps,
  )

  if (usage.error) return <div className="error-banner">Failed to load routes: {usage.error.message}</div>

  return (
    <section className="panel">
      <div className="section-heading">
        <h2>Routes</h2>
        <span className="live-pill">cost desc · {query.rangeLabel}</span>
      </div>
      <Table
        loading={usage.loading}
        rows={usage.data ? usage.data.items : null}
        rowKey={(r) => r.key}
        columns={[
          {
            key: 'key',
            label: 'Route',
            render: (r) => {
              const { unnamed, label } = routeLabel(r.key)
              return unnamed ? <span className="muted">{label}</span> : <strong>{label}</strong>
            },
          },
          { key: 'calls', label: 'Calls', align: 'right', render: (r) => fmtInt(r.calls) },
          { key: 'cost_usd', label: 'Cost', align: 'right', render: (r) => fmtUsd(r.cost_usd) },
          {
            key: 'tokens',
            label: 'Tokens in / out',
            align: 'right',
            render: (r) => `${fmtTokens(r.input_tokens)} / ${fmtTokens(r.output_tokens)}`,
          },
          { key: 'cache_read_tokens', label: 'Cache read', align: 'right', render: (r) => fmtTokens(r.cache_read_tokens) },
          { key: 'p95_latency_ms', label: 'p95 latency', align: 'right', render: (r) => fmtMs(r.p95_latency_ms) },
          { key: 'error_rate', label: 'Errors', align: 'right', render: (r) => fmtPct(r.error_rate) },
        ]}
      />
    </section>
  )
}
