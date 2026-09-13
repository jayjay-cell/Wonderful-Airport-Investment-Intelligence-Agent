"""Graceful-degradation helpers: bounded retry and a typed unavailable-source
error that callers (tools/) can catch to produce a partial result with a
stated caveat, rather than crashing or silently returning nothing.
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

T = TypeVar("T")


class SourceUnavailable(Exception):
    """Raised when a data source fails after its bounded retry budget.
    Callers should catch this, surface SOURCE_UNAVAILABLE with `detail`,
    and — where possible — continue with a partial result and lowered
    Confidence rather than aborting the whole tool call."""

    def __init__(self, source_name: str, detail: str):
        self.source_name = source_name
        self.detail = detail
        super().__init__(f"{source_name}: {detail}")


def with_retry(fn: Callable[[], T], retries: int = 1, backoff_seconds: float = 0.5) -> T:
    """Runs fn(), retrying up to `retries` additional times on any
    exception, with linear backoff. Re-raises the last exception if all
    attempts fail — callers wrap this in SourceUnavailable."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - intentionally broad, this is the retry boundary
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff_seconds)
    raise last_exc  # type: ignore[misc]
