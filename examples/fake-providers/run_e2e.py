"""End-to-end demo: fake providers -> metergraph SDK -> self-hosted server.

Usage:
    METERGRAPH_INGEST_URL=http://localhost:8787 METERGRAPH_APP_TOKEN=dev-token \
        python run_e2e.py

Sends usage rows for three "application functions" so the dashboard's
cost-by-function view has data. No provider API keys required.
"""

from fake_clients import FakeAnthropic, FakeGemini, FakeOpenAI

import metergraph

openai_client = metergraph.wrap(FakeOpenAI(), provider="openai")
anthropic_client = metergraph.wrap(FakeAnthropic(), provider="anthropic")
gemini_client = metergraph.wrap(FakeGemini())


@metergraph.track
def summarize_invoice():
    return openai_client.chat.completions.create(
        model="gpt-5.6-luna",
        messages=[{"role": "user", "content": "summarize"}],
    )


@metergraph.track("support.classify_ticket")
def classify_ticket():
    return anthropic_client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=256,
        messages=[{"role": "user", "content": "classify"}],
    )


@metergraph.track("extraction.parse_receipt")
def parse_receipt():
    return gemini_client.models.generate_content(
        model="gemini-2.5-flash", contents="extract fields"
    )


@metergraph.track("extraction.parse_receipt_stream")
def parse_receipt_stream():
    for _chunk in gemini_client.models.generate_content_stream(
        model="gemini-2.5-flash", contents="extract fields"
    ):
        pass


def main():
    metergraph.init(environment="demo")
    metergraph.set_session("demo-session-1")
    with metergraph.route("invoice-summarizer", unit="invoice"):
        for _ in range(3):
            summarize_invoice()
    with metergraph.route("ticket-classifier", unit="ticket"):
        for _ in range(2):
            classify_ticket()
    with metergraph.route("receipt-parser", unit="receipt"):
        parse_receipt()
        parse_receipt_stream()
    metergraph.flush()
    metergraph.shutdown()
    print("sent 7 calls across 4 tracked functions")


if __name__ == "__main__":
    main()
