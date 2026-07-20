import { fmtPct, fmtUsd } from '../format.js'
import { seriesColor } from './palette.js'

/**
 * Spend-composition donut. Ranks `items` by cost, folds everything past
 * `max` slices into a neutral "other", and renders the total in the center.
 * Each slice carries a hover tooltip and appears in the labelled legend, so
 * identity is never color-alone.
 */
export default function DonutChart({ items, max = 6, centerLabel = 'total', size = 168 }) {
  const rows = (items || [])
    .map((it) => ({ key: it.key, value: Math.max(0, it.cost_usd || 0) }))
    .filter((r) => r.value > 0)
    .sort((a, b) => b.value - a.value)

  const total = rows.reduce((s, r) => s + r.value, 0)
  if (!rows.length || total <= 0) {
    return <div className="empty donut-empty">No spend in range</div>
  }

  const head = rows.slice(0, max)
  const tail = rows.slice(max)
  const slices = [...head]
  if (tail.length) slices.push({ key: 'other', value: tail.reduce((s, r) => s + r.value, 0) })

  const stroke = 26
  const r = (size - stroke) / 2
  const cx = size / 2
  const c = 2 * Math.PI * r
  const gap = slices.length > 1 ? 2 : 0 // 2px surface gap between arcs

  let offset = 0
  const arcs = slices.map((s, i) => {
    const frac = s.value / total
    const len = Math.max(frac * c - gap, 0.5)
    const arc = {
      key: s.key,
      value: s.value,
      frac,
      color: seriesColor(i, s.key),
      dash: `${len} ${c - len}`,
      dashoffset: -offset,
    }
    offset += frac * c
    return arc
  })

  return (
    <div className="donut">
      <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} className="donut-svg" role="img">
        <circle cx={cx} cy={cx} r={r} fill="none" stroke="var(--ink-08)" strokeWidth={stroke} />
        <g transform={`rotate(-90 ${cx} ${cx})`}>
          {arcs.map((a) => (
            <circle
              key={a.key}
              cx={cx}
              cy={cx}
              r={r}
              fill="none"
              stroke={a.color}
              strokeWidth={stroke}
              strokeDasharray={a.dash}
              strokeDashoffset={a.dashoffset}
            >
              <title>{`${a.key} — ${fmtUsd(a.value)} (${fmtPct(a.frac)})`}</title>
            </circle>
          ))}
        </g>
        <text x={cx} y={cx - 4} textAnchor="middle" className="donut-total">
          {fmtUsd(total)}
        </text>
        <text x={cx} y={cx + 14} textAnchor="middle" className="donut-caption">
          {centerLabel}
        </text>
      </svg>
      <ul className="donut-legend">
        {arcs.map((a) => (
          <li key={a.key}>
            <i className="swatch" style={{ background: a.color }} />
            <span className="donut-legend-key" title={a.key}>{a.key}</span>
            <span className="donut-legend-val">{fmtUsd(a.value)}</span>
            <span className="donut-legend-pct">{fmtPct(a.frac)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
