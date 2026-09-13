"""BTS Reporting Carrier On-Time Performance connector.

Static ZIP download, no auth, verified live with a stable URL pattern:
PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{Y}_{M}.zip

IMPORTANT (observed during implementation): BTS's server intermittently
resets the connection mid-download on files of this size (~10-28MB/month).
The retry wrapper in data/degradation.py is not optional here — a single
attempt without retry will periodically fail even though the source is
generally available.

IMPORTANT: delay-cause fields (CarrierDelay/WeatherDelay/NASDelay/
SecurityDelay/LateAircraftDelay) are only populated from June 2003 onward
and only for flights that were actually delayed — empty otherwise. This is
a dataset limitation, not a parsing bug, and must be reflected in coverage
notes rather than treated as missing/zero.

Each monthly file is downloaded once, filtered down to the requested origin
airport in memory, and only that filtered slice is cached — the full
national month is not retained after filtering, keeping the cache small and
per-query-scoped rather than a persisted nationwide dataset.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date

import httpx

from data.cache import cache_get, cache_set
from data.degradation import SourceUnavailable, with_retry

_URL_TEMPLATE = (
    "https://transtats.bts.gov/PREZIP/"
    "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{month}.zip"
)
_TIMEOUT = 8.0

_DELAY_CAUSE_FIELDS = {
    "carrier": "CarrierDelay",
    "weather": "WeatherDelay",
    "nas": "NASDelay",
    "security": "SecurityDelay",
    "late_aircraft": "LateAircraftDelay",
}


def _float_or_none(raw: str) -> float | None:
    raw = (raw or "").strip()
    if raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def get_on_time_records(airport_code: str, year: int, month: int) -> dict:
    """Returns on-time performance records where Origin == airport_code for
    the given year/month. Downloads the full monthly national file (BTS
    publishes no per-airport granularity) but filters and discards the
    unfiltered rows immediately — only the airport-scoped result is cached.
    """
    code = airport_code.strip().upper()
    cache_key = f"bts_on_time:{code}:{year}:{month}"
    cached = cache_get(cache_key)
    if cached is not None:
        result = dict(cached)
        result["cache_status"] = "hit"
        return result

    url = _URL_TEMPLATE.format(year=year, month=month)

    def _fetch():
        resp = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        return resp.content

    try:
        # 1 retry, short timeout: worst case ~2 x _TIMEOUT + backoff for one
        # call. This module previously used a 120s timeout with 2 retries
        # (up to 360s worst case for a single source) — that, not
        # concurrency, was the actual cause of multi-minute response times.
        zip_bytes = with_retry(_fetch, retries=0)
    except Exception as exc:
        raise SourceUnavailable(
            source_name="BTS Reporting Carrier On-Time Performance",
            detail=f"Failed to download {year}-{month:02d} on-time file "
                   f"after retries: {exc}",
        ) from exc

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            csv_name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
            with z.open(csv_name) as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
                flights = []
                for row in reader:
                    if row.get("Origin") != code:
                        continue
                    delay_causes = {
                        k: _float_or_none(row.get(field, ""))
                        for k, field in _DELAY_CAUSE_FIELDS.items()
                    }
                    flights.append({
                        "flight_date": row.get("FlightDate"),
                        "dest": row.get("Dest"),
                        "distance_miles": _float_or_none(row.get("Distance", "")),
                        "dep_delay_minutes": _float_or_none(row.get("DepDelayMinutes", "")),
                        "dep_del15": _float_or_none(row.get("DepDel15", "")),
                        "taxi_out_minutes": _float_or_none(row.get("TaxiOut", "")),
                        "cancelled": row.get("Cancelled") == "1.00",
                        "cancellation_code": row.get("CancellationCode") or None,
                        "delay_causes": delay_causes,
                    })
    except Exception as exc:
        raise SourceUnavailable(
            source_name="BTS Reporting Carrier On-Time Performance",
            detail=f"Failed to parse {year}-{month:02d} on-time file: {exc}",
        ) from exc

    result = {
        "airport_code": code,
        "year": year,
        "month": month,
        "flight_count": len(flights),
        "flights": flights,
        "delay_cause_coverage_note": (
            "Delay-cause fields are only populated for delayed flights from "
            "June 2003 onward; empty for on-time flights and for any data "
            "before that date."
        ),
        "source_name": "BTS Reporting Carrier On-Time Performance",
        "retrieved_at": date.today().isoformat(),
        "cache_status": "miss",
    }
    cache_set(cache_key, result, ttl_seconds=24 * 60 * 60)
    return result
