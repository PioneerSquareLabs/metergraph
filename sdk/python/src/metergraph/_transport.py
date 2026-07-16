"""Bounded, fire-and-forget HTTP transport using only the stdlib."""

from __future__ import annotations

import gzip
import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from ._version import SDK_VERSION


log = logging.getLogger("metergraph")
MAX_BATCH_BYTES = 512 * 1024


class Writer:
    def __init__(
        self,
        token: str,
        base_url: str,
        *,
        queue_size: int = 2000,
        batch_size: int = 100,
        flush_seconds: float = 5.0,
    ) -> None:
        self._token = token
        self._url = f"{base_url.rstrip('/')}/v1/ingest"
        self._queue_size = max(1, queue_size)
        self._batch_size = max(1, min(batch_size, 1000))
        self._flush_seconds = max(0.05, flush_seconds)
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(self._queue_size)
        self._stop = threading.Event()
        self._flush_now = threading.Event()
        self._fatal = False
        self._dropped = 0
        self._errors = 0
        self._backoff = 1.0
        self._retry_at = 0.0
        self._thread = self._new_thread()
        self._thread.start()
        if hasattr(os, "register_at_fork"):
            os.register_at_fork(after_in_child=self._after_fork)

    @property
    def dropped(self) -> int:
        return self._dropped

    def _new_thread(self) -> threading.Thread:
        return threading.Thread(target=self._run, name="metergraph-writer", daemon=True)

    def _after_fork(self) -> None:
        # Inherited Queue locks and threads are not safe in the child. Pending
        # parent rows stay with the parent; the child starts a clean writer.
        self._queue = queue.Queue(self._queue_size)
        self._stop = threading.Event()
        self._flush_now = threading.Event()
        self._thread = self._new_thread()
        self._thread.start()

    def enqueue(self, row: dict[str, Any]) -> bool:
        if self._fatal or self._stop.is_set():
            self._dropped += 1
            return False
        try:
            self._queue.put_nowait(row)
            return True
        except queue.Full:
            self._dropped += 1
            return False

    def _run(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                first = self._queue.get(timeout=min(self._flush_seconds, 0.1))
            except queue.Empty:
                continue
            batch = [first]
            deadline = time.monotonic() + self._flush_seconds
            while len(batch) < self._batch_size:
                if self._flush_now.is_set():
                    break
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or self._stop.is_set():
                        break
                    try:
                        batch.append(self._queue.get(timeout=min(remaining, 0.05)))
                    except queue.Empty:
                        continue
            try:
                self._deliver(batch)
            finally:
                self._flush_now.clear()
                for _ in batch:
                    self._queue.task_done()

    def _deliver(self, rows: list[dict[str, Any]]) -> bool:
        if self._fatal or time.monotonic() < self._retry_at:
            self._dropped += len(rows)
            return False
        meta = {"dropped": self._dropped, "transport_errors": self._errors}
        body = json.dumps(
            {"schema_version": 1, "rows": rows, "meta": meta},
            separators=(",", ":"),
        ).encode()
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "User-Agent": f"metergraph-python/{SDK_VERSION}",
        }
        if len(body) > 32 * 1024:
            body = gzip.compress(body)
            headers["Content-Encoding"] = "gzip"
        if len(body) > MAX_BATCH_BYTES:
            if len(rows) == 1:
                self._dropped += 1
                return False
            midpoint = len(rows) // 2
            left = self._deliver(rows[:midpoint])
            right = self._deliver(rows[midpoint:])
            return left and right
        request = urllib.request.Request(
            self._url, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                if response.status != 202:
                    raise OSError(f"unexpected ingest status {response.status}")
            self._backoff = 1.0
            self._retry_at = 0.0
            return True
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                self._fatal = True
                log.warning(
                    "Metergraph authentication failed; capture disabled for this process"
                )
            else:
                self._failed(len(rows))
            return False
        except Exception:
            self._failed(len(rows))
            return False

    def _failed(self, count: int) -> None:
        self._errors += 1
        self._dropped += count
        self._retry_at = time.monotonic() + self._backoff
        self._backoff = min(self._backoff * 2, 60.0)

    def flush(self, timeout: float = 3.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        self._flush_now.set()
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        return self._queue.unfinished_tasks == 0

    def shutdown(self) -> None:
        self.flush(3.0)
        self._stop.set()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=max(1.0, self._flush_seconds + 0.5))
