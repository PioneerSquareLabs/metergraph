import { useState } from 'react'
import { setToken } from '../api.js'

export default function TokenGate({ error, onDone }) {
  const [value, setValue] = useState('')

  const submit = (e) => {
    e.preventDefault()
    const token = value.trim()
    if (!token) return
    setToken(token)
    onDone()
  }

  return (
    <div className="token-shell">
      <div className="token-glow" />
      <form className="token-card" onSubmit={submit}>
        <div className="wordmark" aria-label="Metergraph">
          <b><span>meter</span><span className="wm-graph">graph</span><span className="wm-spike" aria-hidden="true">&#10033;</span></b>
          <small>OSS</small>
        </div>
        <p className="eyebrow">Self-hosted · LLM cost tracking</p>
        <h1>Connect to your server</h1>
        <p className="token-copy">
          Paste the API token configured on your Metergraph server. It is stored locally in this
          browser and sent as a bearer token with every request.
        </p>
        {error ? <div className="error-banner">{error}</div> : null}
        <input
          type="password"
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="mg_………"
          aria-label="API token"
        />
        <button type="submit" className="primary-button">
          Connect <span>→</span>
        </button>
        <p className="secure-note">
          <span className="status-dot" /> stored in localStorage only
        </p>
      </form>
    </div>
  )
}
