import { useState } from 'react'
import { api, useApi } from '../api.js'
import { fmtInt, fmtMs, fmtPct, fmtTokens, fmtTs, fmtUsd } from '../format.js'
import Table from '../components/Table.jsx'
import Sparkline from '../components/Sparkline.jsx'
import BarChart from '../components/BarChart.jsx'
import HBarChart from '../components/HBarChart.jsx'

function FunctionDetail({ func, query }) {
  const deps = [func, query.from, query.to, query.environment]
  const ts = useApi(
    () =>
      api('/v1/usage/timeseries', {
        group_by: 'func',
        func,
        bucket: query.bucket,
        from: query.from,
        to: query.to,
        environment: query.environment,
      }),
    deps,
  )
  const calls = useApi(() => api('/v1/calls', { limit: 20, func, environment: query.environment }), deps)

  return (
    <>
      <div className="panel-body">
        {ts.loading ? <div className="table-loading" /> : null}
        {ts.error ? <div className="error-banner">Failed to load timeseries: {ts.error.message}</div> : null}
        {!ts.loading && !ts.error && ts.data ? (
          ts.data.buckets.length && ts.data.series.length ? (
            <BarChart buckets={ts.data.buckets} series={ts.data.series} height={150} />
          ) : (
            <div className="empty">No data yet — point your SDK at this server</div>
          )
        ) : null}
      </div>
      <div className="section-heading sub">
        <h2>Recent calls</h2>
      </div>
      <Table
        loading={calls.loading}
        rows={calls.data ? calls.data.items : null}
        rowKey={(c) => c.ts + c.session_id}
        emptyMessage="No calls recorded for this function in the selected range"
        search={(c) => `${c.model || ''} ${c.status || ''} ${c.error_type || ''}`}
        searchPlaceholder="Search calls…"
        columns={[
          { key: 'ts', label: 'Time', sort: (c) => c.ts, render: (c) => fmtTs(c.ts) },
          { key: 'model', label: 'Model', sort: (c) => c.model, render: (c) => <span className="mono">{c.model}</span> },
          {
            key: 'tokens',
            label: 'Tokens in / out',
            align: 'right',
            sort: (c) => (c.input_tokens || 0) + (c.output_tokens || 0),
            render: (c) => `${fmtTokens(c.input_tokens)} / ${fmtTokens(c.output_tokens)}`,
          },
          {
            key: 'cost_usd',
            label: 'Cost',
            align: 'right',
            sort: (c) => c.cost_usd || 0,
            render: (c) =>
              c.cost_status === 'unpriced' ? <span className="muted">unpriced</span> : fmtUsd(c.cost_usd),
          },
          { key: 'latency_ms', label: 'Latency', align: 'right', render: (c) => fmtMs(c.latency_ms) },
          {
            key: 'status',
            label: 'Status',
            sort: (c) => (c.error ? 1 : 0),
            render: (c) =>
              c.error ? (
                <span className="pill err">{c.error_type || 'error'}</span>
              ) : (
                <span className="pill ok">{c.status || 'ok'}</span>
              ),
          },
        ]}
      />
      {calls.error ? <div className="error-banner">Failed to load calls: {calls.error.message}</div> : null}
    </>
  )
}

export default function Functions({ query }) {
  const deps = [query.from, query.to, query.environment]
  const [selected, setSelected] = useState(null)

  const usage = useApi(
    () =>
      api('/v1/usage', { group_by: 'func', from: query.from, to: query.to, environment: query.environment }),
    deps,
  )
  const ts = useApi(
    () =>
      api('/v1/usage/timeseries', {
        group_by: 'func',
        bucket: query.bucket,
        from: query.from,
        to: query.to,
        top: 8,
        environment: query.environment,
      }),
    deps,
  )

  const sparkMap = {}
  if (ts.data) for (const sr of ts.data.series) sparkMap[sr.key] = sr.values

  if (usage.error) return <div className="error-banner">Failed to load functions: {usage.error.message}</div>

  return (
    <>
      <section className="panel">
        <div className="section-heading">
          <h2>Spend by function</h2>
          <span className="live-pill">cost · {query.rangeLabel}</span>
        </div>
        <div className="panel-body">
          {usage.loading ? <div className="table-loading" /> : null}
          {!usage.loading && usage.data ? (
            <HBarChart items={usage.data.items} sub={(r) => `${fmtInt(r.calls)} calls`} />
          ) : null}
        </div>
      </section>

      <section className="panel">
        <div className="section-heading">
          <h2>Functions</h2>
          <span className="live-pill">cost desc · {query.rangeLabel}</span>
        </div>
        <Table
          loading={usage.loading}
          rows={usage.data ? usage.data.items : null}
          rowKey={(r) => r.key}
          activeKey={selected}
          onRowClick={(r) => setSelected(selected === r.key ? null : r.key)}
          search={(r) => r.key}
          searchPlaceholder="Search functions…"
          columns={[
            { key: 'key', label: 'Function', sort: (r) => r.key, render: (r) => <strong>{r.key}</strong> },
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
            {
              key: 'spark',
              label: 'Cost trend',
              sort: false,
              render: (r) => <Sparkline values={sparkMap[r.key]} />,
            },
          ]}
        />
      </section>

      {selected ? (
        <section className="panel detail-card">
          <div className="section-heading">
            <h2>
              <span className="route-icon">ƒ</span>
              {selected}
            </h2>
            <button type="button" className="close-button" onClick={() => setSelected(null)}>
              Close ✕
            </button>
          </div>
          <FunctionDetail func={selected} query={query} />
        </section>
      ) : null}
    </>
  )
}
