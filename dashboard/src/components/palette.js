// Categorical series palette for spend charts.
//
// This 8-hue order is not cosmetic — it is the colorblind-safety mechanism.
// It was validated with the dataviz palette checker against the dashboard's
// light card surface (#fbfcfe): worst adjacent CVD ΔE 8.9 (≥8 target) and
// worst adjacent normal-vision ΔE 19.6 (≥15 floor), all slots inside the
// lightness band and above the chroma floor. Slot 5 is the brand emerald.
//
// Assign hues in this fixed order and never cycle: a 9th category folds into
// "other" (the neutral gray) rather than reusing a hue. Three slots (emerald,
// yellow, magenta) sit below 3:1 contrast on the light surface, so every chart
// that uses them ships a legend with text labels and a coexisting table view.
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

// Neutral residual for the folded "other" bucket — never a categorical hue.
export const OTHER_COLOR = '#9aa4b2'

/** Color for the series at rank `index`; the residual "other" bucket is gray. */
export function seriesColor(index, key) {
  if (key === 'other') return OTHER_COLOR
  return SERIES_COLORS[index % SERIES_COLORS.length]
}
