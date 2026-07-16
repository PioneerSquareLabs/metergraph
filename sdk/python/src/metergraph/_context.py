"""Ambient route/session metadata, safe across async call trees."""

from __future__ import annotations

import contextvars
import functools
import inspect
from concurrent.futures import Executor
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class CaptureContext:
    route: str | None = None
    session_id: str | None = None
    tags: Mapping[str, str] = field(default_factory=dict)
    unit_name: str | None = None
    unit_count: float | None = None
    capture_text: bool | None = None
    func_name: str | None = None
    func_module: str | None = None


_current: contextvars.ContextVar[CaptureContext] = contextvars.ContextVar(
    "metergraph_context", default=CaptureContext()
)


def snapshot() -> CaptureContext:
    return _current.get()


class route:
    """Route context manager and sync/async decorator."""

    def __init__(
        self,
        name: str,
        *,
        unit: str | None = None,
        unit_count: float | None = None,
        tags: Mapping[str, Any] | None = None,
        capture_text: bool | None = None,
    ) -> None:
        self.name = str(name)
        self.unit = str(unit) if unit is not None else None
        self.unit_count = (
            float(unit_count) if unit_count is not None else (1.0 if unit else None)
        )
        self.tags = {str(k): str(v) for k, v in (tags or {}).items()}
        self.capture_text = (
            bool(capture_text) if capture_text is not None else None
        )
        self._token: contextvars.Token[CaptureContext] | None = None

    def __enter__(self) -> "route":
        current = snapshot()
        merged = {**current.tags, **self.tags}
        self._token = _current.set(
            replace(
                current,
                route=self.name,
                tags=merged,
                unit_name=self.unit if self.unit is not None else current.unit_name,
                unit_count=self.unit_count
                if self.unit is not None
                else current.unit_count,
                capture_text=self.capture_text
                if self.capture_text is not None
                else current.capture_text,
            )
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._token is not None:
            _current.reset(self._token)
            self._token = None

    def __call__(self, fn: Callable):
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapped(*args, **kwargs):
                with type(self)(
                    self.name,
                    unit=self.unit,
                    unit_count=self.unit_count,
                    tags=self.tags,
                    capture_text=self.capture_text,
                ):
                    return await fn(*args, **kwargs)

            return async_wrapped

        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            with type(self)(
                self.name,
                unit=self.unit,
                unit_count=self.unit_count,
                tags=self.tags,
                capture_text=self.capture_text,
            ):
                return fn(*args, **kwargs)

        return wrapped


def set_session(session_id: str | None) -> None:
    _current.set(
        replace(snapshot(), session_id=str(session_id) if session_id else None)
    )


def set_tags(**tags: Any) -> None:
    current = snapshot()
    merged = {**current.tags, **{str(k): str(v) for k, v in tags.items()}}
    _current.set(replace(current, tags=merged))


def wrap_executor(executor: Executor) -> Executor:
    """Propagate the current context into executor submissions."""
    if getattr(executor, "__metergraph__", False):
        return executor
    original_submit = executor.submit

    @functools.wraps(original_submit)
    def submit(fn, /, *args, **kwargs):
        ctx = contextvars.copy_context()
        return original_submit(ctx.run, fn, *args, **kwargs)

    executor.submit = submit  # type: ignore[method-assign]
    executor.__metergraph__ = True  # type: ignore[attr-defined]
    return executor
