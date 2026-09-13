"""BTS Reporting Carrier On-Time Performance connector.

Static ZIP download, no auth, verified live with a stable URL pattern:
PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{Y}_{M}.zip

IMPORTANT (observed during implementation): BTS's server intermittently
resets the connection mid-download on files of this size (~10-28MB/month).

IMPORTANT: delay-cause fields (CarrierDelay/WeatherDelay/NASDelay/
SecurityDelay/LateAircraftDelay) are only populated from June 2003 onward
and only for flights that were actually delayed — empty otherwise. This is
a dataset limitation, not a parsing bug, and must be reflected in coverage
notes rather than treated as missing/zero.

RESOURCE-ORIENTED CACHING (fixed root cause: this file is a NATIONAL
dataset for one year/month — every airport's data lives in the same
download. The cache key is therefore keyed by year/month only, not by
airport. The file is downloaded and parsed exactly once per year/month
(with single-flight protection via data.cache.get_or_load — concurrent
requests for the same period share one in-flight download rather than each
triggering their own), and the parsed result is an index of per-airport
AGGREGATES (not the raw per-flight record list — see _summarize_flights)
keyed by origin airport code. A comparison like "LAX vs SNA" or an
8-airport ranking for the same month now triggers exactly one national
download, not one per airport.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date

import httpx

from data.cache import cache_status, get_or_load
from data.degradation import SourceUnavailable, with_retry

_URL_TEMPLATE = (
    "https://transtats.bts.gov/PREZIP/"
    "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{month}.zip"
)
# This is a 10-28MB static download; an earlier pass set this too low (8s,
# zero retries), which risks the exact SOURCE_UNAVAILABLE regression found
# in bts_t100.py on a real connection. 20s + 1 retry gives real headroom.
# Only paid once per year/month resource regardless of airport count
# (single-flight).
_TIMEOUT = 20.0

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


def _resource_key(year: int, month: int) -> str:
    return f"bts_on_time:resource:{year}:{month}"


def _fetch_national_zip(year: int, month: int) -> bytes:
    url = _URL_TEMPLATE.format(year=year, month=month)

    def _fetch():
        resp = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        return resp.content

    try:
        return with_retry(_fetch, retries=1, backoff_seconds=0.5)
    except Exception as exc:
        raise SourceUnavailable(
            source_name="BTS Reporting Carrier On-Time Performance",
            detail=f"Failed to download {year}-{month:02d} on-time file: {exc}",
        ) from exc


def _summarize_flights_by_origin(zip_bytes: bytes, year: int, month: int) -> dict:
    """Parses the national monthly CSV ONCE and returns per-origin-airport
    AGGREGATE metrics only — not the raw per-flight record list. Retaining
    ~500K+ individual flight dicts nationwide in the process-lifetime cache
    would be significant, avoidable memory growth; every downstream
    consumer (core/classifications.py via agent/tools/airport_profile.py)
    only ever needed rates/medians/sums per airport, never individual
    flight rows.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            csv_name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
            with z.open(csv_name) as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))

                # Per-origin running aggregates, built in one pass.
                agg: dict[str, dict] = {}

                for row in reader:
                    origin = row.get("Origin")
                    if not origin:
                        continue
                    a = agg.setdefault(origin, {
                        "eligible": 0,
                        "delayed": 0,
                        "cancelled": 0,
                        "taxi_out_values": [],
                        "delay_causes": {"carrier": 0.0, "weather": 0.0, "nas": 0.0,
                                          "security": 0.0, "late_aircraft": 0.0},
                    })

                    a["eligible"] += 1

                    dep_del15 = _float_or_none(row.get("DepDel15", ""))
                    if dep_del15 and dep_del15 >= 1:
                        a["delayed"] += 1

                    if row.get("Cancelled") == "1.00":
                        a["cancelled"] += 1

                    taxi_out = _float_or_none(row.get("TaxiOut", ""))
                    if taxi_out is not None:
                        a["taxi_out_values"].append(taxi_out)

                    for cause_key, field in _DELAY_CAUSE_FIELDS.items():
                        v = _float_or_none(row.get(field, ""))
                        if v:
                            a["delay_causes"][cause_key] += v
    except Exception as exc:
        raise SourceUnavailable(
            source_name="BTS Reporting Carrier On-Time Performance",
            detail=f"Failed to parse {year}-{month:02d} on-time file: {exc}",
        ) from exc

    # Finalize: compute rates/medians once per airport (cheap, done at parse
    # time rather than on every lookup).
    index: dict[str, dict] = {}
    for origin, a in agg.items():
        eligible = a["eligible"]
        taxi_values = sorted(a["taxi_out_values"])
        median_taxi = taxi_values[len(taxi_values) // 2] if taxi_values else None
        index[origin] = {
            "airport_code": origin,
            "year": year,
            "month": month,
            "flight_count": eligible,
            "delay_rate": (a["delayed"] / eligible) if eligible else None,
            "cancellation_rate": (a["cancelled"] / eligible) if eligible else None,
            "median_taxi_out_minutes": median_taxi,
            "delay_causes": a["delay_causes"],
        }
    return index


def _load_national_index(year: int, month: int) -> dict[str, dict]:
    """Single-flight-protected: concurrent callers for the same year/month
    share one download+parse rather than each triggering their own."""
    def _load():
        zip_bytes = _fetch_national_zip(year, month)
        return _summarize_flights_by_origin(zip_bytes, year, month)

    return get_or_load(_resource_key(year, month), _load, ttl_seconds=24 * 60 * 60)


def get_on_time_records(airport_code: str, year: int, month: int) -> dict:
    """Returns on-time performance AGGREGATES for one airport for the given
    year/month. Internally shares one national download+parse per
    year/month across all callers (see module docstring) — this is a thin
    per-airport lookup into that shared index, not an independent fetch.
    """
    code = airport_code.strip().upper()
    # Checked BEFORE get_or_load, since that call will itself populate the
    # cache — this reflects whether the shared national resource was
    # already warm when this specific call arrived.
    resource_was_cached = cache_status(_resource_key(year, month)) == "hit"
    index = _load_national_index(year, month)
    cache_status_value = "hit" if resource_was_cached else "miss"

    record = index.get(code)
    if record is None:
        return {
            "airport_code": code,
            "year": year,
            "month": month,
            "flight_count": 0,
            "delay_rate": None,
            "cancellation_rate": None,
            "median_taxi_out_minutes": None,
            "delay_causes": None,
            "delay_cause_coverage_note": (
                "Delay-cause fields are only populated for delayed flights from "
                "June 2003 onward; empty for on-time flights and for any data "
                "before that date."
            ),
            "source_name": "BTS Reporting Carrier On-Time Performance",
            "retrieved_at": date.today().isoformat(),
            "note": f"No on-time records found for origin {code!r} in {year}-{month:02d}.",
            "cache_status": cache_status_value,
        }

    return {
        **record,
        "delay_cause_coverage_note": (
            "Delay-cause fields are only populated for delayed flights from "
            "June 2003 onward; empty for on-time flights and for any data "
            "before that date."
        ),
        "source_name": "BTS Reporting Carrier On-Time Performance",
        "retrieved_at": date.today().isoformat(),
        "cache_status": cache_status_value,
    }


def get_on_time_records_bulk(airport_codes: list[str], year: int, month: int) -> dict[str, dict]:
    """Multi-airport interface: one shared national download+parse serves
    every airport code requested. Use this (instead of N calls to
    get_on_time_records) whenever more than one airport is needed for the
    same period — e.g. compare_airports, rank_airports.
    """
    index = _load_national_index(year, month)
    results = {}
    for airport_code in airport_codes:
        code = airport_code.strip().upper()
        results[code] = get_on_time_records(code, year, month)
    return results
