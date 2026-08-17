"""Seed demo traffic through the MeterGraph Python SDK.

No provider API key is required: the SDK wraps a local OpenAI-shaped fake.
Usage:

    MG_URL=http://localhost:8787 MG_TOKEN=dev-token python scripts/seed_demo.py [multiplier]
"""

import os
import random
import sys
import time
import uuid

try:
    import metergraph
except ImportError as exc:
    raise SystemExit(
        "The demo requires the MeterGraph SDK. Install it with "
        "`python -m pip install 'metergraph>=0.4,<1'`."
    ) from exc


URL = os.environ.get("MG_URL", "http://localhost:8787")
TOKEN = os.environ.get("MG_TOKEN", "dev-token")
MULTIPLIER = int(sys.argv[1]) if len(sys.argv) > 1 else 1

random.seed(7)

PROFILES = [
    # function, route, provider, model, calls, input range, output range, error rate
    ("summarize_invoice", "invoice-summarizer", "openai", "gpt-5.6-luna", 9, (2000, 9000), (300, 900), 0.01),
    ("audit_line_items", "invoice-summarizer", "openai", "gpt-5.6-terra", 3, (6000, 20000), (500, 1500), 0.02),
    ("classify_ticket", "ticket-classifier", "anthropic", "claude-haiku-4-5", 12, (800, 3000), (100, 400), 0.03),
    ("draft_reply", "reply-drafter", "anthropic", "claude-sonnet-5", 5, (3000, 12000), (400, 1200), 0.05),
    ("parse_receipt", "receipt-parser", "google", "gemini-2.5-flash", 8, (500, 2500), (150, 600), 0.02),
    ("deep_audit", "receipt-parser", "google", "gemini-3-pro", 2, (5000, 30000), (800, 2500), 0.04),
    ("summarize_thread", None, "openai", "ft:gpt-4o-mini:acme", 2, (1500, 5000), (200, 700), 0.02),
]


class DemoCompletions:
    def create(self, *, model, messages, demo_input_tokens, demo_output_tokens):
        return {
            "id": f"chatcmpl-demo-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Demo response"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": demo_input_tokens,
                "completion_tokens": demo_output_tokens,
                "total_tokens": demo_input_tokens + demo_output_tokens,
            },
        }


class DemoChat:
    def __init__(self):
        self.completions = DemoCompletions()


class DemoOpenAI:
    def __init__(self):
        self.chat = DemoChat()


class DemoAnthropicMessages:
    def create(self, *, model, messages, demo_input_tokens, demo_output_tokens):
        return {
            "id": f"msg-demo-{uuid.uuid4().hex[:12]}",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": "Demo response"}],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": demo_input_tokens,
                "output_tokens": demo_output_tokens,
            },
        }


class DemoAnthropic:
    def __init__(self):
        self.messages = DemoAnthropicMessages()


class DemoGoogleModels:
    def generate_content(
        self, *, model, contents, demo_input_tokens, demo_output_tokens
    ):
        return {
            "response_id": f"gemini-demo-{uuid.uuid4().hex[:12]}",
            "model_version": model,
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"text": "Demo response"}],
                    },
                    "finish_reason": "STOP",
                }
            ],
            "usage_metadata": {
                "prompt_token_count": demo_input_tokens,
                "candidates_token_count": demo_output_tokens,
                "total_token_count": demo_input_tokens + demo_output_tokens,
            },
        }


class DemoGoogle:
    def __init__(self):
        self.models = DemoGoogleModels()


def run_profile(client, provider, name, route, model, input_range, output_range):
    def instrumented_call():
        common = {
            "model": model,
            "demo_input_tokens": random.randint(*input_range),
            "demo_output_tokens": random.randint(*output_range),
        }
        if provider == "openai":
            return client.chat.completions.create(
                messages=[{"role": "user", "content": "Generate one demo response."}],
                **common,
            )
        if provider == "anthropic":
            return client.messages.create(
                messages=[{"role": "user", "content": "Generate one demo response."}],
                **common,
            )
        return client.models.generate_content(
            contents="Generate one demo response.", **common
        )

    instrumented_call.__name__ = name
    with metergraph.track(name, module="demo"):
        if route:
            with metergraph.route(route):
                return instrumented_call()
        return instrumented_call()


def main() -> None:
    if MULTIPLIER < 1:
        raise SystemExit("multiplier must be at least 1")
    metergraph.init(
        token=TOKEN,
        ingest_url=URL,
        capture_text=False,
        app_root=os.getcwd(),
        environment="demo",
    )
    clients = {
        "openai": metergraph.wrap(DemoOpenAI(), provider="openai"),
        "anthropic": metergraph.wrap(DemoAnthropic(), provider="anthropic"),
        "google": metergraph.wrap(DemoGoogle(), provider="google"),
    }
    sent = 0
    try:
        with metergraph.trace("self-hosted-demo"):
            for name, route, provider, model, calls, in_range, out_range, _error in PROFILES:
                for _ in range(calls * MULTIPLIER):
                    run_profile(
                        clients[provider],
                        provider,
                        name,
                        route,
                        model,
                        in_range,
                        out_range,
                    )
                    sent += 1
        if not metergraph.flush(timeout=10):
            raise RuntimeError("MeterGraph SDK did not flush demo traffic")
    finally:
        metergraph.shutdown()
    print(f"sent {sent} SDK-instrumented demo calls to {URL}")


if __name__ == "__main__":
    main()
