import gzip
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "seed_demo.py"


@pytest.fixture()
def collector():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _send_json(self, status, body, headers=None):
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            requests.append(("GET", self.path, None))
            if self.path == "/v1/config":
                self._send_json(200, {"routes": {}}, {"ETag": '"demo"'})
                return
            self._send_json(404, {})

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if self.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            payload = json.loads(body)
            requests.append(("POST", self.path, payload))
            if self.path == "/v1/ingest/sessions":
                self._send_json(
                    201,
                    {
                        "session_token": "mgs1.demo.signature",
                        "expires_at": "2099-01-01T00:00:00Z",
                    },
                )
                return
            if self.path == "/v1/ingest":
                self._send_json(202, {"accepted": len(payload["rows"]), "ignored": 0})
                return
            self._send_json(404, {})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_demo_uses_sdk_0_4_session_exchange_before_ingest(tmp_path, collector):
    url, requests = collector
    config_dir = tmp_path / ".metergraph"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps({"version": 2, "repository": "acme/demo"})
    )
    env = {
        **os.environ,
        "MG_URL": url,
        "MG_TOKEN": "test-token",
        "METERGRAPH_FLUSH_SECONDS": "0.01",
    }

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "1"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    posts = [(path, payload) for method, path, payload in requests if method == "POST"]
    assert [path for path, _ in posts[:2]] == [
        "/v1/ingest/sessions",
        "/v1/ingest",
    ]
    rows = [row for path, payload in posts if path == "/v1/ingest" for row in payload["rows"]]
    assert rows
    assert {row["sdk_version"] for row in rows} == {version("metergraph")}
    assert {row["provider"] for row in rows} == {"openai", "anthropic", "google"}
