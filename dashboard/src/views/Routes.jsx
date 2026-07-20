import { api, useApi } from '../api.js'
import { fmtInt, fmtMs, fmtPct, fmtTokens, fmtUsd, routeLabel } from '../format.js'
import Table from '../components/Table.jsx'
import HBarChart from '../components/HBarChart.jsx'

export default function Routes({ query }) {
  const deps = [query.from, query.to, query.environment]
  const usage = useApi(
    () =>
      api('/v1/usage', { group_by: 'route', from: query.from, to: query.to, environment: query.environment }),
    deps,
  )

  if (usage.error) return <div className="error-banner">Failed to load routes: {usage.error.message}</div>

  const barItems = usage.data
    ? usage.data.items.map((r) => ({ ...r, key: routeLabel(r.key).label }))
    : null

  return (
    <>
    <section className="panel">
      <div className="section-heading">
        <h2>Spend by route</h2>
        <span className="live-pill">cost · {query.rangeLabel}</span>
      </div>
      <div className="panel-body">
        {usage.loading ? <div className="table-loading" /> : null}
        {!usage.loading && barItems ? (
          <HBarChart items={barItems} sub={(r) => `${fmtInt(r.calls)} calls`} />
        ) : null}
      </div>
    </section>

    <section className="panel">
      <div className="section-heading">
        <h2>Routes</h2>
        <span className="live-pill">cost desc · {query.rangeLabel}</span>
      </div>
      <Table
        loading={usage.loading}
        rows={usage.data ? usage.data.items : null}
        rowKey={(r) => r.key}
        search={(r) => `${routeLabel(r.key).label} ${r.key}`}
        searchPlaceholder="Search routes…"
        columns={[
          {
            key: 'key',
            label: 'Route',
            sort: (r) => routeLabel(r.key).label,
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
            sort: (r) => (r.input_tokens || 0) + (r.output_tokens || 0),
            render: (r) => `${fmtTokens(r.input_tokens)} / ${fmtTokens(r.output_tokens)}`,
          },
          { key: 'cache_read_tokens', label: 'Cache read', align: 'right', render: (r) => fmtTokens(r.cache_read_tokens) },
          { key: 'p95_latency_ms', label: 'p95 latency', align: 'right', render: (r) => fmtMs(r.p95_latency_ms) },
          { key: 'error_rate', label: 'Errors', align: 'right', render: (r) => fmtPct(r.error_rate) },
        ]}
      />
    </section>
    </>
  )
}
