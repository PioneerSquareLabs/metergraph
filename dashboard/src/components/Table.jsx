/**
 * Generic table.
 * columns: [{ key, label, align?, render?(row) }]
 * rows: array of objects; rowKey(row) -> string
 */
export default function Table({ columns, rows, rowKey, onRowClick, loading, emptyMessage, activeKey }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} style={c.align === 'right' ? { textAlign: 'right' } : undefined}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading
            ? [0, 1, 2, 3].map((i) => (
                <tr key={'loading-' + i}>
                  <td colSpan={columns.length}>
                    <div className="table-loading" />
                  </td>
                </tr>
              ))
            : null}
          {!loading && rows && rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length}>
                <div className="empty">{emptyMessage || 'No data yet — point your SDK at this server'}</div>
              </td>
            </tr>
          ) : null}
          {!loading && rows
            ? rows.map((row) => {
                const key = rowKey(row)
                return (
                  <tr
                    key={key}
                    className={
                      (onRowClick ? 'row-click' : '') + (activeKey && activeKey === key ? ' row-active' : '')
                    }
                    onClick={onRowClick ? () => onRowClick(row) : undefined}
                  >
                    {columns.map((c) => (
                      <td key={c.key} style={c.align === 'right' ? { textAlign: 'right' } : undefined}>
                        {c.render ? c.render(row) : row[c.key]}
                      </td>
                    ))}
                  </tr>
                )
              })
            : null}
        </tbody>
      </table>
    </div>
  )
}
