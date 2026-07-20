export default function Sparkline({ values, width = 120, height = 28, stroke = 'var(--emerald)' }) {
  if (!values || values.length < 2 || values.every((v) => !v)) {
    return <span className="spark-none">—</span>
  }
  const max = Math.max(...values, 1e-9)
  const pts = values.map((v, i) => {
    const x = 1 + (i / (values.length - 1)) * (width - 2)
    const y = height - 2 - (Math.max(0, v) / max) * (height - 6)
    return [x, y]
  })
  const line = pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const area =
    `M${pts[0][0].toFixed(1)},${height - 1} ` +
    pts.map(([x, y]) => `L${x.toFixed(1)},${y.toFixed(1)}`).join(' ') +
    ` L${pts[pts.length - 1][0].toFixed(1)},${height - 1} Z`
  return (
    <svg className="spark" width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <path d={area} fill={stroke} opacity="0.12" />
      <polyline points={line} fill="none" stroke={stroke} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  )
}
