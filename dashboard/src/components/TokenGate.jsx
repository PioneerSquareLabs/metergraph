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
        <div className="logo">
          <svg viewBox="0 0 24 24">
            <path d="M3 20 9 8l4 7 3-5 5 10" />
          </svg>
          Metergraph
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
