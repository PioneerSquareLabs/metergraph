// Eight-color categorical order for spend charts. `seriesColor` wraps, so
// callers that require unique identities must cap or fold additional series.
// Charts must also provide labels, a legend, or a table; color is not identity.
export const SERIES_COLORS = [
  '#2a78d6', // blue
  '#008300', // green
  '#e87ba4', // magenta
  '#eda100', // yellow
  '#10b981', // emerald (brand)
  '#eb6834', // orange
  '#4a3aa7', // violet
  '#e34948', // red
]

// Neutral residual for a folded "other" bucket.
export const OTHER_COLOR = '#9aa4b2'

/** Color for the series at rank `index`; the residual "other" bucket is gray. */
export function seriesColor(index, key) {
  if (key === 'other') return OTHER_COLOR
  return SERIES_COLORS[index % SERIES_COLORS.length]
}
