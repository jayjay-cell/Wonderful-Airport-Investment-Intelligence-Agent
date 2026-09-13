"""In-memory cache, keyed per-process. Deliberately simple (a dict), per the
plan: no DuckDB/Parquet/large pipeline for the MVP. Never persists a
nationwide dataset — only caches results for airports actually queried.
"""

from __future__ import annotations

import time

_STORE: dict[str, tuple[float, object]] = {}

DEFAULT_TTL_SECONDS = 6 * 60 * 60  # 6 hours — static/annual sources change rarely


def cache_get(key: str) -> object | None:
    entry = _STORE.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if time.time() > expires_at:
        _STORE.pop(key, None)
        return None
    return value


def cache_set(key: str, value: object, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
    _STORE[key] = (time.time() + ttl_seconds, value)


def cache_status(key: str) -> str:
    """Returns 'hit' or 'miss' without affecting expiry, for surfacing cache
    status in tool results."""
    return "hit" if cache_get(key) is not None else "miss"


def cache_clear() -> None:
    """Test-only helper."""
    _STORE.clear()
