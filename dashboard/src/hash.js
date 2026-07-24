/** Navigate to a tab and mark a key as selected there, e.g. #/functions?select=app.billing%3Asummarize */
export function navigateWithSelection(tab, key) {
  window.location.hash = `#/${tab}?select=${encodeURIComponent(key)}`
}

/** Reads `select` off the hash. Pure — must stay side-effect-free, since
 * React may invoke a useState initializer twice (e.g. StrictMode). */
export function consumeHashSelection() {
  const query = window.location.hash.split('?')[1]
  if (!query) return null
  return new URLSearchParams(query).get('select')
}

/** Strips `?select=...` from the hash. Call from an effect, not the
 * initializer above — this one is a side effect. */
export function clearHashSelection() {
  const [route, query] = window.location.hash.split('?')
  if (query) window.history.replaceState(null, '', route || '#/')
}
