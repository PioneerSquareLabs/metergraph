import { useEffect, useState } from 'react'

const TOKEN_KEY = 'mg_token'
const MOCK_KEY = 'mg_mock'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

export function isMock() {
  return localStorage.getItem(MOCK_KEY) === '1'
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail)
    this.status = status
    this.detail = detail
  }
}

/**
 * Fetch a /v1 endpoint with the bearer token. Params with empty/null values
 * are omitted. On 401 the stored token is cleared and an "mg:unauthorized"
 * window event is dispatched so the app can re-prompt.
 */
export async function api(path, params = {}) {
  if (isMock()) {
    const { mockApi } = await import('./mock.js')
    return mockApi(path, params)
  }

  const qs = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') qs.set(key, value)
  }
  const url = qs.toString() ? `${path}?${qs}` : path

  const res = await fetch(url, {
    headers: { Authorization: `Bearer ${getToken() || ''}` },
  })

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body && body.detail) detail = body.detail
    } catch {
      /* non-JSON error body */
    }
    if (res.status === 401) {
      clearToken()
      window.dispatchEvent(new CustomEvent('mg:unauthorized', { detail }))
    }
    throw new ApiError(res.status, detail)
  }

  return res.json()
}

/** Small data-fetching hook: { loading, data, error }, refetches when deps change. */
export function useApi(call, deps) {
  const [state, setState] = useState({ loading: true, data: null, error: null })
  useEffect(() => {
    let alive = true
    setState((s) => ({ ...s, loading: true, error: null }))
    call().then(
      (data) => alive && setState({ loading: false, data, error: null }),
      (error) => alive && setState({ loading: false, data: null, error }),
    )
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return state
}
