import { useEffect, useState } from 'react'
import { api, useApi } from '../api.js'
import { fmtInt, fmtPct, fmtTokens, fmtUsd } from '../format.js'
import { clearHashSelection, consumeHashSelection } from '../hash.js'
import Table from '../components/Table.jsx'
import DonutChart from '../components/DonutChart.jsx'
import HBarChart from '../components/HBarChart.jsx'

function cacheHitRatio(r) {
  const denom = (r.input_tokens || 0) + (r.cache_read_tokens || 0)
  return denom > 0 ? (r.cache_read_tokens || 0) / denom : null
}

function reasoningShare(r) {
  return r.output_tokens > 0 ? (r.reasoning_tokens || 0) / r.output_tokens : null
}

export default function Models({ query }) {
  const deps = [query.from, query.to, query.environment]
  const [selected, setSelected] = useState(consumeHashSelection)
  useEffect(() => clearHashSelection(), [])
  const usage = useApi(
    () =>
      api('/v1/usage', { group_by: 'model', from: query.from, to: query.to, environment: query.environment }),
    deps,
  )
  const providers = useApi(
    () =>
      api('/v1/usage', { group_by: 'provider', from: query.from, to: query.to, environment: query.environment }),
    deps,
  )
  const catalog = useApi(() => api('/v1/catalog'), [])

  // fallback when the usage row lacks a provider: match catalog aliases
  const providerFor = (key) => {
    if (!catalog.data) return null
    const wanted = String(key).toLowerCase()
    for (const m of catalog.data.models || []) {
      if (m.canonical_id === wanted) return m.publisher
      if ((m.aliases || []).some((a) => a.alias === wanted)) return m.publisher
    }
    return null
  }

  if (usage.error) return <div className="error-banner">Failed to load models: {usage.error.message}</div>

  return (
    <>
    <div className="chart-grid two">
      <section className="panel">
        <div className="section-heading">
          <h2>Spend by provider</h2>
          <span className="live-pill">share · {query.rangeLabel}</span>
        </div>
        <div className="panel-body">
          {providers.loading ? <div className="table-loading" /> : null}
          {providers.error ? (
            <div className="error-banner">Failed to load providers: {providers.error.message}</div>
          ) : null}
          {!providers.loading && !providers.error && providers.data ? (
            <DonutChart items={providers.data.items} centerLabel="all providers" />
          ) : null}
        </div>
      </section>
      <section className="panel">
        <div className="section-heading">
          <h2>Spend by model</h2>
          <span className="live-pill">cost · {query.rangeLabel}</span>
        </div>
        <div className="panel-body">
          {usage.loading ? <div className="table-loading" /> : null}
          {!usage.loading && usage.data ? (
            <HBarChart
              items={usage.data.items}
              sub={(r) => `${fmtInt(r.calls)} calls`}
              onSelect={(key) => setSelected((s) => (s === key ? null : key))}
            />
          ) : null}
        </div>
      </section>
    </div>

    <section className="panel">
      <div className="section-heading">
        <h2>Models</h2>
        <span className="live-pill">
          {catalog.data ? `catalog ${catalog.data.version}` : 'catalog …'} · {query.rangeLabel}
        </span>
      </div>
      <Table
        loading={usage.loading}
        rows={usage.data ? usage.data.items : null}
        rowKey={(r) => r.key}
        activeKey={selected}
        onRowClick={(r) => setSelected((s) => (s === r.key ? null : r.key))}
        search={(r) => `${r.key} ${(r.provider !== '(unknown)' && r.provider) || providerFor(r.key) || ''}`}
        searchPlaceholder="Search models…"
        columns={[
          { key: 'key', label: 'Model', sort: (r) => r.key, render: (r) => <strong className="mono">{r.key}</strong> },
          {
            key: 'provider',
            label: 'Provider',
            sort: (r) => (r.provider !== '(unknown)' && r.provider) || providerFor(r.key) || '',
            render: (r) =>
              (r.provider !== '(unknown)' && r.provider) ||
              providerFor(r.key) || <span className="muted">unknown</span>,
          },
          { key: 'calls', label: 'Calls', align: 'right', render: (r) => fmtInt(r.calls) },
          {
            key: 'cost_usd',
            label: 'Cost',
            align: 'right',
            sort: (r) => r.cost_usd || 0,
            render: (r) =>
              (r.unpriced_calls || 0) > 0 ? (
                <span className="pill warn" title={`${fmtInt(r.unpriced_calls)} unpriced calls`}>
                  {fmtUsd(r.cost_usd)} · {fmtInt(r.unpriced_calls)} unpriced
                </span>
              ) : (
                fmtUsd(r.cost_usd)
              ),
          },
          {
            key: 'tokens',
            label: 'Tokens in / out',
            align: 'right',
            sort: (r) => (r.input_tokens || 0) + (r.output_tokens || 0),
            render: (r) => `${fmtTokens(r.input_tokens)} / ${fmtTokens(r.output_tokens)}`,
          },
          { key: 'cache_read_tokens', label: 'Cache read', align: 'right', sort: (r) => r.cache_read_tokens || 0, render: (r) => fmtTokens(r.cache_read_tokens) },
          { key: 'cache', label: 'Cache-hit ratio', align: 'right', sort: (r) => cacheHitRatio(r) ?? -1, render: (r) => fmtPct(cacheHitRatio(r)) },
          { key: 'reasoning', label: 'Reasoning share', align: 'right', sort: (r) => reasoningShare(r) ?? -1, render: (r) => fmtPct(reasoningShare(r)) },
        ]}
      />
    </section>
    </>
  )
}
