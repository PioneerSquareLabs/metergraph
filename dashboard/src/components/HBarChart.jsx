import { fmtUsd } from '../format.js'

/**
 * Ranked horizontal "spend leaderboard": one measure (cost) across categories.
 * Single hue by design — magnitude is carried by bar length and the direct
 * value label, so color never encodes rank. Rows past `max` fold into "other".
 */
export default function HBarChart({ items, max = 8, color = 'var(--emerald)', unit = fmtUsd, sub }) {
  const rows = (items || [])
    .map((it) => ({ key: it.key, value: Math.max(0, it.cost_usd || 0), raw: it }))
    .filter((r) => r.value > 0)
    .sort((a, b) => b.value - a.value)

  if (!rows.length) return <div className="empty">No spend in range</div>

  const head = rows.slice(0, max)
  const tail = rows.slice(max)
  const display = [...head]
  if (tail.length) {
    display.push({
      key: `other (${tail.length})`,
      value: tail.reduce((s, r) => s + r.value, 0),
      raw: null,
      other: true,
    })
  }
  const peak = Math.max(...display.map((r) => r.value), 1e-9)

  return (
    <div className="hbar">
      {display.map((r) => (
        <div className="hbar-row" key={r.key} title={`${r.key} — ${unit(r.value)}`}>
          <span className="hbar-key">{r.key}</span>
          <span className="hbar-track">
            <span
              className="hbar-fill"
              style={{ width: `${(r.value / peak) * 100}%`, background: r.other ? 'var(--ink-45)' : color }}
            />
          </span>
          <span className="hbar-val">
            {unit(r.value)}
            {sub && r.raw ? <small>{sub(r.raw)}</small> : null}
          </span>
        </div>
      ))}
    </div>
  )
}
