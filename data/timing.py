"""Structured timing instrumentation. A single small context manager used
consistently across the request path so bottlenecks are immediately
identifiable in logs, per the spec's instrumentation requirement.

Deliberately minimal — logs one line per timed span via the standard
logging module (no metrics backend, no tracing infrastructure). Never logs
secrets, full prompts, or large payloads — only span name, duration, and a
small set of scalar labels (e.g. cache hit/miss, provider name).
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager

logger = logging.getLogger("perf")


@contextmanager
def timed(span_name: str, **labels):
    """Logs `span_name` duration on exit, plus any scalar labels (e.g.
    airport="ANC", cache="hit", provider="groq"). Labels must be small,
    non-sensitive scalars — never pass full API responses, prompts, or
    keys here.

    Usage:
        with timed("bts_t100.fetch", origin="ANC", year=2024):
            ...

    On exception, still logs the elapsed time (with error=True) before
    re-raising, so a failed/timed-out call is visible in the timing log
    rather than silently missing.
    """
    start = time.monotonic()
    label_str = " ".join(f"{k}={v}" for k, v in labels.items())
    try:
        yield
    except Exception:
        elapsed = time.monotonic() - start
        logger.info("perf: span=%s elapsed=%.3fs error=True %s", span_name, elapsed, label_str)
        raise
    else:
        elapsed = time.monotonic() - start
        logger.info("perf: span=%s elapsed=%.3fs %s", span_name, elapsed, label_str)
