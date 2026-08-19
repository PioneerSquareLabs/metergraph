import { environmentKey } from '../environment-selection.js'

export default function EnvironmentFilter({ items, selected, loading, error, onChange }) {
  const options = items.map((item) => ({ ...item, key: environmentKey(item.value) }))
  const selectedCount = selected === null
    ? options.length
    : options.filter((option) => selected.has(option.key)).length
  const summary = loading
    ? 'Loading…'
    : error
      ? 'Unavailable'
      : options.length === 0
        ? 'No traffic'
      : selectedCount === options.length
        ? 'All environments'
        : selectedCount === 0
          ? 'No environments'
          : `${selectedCount} of ${options.length}`

  function toggle(key) {
    const next = selected === null
      ? new Set(options.map((option) => option.key))
      : new Set(selected)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    onChange(next)
  }

  return (
    <details className="environment-select">
      <summary aria-label={`Environment filter: ${summary}`}>{summary}</summary>
      <div className="environment-menu">
        <div className="environment-actions">
          <button type="button" onClick={() => onChange(null)}>Select all</button>
          <button type="button" onClick={() => onChange(new Set())}>Clear all</button>
        </div>
        {error ? <p className="environment-message">Could not load environments.</p> : null}
        {!loading && !error && options.length === 0 ? (
          <p className="environment-message">No traffic in this range.</p>
        ) : null}
        {options.map((option) => (
          <label key={option.key} className="environment-option">
            <input
              type="checkbox"
              checked={selected === null || selected.has(option.key)}
              onChange={() => toggle(option.key)}
            />
            <span>{option.value === null ? 'Untagged' : option.value}</span>
            <small>{option.calls}</small>
          </label>
        ))}
      </div>
    </details>
  )
}
