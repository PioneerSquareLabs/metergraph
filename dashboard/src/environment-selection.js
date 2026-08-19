const UNTAGGED = 'untagged'

export function environmentKey(value) {
  return value === null ? UNTAGGED : `named:${value}`
}

export function buildEnvironmentQuery(items, selected) {
  if (selected === null) return {}
  return {
    environment: items
      .filter((item) => item.value !== null && selected.has(environmentKey(item.value)))
      .map((item) => item.value),
    includeUntagged: items.some(
      (item) => item.value === null && selected.has(environmentKey(item.value)),
    ),
  }
}
