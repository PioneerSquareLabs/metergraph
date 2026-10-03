export default function UsageCompleteness({ data }) {
  const partial = data.some((value) => value?.completeness?.complete === false)
  return partial ? <div className="banner-warn" role="status">Showing 500 usage groups. Some groups are omitted, so displayed totals are partial. Narrow the time range or filters to include fewer groups.</div> : null
}
