"""Thread-safe in-memory cache with single-flight deduplication.

Deliberately still a plain in-process dict (no DuckDB/Parquet/Redis/other
infrastructure) — this is the minimum robust behavior needed, not a
platform. Two properties beyond the original version:

1. Thread-safe reads/writes (a lock around the store).
2. Single-flight: if N threads request the same cold key concurrently,
   only one of them does the actual work (fetch+parse); the rest wait for
   and share that one result. This is what stops "LAX vs SNA" or an
   8-airport ranking from triggering multiple simultaneous downloads of
   the identical national BTS/FAA file.

TTL and no-stale-data behavior are unchanged: every entry has an expiry,
and cache_get() returns None (a real miss) once expired — callers always
re-fetch rather than serve stale data past its TTL.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, TypeVar

logger = logging.getLogger("perf.cache")

T = TypeVar("T")

_LOCK = threading.Lock()
_STORE: dict[str, tuple[float, object]] = {}
_INFLIGHT: dict[str, threading.Event] = {}
_INFLIGHT_RESULT: dict[str, tuple[bool, object]] = {}  # key -> (ok, value_or_exception)

DEFAULT_TTL_SECONDS = 6 * 60 * 60  # 6 hours — static/annual sources change rarely

# Rough cap on distinct cache entries, to avoid unbounded memory growth from
# a long-running process fielding many distinct airports/periods. Eviction
# is simplest-possible (drop oldest-inserted) — adequate for a one-day
# assignment's process lifetime, not a production LRU.
_MAX_ENTRIES = 500


def _evict_if_over_capacity_locked() -> None:
    if len(_STORE) <= _MAX_ENTRIES:
        return
    # Drop the entries with the earliest expiry (proxy for oldest/least
    # freshly-set) until back under the cap.
    overflow = len(_STORE) - _MAX_ENTRIES
    oldest_keys = sorted(_STORE, key=lambda k: _STORE[k][0])[:overflow]
    for k in oldest_keys:
        _STORE.pop(k, None)


def cache_get(key: str) -> object | None:
    with _LOCK:
        entry = _STORE.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.time() > expires_at:
            _STORE.pop(key, None)
            return None
        return value


def cache_set(key: str, value: object, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
    with _LOCK:
        _STORE[key] = (time.time() + ttl_seconds, value)
        _evict_if_over_capacity_locked()


def cache_status(key: str) -> str:
    """Returns 'hit' or 'miss' without affecting expiry, for surfacing cache
    status in tool results."""
    return "hit" if cache_get(key) is not None else "miss"


def cache_clear() -> None:
    """Test-only helper."""
    with _LOCK:
        _STORE.clear()
        _INFLIGHT.clear()
        _INFLIGHT_RESULT.clear()


def get_or_load(key: str, loader: Callable[[], T], ttl_seconds: float = DEFAULT_TTL_SECONDS) -> T:
    """Cache-or-compute with single-flight deduplication.

    If `key` is cached (and not expired), returns it immediately — no lock
    contention beyond the cheap dict read. If `key` is cold, exactly one
    caller (across threads) runs `loader()`; every other concurrent caller
    for the same key blocks on an Event and receives that same result
    (or that same exception, re-raised) instead of independently calling
    `loader()` itself. This is the mechanism that turns "N threads, same
    national BTS file, N downloads" into "N threads, same file, 1 download."
    """
    cached = cache_get(key)
    if cached is not None:
        logger.info("perf: cache=hit key=%s", key)
        return cached  # type: ignore[return-value]

    with _LOCK:
        # Re-check under the lock — another thread may have just finished
        # loading and populated the cache between our cache_get() above and
        # acquiring this lock.
        cached = _STORE.get(key)
        if cached is not None and time.time() <= cached[0]:
            return cached[1]  # type: ignore[return-value]

        existing_event = _INFLIGHT.get(key)
        if existing_event is None:
            # We are the leader for this key: create the event, release the
            # lock, and actually do the work.
            event = threading.Event()
            _INFLIGHT[key] = event
            is_leader = True
        else:
            event = existing_event
            is_leader = False

    if not is_leader:
        logger.info("perf: cache=miss key=%s role=follower (waiting on in-flight leader)", key)
        wait_start = time.monotonic()
        event.wait()
        logger.info("perf: key=%s follower_wait=%.3fs", key, time.monotonic() - wait_start)
        ok, value = _INFLIGHT_RESULT.get(key, (False, RuntimeError(f"single-flight result missing for {key!r}")))
        if ok:
            return value  # type: ignore[return-value]
        raise value  # type: ignore[misc]

    # Leader path: do the real work OUTSIDE the lock so concurrent readers
    # of other keys are never blocked by one slow download.
    logger.info("perf: cache=miss key=%s role=leader (loading)", key)
    load_start = time.monotonic()
    try:
        result = loader()
        logger.info("perf: key=%s load=%.3fs", key, time.monotonic() - load_start)
        cache_set(key, result, ttl_seconds=ttl_seconds)
        with _LOCK:
            _INFLIGHT_RESULT[key] = (True, result)
        return result
    except Exception as exc:
        with _LOCK:
            _INFLIGHT_RESULT[key] = (False, exc)
        raise
    finally:
        with _LOCK:
            _INFLIGHT.pop(key, None)
            event.set()
        # Result stash only needs to live long enough for waiters woken by
        # this event to read it; clear it after a short grace period isn't
        # necessary for a single-process, single-lifetime cache — leaving
        # it is bounded (one entry per key that was ever in flight) and
        # small relative to _MAX_ENTRIES.
