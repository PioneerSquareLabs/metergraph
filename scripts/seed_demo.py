"""Seed demo traffic into a Metergraph server over the plain ingest API.

No SDK required. Usage:

    MG_URL=http://localhost:8787 MG_TOKEN=dev-token python scripts/seed_demo.py [days]
"""

import json
import os
import random
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

URL = os.environ.get("MG_URL", "http://localhost:8787")
TOKEN = os.environ.get("MG_TOKEN", "dev-token")
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 7

random.seed(7)
NOW = datetime.now(timezone.utc)

PROFILES = [
    # func, module, route, provider, model, calls/day, input range, output range, error rate
    ("app.billing:summarize_invoice", "app.billing", "invoice-summarizer",
     "openai", "gpt-5.6-luna", 9, (2000, 9000), (300, 900), 0.01),
    ("app.billing:audit_line_items", "app.billing", "invoice-summarizer",
     "openai", "gpt-5.6-terra", 3, (6000, 20000), (500, 1500), 0.02),
    ("support.classify_ticket", "app.support", "ticket-classifier",
     "anthropic", "claude-haiku-4-5", 12, (800, 3000), (100, 400), 0.03),
    ("support.draft_reply", "app.support", "reply-drafter",
     "anthropic", "claude-sonnet-5", 5, (3000, 12000), (400, 1200), 0.05),
    ("extraction.parse_receipt", "app.extraction", "receipt-parser",
     "google", "gemini-2.5-flash", 8, (500, 2500), (150, 600), 0.02),
    ("extraction.deep_audit", "app.extraction", "receipt-parser",
     "google", "gemini-3-pro", 2, (5000, 30000), (800, 2500), 0.04),
    ("research.summarize_thread", "app.research", None,
     "openai", "ft:gpt-4o-mini:acme", 2, (1500, 5000), (200, 700), 0.02),
]


def build_rows() -> list[dict]:
    rows = []
    for day in range(DAYS, -1, -1):
        day_start = (NOW - timedelta(days=day)).replace(
            minute=0, second=0, microsecond=0
        )
        daily_scale = 0.6 + 0.55 * random.random() + (0.35 if day in (2, 3) else 0)
        for func, module, route, provider, model, weight, in_r, out_r, err in PROFILES:
            for _ in range(max(1, int(weight * daily_scale))):
                ts = day_start + timedelta(
                    hours=random.randint(8, 22), minutes=random.randint(0, 59)
                )
                if ts > NOW:
                    ts = NOW - timedelta(minutes=random.randint(1, 300))
                failed = random.random() < err
                in_tok = random.randint(*in_r)
                row = {
                    "ts": ts.isoformat(),
                    "func": func,
                    "module": module,
                    "provider": provider,
                    "model": model,
                    "endpoint": "chat.completions",
                    "input_tokens": in_tok,
                    "output_tokens": 0 if failed else random.randint(*out_r),
                    "cache_read_tokens": int(in_tok * random.choice([0, 0, 0.3, 0.6])),
                    "reasoning_tokens": random.randint(50, 400) if "pro" in model else 0,
                    "latency_ms": random.randint(400, 6000),
                    "ttft_ms": random.randint(150, 900),
                    "status": "error" if failed else "stop",
                    "error": failed,
                    "error_type": "APIError" if failed else None,
                    "stream": random.random() < 0.4,
                    "session_id": f"sess-{random.randint(1, 40)}",
                    "template_hash": "th" + func[-6:],
                    "tags": {"team": module.split(".")[-1]},
                    "environment": "production",
                    "sdk": "python",
                    "sdk_version": "0.2.0",
                }
                if route:
                    row["route"] = route
                rows.append(row)
    return rows


def main() -> None:
    rows = build_rows()
    body = json.dumps({"schema_version": 1, "rows": rows}).encode()
    request = urllib.request.Request(
        f"{URL}/v1/ingest",
        data=body,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request) as response:
        print(response.status, response.read().decode())
    print(f"sent {len(rows)} rows to {URL}")


if __name__ == "__main__":
    main()
