import { useEffect, useMemo, useState } from 'react'
import { getToken, isMock } from './api.js'
import TokenGate from './components/TokenGate.jsx'
import Overview from './views/Overview.jsx'
import Functions from './views/Functions.jsx'
import Routes from './views/Routes.jsx'
import Models from './views/Models.jsx'

const TABS = [
  { id: 'overview', label: 'Overview', View: Overview },
  { id: 'functions', label: 'Functions', View: Functions },
  { id: 'routes', label: 'Routes', View: Routes },
  { id: 'models', label: 'Models', View: Models },
]

const PRESETS = [
  { id: '24h', label: '24h', hours: 24 },
  { id: '7d', label: '7d', hours: 24 * 7 },
  { id: '30d', label: '30d', hours: 24 * 30 },
  { id: 'custom', label: 'Custom' },
]

function currentRoute() {
  const id = window.location.hash.replace(/^#\/?/, '').split('?')[0]
  return TABS.some((t) => t.id === id) ? id : 'overview'
}

function isoDate(d) {
  return d.toISOString().slice(0, 10)
}

export default function App() {
  const [route, setRoute] = useState(currentRoute)
  const [authed, setAuthed] = useState(() => isMock() || !!getToken())
  const [authError, setAuthError] = useState('')
  const [preset, setPreset] = useState('7d')
  const [customFrom, setCustomFrom] = useState(() => isoDate(new Date(Date.now() - 7 * 86400000)))
  const [customTo, setCustomTo] = useState(() => isoDate(new Date()))
  const [envInput, setEnvInput] = useState('')
  const [environment, setEnvironment] = useState('')
  const [excludeDemo, setExcludeDemo] = useState(false)

  useEffect(() => {
    const onHash = () => setRoute(currentRoute())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  useEffect(() => {
    const onUnauthorized = (e) => {
      setAuthed(false)
      setAuthError(e.detail || 'Token rejected (401) — enter a valid API token.')
    }
    window.addEventListener('mg:unauthorized', onUnauthorized)
    return () => window.removeEventListener('mg:unauthorized', onUnauthorized)
  }, [])

  const query = useMemo(() => {
    const now = new Date()
    let from
    let to = now
    let rangeLabel
    let bucket = 'day'
    if (preset === 'custom') {
      from = new Date(customFrom + 'T00:00:00')
      to = new Date(customTo + 'T23:59:59.999')
      if (Number.isNaN(from.getTime())) from = new Date(now.getTime() - 7 * 86400000)
      if (Number.isNaN(to.getTime())) to = now
      rangeLabel = `${customFrom} → ${customTo}`
      if (to.getTime() - from.getTime() <= 48 * 3600000) bucket = 'hour'
    } else {
      const hours = (PRESETS.find((p) => p.id === preset) || PRESETS[1]).hours
      from = new Date(now.getTime() - hours * 3600000)
      rangeLabel = `last ${preset}`
      if (hours <= 48) bucket = 'hour'
    }
    return {
      from: from.toISOString(),
      to: to.toISOString(),
      environment,
      excludeEnvironment: excludeDemo ? 'demo' : '',
      bucket,
      rangeLabel,
    }
  }, [preset, customFrom, customTo, environment, excludeDemo])

  if (!authed) {
    return (
      <TokenGate
        error={authError}
        onDone={() => {
          setAuthError('')
          setAuthed(true)
        }}
      />
    )
  }

  const active = TABS.find((t) => t.id === route) || TABS[0]
  const View = active.View

  return (
    <div className="shell">
      <header className="topbar">
        <a className="wordmark" href="#/overview" aria-label="Metergraph home">
          <b><span>meter</span><span className="wm-graph">graph</span><span className="wm-spike" aria-hidden="true">&#10033;</span></b>
          <small>OSS</small>
        </a>
        <nav className="tabs">
          {TABS.map((t) => (
            <a key={t.id} href={'#/' + t.id} className={t.id === route ? 'active' : ''}>
              {t.label}
            </a>
          ))}
        </nav>
        <div className="controls">
          <div className="control">
            <label>Range</label>
            <div className="preset-group">
              {PRESETS.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  className={preset === p.id ? 'active' : ''}
                  onClick={() => setPreset(p.id)}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
          {preset === 'custom' ? (
            <>
              <div className="control">
                <label>From</label>
                <input type="date" value={customFrom} onChange={(e) => setCustomFrom(e.target.value)} />
              </div>
              <div className="control">
                <label>To</label>
                <input type="date" value={customTo} onChange={(e) => setCustomTo(e.target.value)} />
              </div>
            </>
          ) : null}
          <div className="control">
            <label>Environment</label>
            <form
              className="environment-filter"
              onSubmit={(e) => {
                e.preventDefault()
                setEnvironment(envInput.trim())
                setExcludeDemo(false)
              }}
            >
              <input
                aria-label="Environment name"
                type="text"
                placeholder="all"
                value={envInput}
                onChange={(e) => setEnvInput(e.target.value)}
              />
              <button type="submit">Apply</button>
            </form>
            <label className="checkbox-filter">
              <input
                type="checkbox"
                checked={excludeDemo}
                onChange={(e) => {
                  setExcludeDemo(e.target.checked)
                  if (e.target.checked) {
                    setEnvInput('')
                    setEnvironment('')
                  }
                }}
              />
              Hide demo
            </label>
          </div>
        </div>
      </header>

      <main className="page">
        <div className="filter-status" role="status">
          <span>Environment</span>
          <strong>{excludeDemo ? 'All except demo' : environment || 'All environments'}</strong>
          {environment || excludeDemo ? (
            <button
              type="button"
              onClick={() => {
                setEnvInput('')
                setEnvironment('')
                setExcludeDemo(false)
              }}
            >
              Clear
            </button>
          ) : null}
        </div>
        <View query={query} />
        <footer>
          metergraph-dashboard 0.1.0{isMock() ? ' · mock data (mg_mock=1)' : ''}
        </footer>
      </main>
    </div>
  )
}
