import { useCallback, useMemo, useRef, useState } from 'react'

/**
 * Generic table with client-side search and click-to-sort.
 * columns: [{ key, label, align?, render?(row), sort? }]
 *   - sort: (row) => primitive   custom sort value
 *   - sort: false                column is not sortable
 *   - sort omitted               sortable by row[key]
 * search: optional (row) => string. When provided, a search box filters rows.
 */
export default function Table({
  columns,
  rows,
  rowKey,
  onRowClick,
  loading,
  emptyMessage,
  activeKey,
  search,
  searchPlaceholder = 'Search…',
}) {
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState({ key: null, dir: 'asc' })

  // Callback ref so the scroll fires once the row actually mounts (it may
  // still be a loading skeleton when activeKey is set). Memoized on
  // activeKey so it doesn't reattach - and rescroll - on unrelated re-renders.
  const scrolledForRef = useRef(null)
  const setActiveRowRef = useCallback(
    (el) => {
      if (el && activeKey && scrolledForRef.current !== activeKey) {
        scrolledForRef.current = activeKey
        el.scrollIntoView({ block: 'center' })
      }
    },
    [activeKey],
  )

  const accessorFor = (c) =>
    typeof c.sort === 'function' ? c.sort : c.sort === false ? null : (row) => row[c.key]

  const view = useMemo(() => {
    if (!rows) return null
    const q = query.trim().toLowerCase()
    let out = q && search ? rows.filter((r) => search(r).toLowerCase().includes(q)) : rows
    if (sort.key) {
      const col = columns.find((c) => c.key === sort.key)
      const acc = col && accessorFor(col)
      if (acc) {
        const dir = sort.dir === 'desc' ? -1 : 1
        out = [...out].sort((a, b) => {
          const av = acc(a)
          const bv = acc(b)
          if (av == null && bv == null) return 0
          if (av == null) return 1
          if (bv == null) return -1
          if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * dir
          return String(av).localeCompare(String(bv)) * dir
        })
      }
    }
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, query, sort, search])

  const toggleSort = (key) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'asc' }))

  return (
    <div className="table-block">
      {search ? (
        <div className="table-toolbar">
          <input
            type="search"
            className="table-search"
            placeholder={searchPlaceholder}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Filter rows"
          />
          {query && view ? <span className="table-count">{view.length} match{view.length === 1 ? '' : 'es'}</span> : null}
        </div>
      ) : null}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              {columns.map((c) => {
                const sortable = accessorFor(c) != null
                const active = sort.key === c.key
                return (
                  <th
                    key={c.key}
                    style={c.align === 'right' ? { textAlign: 'right' } : undefined}
                    aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : undefined}
                  >
                    {sortable ? (
                      <button
                        type="button"
                        className={'th-sort' + (active ? ' active' : '')}
                        onClick={() => toggleSort(c.key)}
                      >
                        {c.label}
                        <span className="th-arrow" aria-hidden="true">
                          {active ? (sort.dir === 'asc' ? '↑' : '↓') : '↕'}
                        </span>
                      </button>
                    ) : (
                      c.label
                    )}
                  </th>
                )
              })}
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
            {!loading && view && view.length === 0 ? (
              <tr>
                <td colSpan={columns.length}>
                  <div className="empty">
                    {query ? `No rows match “${query}”` : emptyMessage || 'No data yet — point your SDK at this server'}
                  </div>
                </td>
              </tr>
            ) : null}
            {!loading && view
              ? view.map((row) => {
                  const key = rowKey(row)
                  const active = activeKey && activeKey === key
                  return (
                    <tr
                      key={key}
                      ref={active ? setActiveRowRef : undefined}
                      className={(onRowClick ? 'row-click' : '') + (active ? ' row-active' : '')}
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
    </div>
  )
}
