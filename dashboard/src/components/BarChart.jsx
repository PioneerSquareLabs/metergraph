import { fmtBucket, fmtUsd } from '../format.js'

export const SERIES_COLORS = [
  '#c9ff55',
  '#75e292',
  '#9fcce0',
  '#ffd06e',
  '#f1a392',
  '#c9a7e8',
  '#e8ebe2',
  '#8f978a',
]

export function seriesColor(index, key) {
  if (key === 'other') return '#4c5149'
  return SERIES_COLORS[index % SERIES_COLORS.length]
}

/** Stacked bar chart of cost per bucket. series: [{ key, values }] */
export default function BarChart({ buckets, series, height = 190 }) {
  const n = buckets ? buckets.length : 0
  if (!n || !series || !series.length) return null

  const totals = buckets.map((_, i) => series.reduce((s, sr) => s + (sr.values[i] || 0), 0))
  const max = Math.max(...totals, 1e-9)

  const W = 760
  const padTop = 14
  const padBottom = 20
  const chartH = height - padTop - padBottom
  const step = W / n
  const barW = Math.max(2, Math.min(38, step * 0.68))

  // ~6 evenly spaced x labels
  const labelEvery = Math.max(1, Math.ceil(n / 6))

  return (
    <div className="barchart">
      <svg viewBox={`0 0 ${W} ${height}`} preserveAspectRatio="none" className="barchart-svg" role="img">
        <line x1="0" y1={padTop} x2={W} y2={padTop} className="gridline" />
        <line x1="0" y1={padTop + chartH / 2} x2={W} y2={padTop + chartH / 2} className="gridline" />
        <line x1="0" y1={padTop + chartH} x2={W} y2={padTop + chartH} className="baseline" />
        {buckets.map((bucket, i) => {
          const x = i * step + (step - barW) / 2
          let y = padTop + chartH
          return (
            <g key={bucket}>
              <title>{`${fmtBucket(bucket)} — ${fmtUsd(totals[i])}`}</title>
              {series.map((sr, si) => {
                const v = sr.values[i] || 0
                if (v <= 0) return null
                const h = (v / max) * chartH
                y -= h
                return (
                  <rect
                    key={sr.key}
                    x={x.toFixed(2)}
                    y={y.toFixed(2)}
                    width={barW.toFixed(2)}
                    height={Math.max(h, 0.5).toFixed(2)}
                    fill={seriesColor(si, sr.key)}
                  />
                )
              })}
            </g>
          )
        })}
      </svg>
      <div className="barchart-scale">
        <span>{fmtUsd(max)}</span>
        <span>{fmtUsd(0)}</span>
      </div>
      <div className="barchart-xlabels">
        {buckets.map((bucket, i) =>
          i % labelEvery === 0 ? (
            <span key={bucket} style={{ left: `${((i + 0.5) / n) * 100}%` }}>
              {fmtBucket(bucket)}
            </span>
          ) : null,
        )}
      </div>
      <div className="legend">
        {series.map((sr, si) => (
          <span key={sr.key} className="legend-item">
            <i className="swatch" style={{ background: seriesColor(si, sr.key) }} />
            {sr.key}
          </span>
        ))}
      </div>
    </div>
  )
}
