export default function StatCard({ label, value, detail, tone }) {
  return (
    <div className={'metric' + (tone ? ' ' + tone : '')}>
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      {detail ? <div className="metric-detail">{detail}</div> : null}
    </div>
  )
}
