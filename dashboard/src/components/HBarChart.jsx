import { fmtUsd } from '../format.js'

/** Split on the last `.`/`:` so the trailing (usually most identifying) segment
 * of a dotted/colon path never gets ellipsized away — only the prefix shrinks. */
function splitForTruncation(text) {
  const idx = Math.max(text.lastIndexOf('.'), text.lastIndexOf(':'))
  if (idx === -1 || idx === text.length - 1) return { head: text, tail: '' }
  return { head: text.slice(0, idx + 1), tail: text.slice(idx + 1) }
}

/**
 * Ranked horizontal "spend leaderboard": one measure (cost) across categories.
 * Single hue by design — magnitude is carried by bar length and the direct
 * value label, so color never encodes rank. Rows past `max` fold into "other".
 * Rows call `onSelect(key)` on click when provided (the folded "other" row
 * has no single key to select, so it's never clickable). `key` stays the raw
 * identifier passed in `items` throughout (so callers can match it against a
 * table row); `labelFor` only transforms what's *displayed*.
 */
export default function HBarChart({
  items,
  max = 8,
  color = 'var(--emerald)',
  unit = fmtUsd,
  sub,
  onSelect,
  labelFor = (key) => key,
}) {
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
      {display.map((r) => {
        const clickable = !r.other && typeof onSelect === 'function'
        const label = labelFor(r.key)
        const { head: keyHead, tail: keyTail } = splitForTruncation(label)
        const Row = clickable ? 'button' : 'div'
        return (
          <Row
            type={clickable ? 'button' : undefined}
            className={'hbar-row' + (clickable ? ' hbar-row-click' : '')}
            key={r.key}
            data-tooltip={`${label} — ${unit(r.value)}`}
            onClick={clickable ? () => onSelect(r.key) : undefined}
          >
            <span className="hbar-key">
              <span className="hbar-key-head">{keyHead}</span>
              <span className="hbar-key-tail">{keyTail}</span>
            </span>
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
          </Row>
        )
      })}
    </div>
  )
}
