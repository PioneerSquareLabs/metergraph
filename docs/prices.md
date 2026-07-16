# Price catalog

`server/src/metergraph_server/prices.yaml` is a versioned, effective-dated model price catalog. The server prices each call **at the call's timestamp**, so historical rows stay correct when prices change.

## Structure

```yaml
version: "2026-07-15"
models:
  - canonical_id: anthropic/claude-sonnet-5
    publisher: anthropic
    aliases:                      # (provider, alias) pairs the SDKs may report
      - {provider: anthropic, alias: claude-sonnet-5, channel: anthropic-api}
    prices:
      - channel: anthropic-api
        region: global            # matched against MG_REGION, then '*', then 'global'
        effective_from: "2026-06-30"
        effective_to: "2026-09-01"   # omit for the open window
        input_per_mtok: 2.00
        output_per_mtok: 10.00
        cache_read_per_mtok: 0.20
        cache_write_5m_per_mtok: 2.50
        batch_input_per_mtok: 1.00
        batch_output_per_mtok: 5.00
        rules: {}
        source_url: https://platform.claude.com/docs/en/about-claude/pricing
```

`rules` options:
- `input_includes_cache_read: true` — provider reports cached tokens inside `input_tokens` (OpenAI); cache reads are deducted from billable input.
- `long_context: {threshold, input_multiplier, output_multiplier}` — surcharge above a prompt-size threshold (OpenAI GPT-5.6, Gemini Pro).
- `uncaptured_fees: true` — provider charges fees tokens can't express; rows are marked `partial`.

## Cost status

Every stored call gets a `cost_status`:
- `priced` — fully priced from the catalog
- `partial` — priced, but something was missing (e.g. cache rate unavailable); the stored cost is a lower bound
- `unpriced` — unknown model or no effective price window; the dashboard surfaces these so you know to update the catalog

## Updating

1. Never edit a historical entry — close its window with `effective_to` and add a new entry.
2. Include a `source_url` for every price.
3. Open a PR; CI validates structure, dates, and window overlaps.
4. Self-hosters: mount an updated file with `MG_PRICES_PATH=/path/to/prices.yaml` — no rebuild needed.
