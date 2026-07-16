# Examples

Each example wraps a real provider client, attributes calls to a function with `track`, and sends usage metadata to your Metergraph server.

Setup for all of them:

```bash
export METERGRAPH_INGEST_URL=http://localhost:8787   # your self-hosted server
export METERGRAPH_APP_TOKEN=dev-token                # one of MG_TOKENS
```

| Example | Needs |
|---|---|
| `fake-providers/run_e2e.py` | nothing — offline demo traffic |
| `python-openai/main.py` | `pip install metergraph openai`, `OPENAI_API_KEY` |
| `python-anthropic/main.py` | `pip install metergraph anthropic`, `ANTHROPIC_API_KEY` |
| `python-gemini/main.py` | `pip install metergraph google-genai`, `GEMINI_API_KEY` |
| `node-openai/main.mjs` | `npm i metergraph openai`, `OPENAI_API_KEY` |
| `node-anthropic/main.mjs` | `npm i metergraph @anthropic-ai/sdk`, `ANTHROPIC_API_KEY` |
| `node-gemini/main.mjs` | `npm i metergraph @google/genai`, `GEMINI_API_KEY` |
