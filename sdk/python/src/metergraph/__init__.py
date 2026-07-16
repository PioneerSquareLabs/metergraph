"""Public Metergraph Python SDK."""

from __future__ import annotations

import atexit
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Callable

from ._capture import Options, Runtime, set_runtime, wrap
from ._config import ConfigPoller
from ._context import route, set_session, set_tags, snapshot, wrap_executor
from ._track import track
from ._transport import Writer
from ._version import SDK_VERSION


__version__ = SDK_VERSION
DEFAULT_INGEST_URL = "https://d2xus7mp8zdv6t.cloudfront.net"
log = logging.getLogger("metergraph")
_writer: Writer | None = None
_config: ConfigPoller | None = None
_initialized = False


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() not in {"0", "false", "no", "off"}


def init(
    *,
    token: str | None = None,
    ingest_url: str | None = None,
    capture_text: bool | None = None,
    redact: Callable[[str, str], str] | None = None,
    app_root: str | None = None,
    skip_frames: list[str] | None = None,
    environment: str | None = None,
    disabled: bool | None = None,
) -> None:
    """Initialize capture. This function is idempotent and never raises."""
    global _initialized, _writer, _config
    if _initialized:
        return
    _initialized = True
    if os.getenv("METERGRAPH_DISABLED") == "1" or disabled:
        return
    token = token or os.getenv("METERGRAPH_APP_TOKEN")
    ingest_url = ingest_url or os.getenv("METERGRAPH_INGEST_URL") or DEFAULT_INGEST_URL
    if not token or not ingest_url:
        log.warning("Metergraph capture disabled: token and ingest URL are required")
        return
    try:
        _writer = Writer(
            token,
            ingest_url,
            queue_size=int(os.getenv("METERGRAPH_QUEUE_SIZE", "2000")),
            batch_size=int(os.getenv("METERGRAPH_BATCH_SIZE", "100")),
            flush_seconds=float(os.getenv("METERGRAPH_FLUSH_SECONDS", "5")),
        )
        options = Options(
            capture_text=(
                _env_bool("METERGRAPH_CAPTURE_TEXT", False)
                if capture_text is None
                else capture_text
            ),
            redact=redact,
            app_root=os.path.realpath(app_root or os.getcwd()),
            skip_frames=tuple(skip_frames or ()),
            environment=environment or os.getenv("METERGRAPH_ENV"),
            text_max_bytes=int(os.getenv("METERGRAPH_TEXT_MAX_BYTES", "100000")),
        )
        set_runtime(Runtime(_writer, options))
        _config = ConfigPoller(
            token,
            ingest_url,
            poll_seconds=float(os.getenv("METERGRAPH_CONFIG_POLL_SECONDS", "30")),
            hard_ttl_seconds=float(
                os.getenv("METERGRAPH_CONFIG_HARD_TTL_SECONDS", "120")
            ),
        )
        atexit.register(shutdown)
    except Exception:
        set_runtime(None)
        if _writer:
            _writer.shutdown()
        _writer = None
        _config = None
        log.warning(
            "Metergraph initialization failed; application is running uninstrumented"
        )


def model_for(route_name: str, *, default: str, session_key: str | None = None) -> str:
    """Return a sticky canary model, or the incumbent on every failure path."""
    if _config is None:
        return default
    return _config.model_for(route_name, default, session_key or snapshot().session_id)


def record_outcome(
    route_name: str,
    *,
    model: str,
    task_completed: bool,
    session_key: str | None = None,
    feedback_score: float | None = None,
    turns_to_resolution: int | None = None,
    escalated: bool | None = None,
    abandoned: bool | None = None,
    edit_distance_ratio: float | None = None,
    regeneration_count: int | None = None,
    event_id: str | None = None,
) -> bool:
    """Enqueue a content-free real outcome without touching the request path."""
    if _writer is None or not isinstance(task_completed, bool):
        return False
    route_name = str(route_name).strip()[:512]
    model = str(model).strip()[:512]
    session_key = str(session_key or snapshot().session_id or "").strip()[:512]
    event_id = str(event_id or uuid.uuid4()).strip()[:128]
    try:
        feedback_score = float(feedback_score) if feedback_score is not None else None
        turns_to_resolution = (
            int(turns_to_resolution) if turns_to_resolution is not None else None
        )
        edit_distance_ratio = (
            float(edit_distance_ratio) if edit_distance_ratio is not None else None
        )
        regeneration_count = (
            int(regeneration_count) if regeneration_count is not None else None
        )
    except (TypeError, ValueError, OverflowError):
        return False
    if not route_name or not model or not session_key or not event_id:
        return False
    if feedback_score is not None and not -1 <= feedback_score <= 1:
        return False
    if turns_to_resolution is not None and not 1 <= turns_to_resolution <= 1_000_000:
        return False
    if edit_distance_ratio is not None and not 0 <= edit_distance_ratio <= 1:
        return False
    if regeneration_count is not None and not 0 <= regeneration_count <= 1_000_000:
        return False
    if escalated is not None and not isinstance(escalated, bool):
        return False
    if abandoned is not None and not isinstance(abandoned, bool):
        return False
    return _writer.enqueue(
        {
            "event_type": "outcome",
            "event_id": event_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "route": route_name,
            "session_id": session_key,
            "model": model,
            "task_completed": task_completed,
            "feedback_score": feedback_score,
            "turns_to_resolution": turns_to_resolution,
            "escalated": escalated,
            "abandoned": abandoned,
            "edit_distance_ratio": edit_distance_ratio,
            "regeneration_count": regeneration_count,
        }
    )


def flush(timeout: float = 3.0) -> bool:
    return True if _writer is None else _writer.flush(timeout)


def shutdown() -> None:
    global _writer, _config
    if _config:
        _config.stop()
        _config = None
    if _writer:
        _writer.shutdown()
        _writer = None
    set_runtime(None)


__all__ = [
    "DEFAULT_INGEST_URL",
    "flush",
    "init",
    "model_for",
    "record_outcome",
    "route",
    "set_session",
    "set_tags",
    "shutdown",
    "track",
    "wrap",
    "wrap_executor",
]
