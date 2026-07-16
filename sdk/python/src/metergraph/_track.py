"""Explicit call-site attribution, overriding the default stack-walk."""

from __future__ import annotations

import contextvars
import functools
import inspect
from dataclasses import replace
from typing import Callable

from ._context import CaptureContext, _current, snapshot


class track:
    """Attribution context manager and sync/async decorator."""

    def __new__(
        cls, name: Callable | str | None = None, *, module: str | None = None
    ):
        if callable(name):
            instance = super().__new__(cls)
            instance.__init__(module=module)
            return instance(name)
        return super().__new__(cls)

    def __init__(
        self, name: str | None = None, *, module: str | None = None
    ) -> None:
        self.name = str(name) if name is not None else None
        self.module = str(module) if module is not None else None
        self._token: contextvars.Token[CaptureContext] | None = None

    def __enter__(self) -> "track":
        self._token = _current.set(
            replace(snapshot(), func_name=self.name, func_module=self.module)
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._token is not None:
            _current.reset(self._token)
            self._token = None

    def __call__(self, fn: Callable):
        name = self.name or f"{fn.__module__}:{fn.__qualname__}"
        module = self.module or fn.__module__
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapped(*args, **kwargs):
                with type(self)(name, module=module):
                    return await fn(*args, **kwargs)

            return async_wrapped

        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            with type(self)(name, module=module):
                return fn(*args, **kwargs)

        return wrapped
