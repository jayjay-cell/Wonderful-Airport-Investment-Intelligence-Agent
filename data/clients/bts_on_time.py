"""BTS Reporting Carrier On-Time Performance connector.

Static ZIP download, no auth, verified live with a stable URL pattern:
PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{Y}_{M}.zip
-- but there is no per-year file, only one ZIP PER MONTH.

Every delay/cancellation/taxi-out figure is a full-year aggregate, not a
single month presented under a year label: this connector downloads and
merges all 12 months of the requested year before returning a result.

COST: a real, deliberate tradeoff. A cold year costs roughly 12x a single
month's download (BTS monthly files run 10-30MB each, so up to ~300MB
total), instead of one ~20MB file. This happens once per year, ever, for
the life of the process -- the result is cached (data.cache.get_or_load,
single-flight-protected) and every airport, comparison, and ranking for
that year is then served from the in-memory aggregate with no further
network cost. This mirrors the resource-oriented caching used for BTS
T-100 and FAA TAF (whole-national-resource, cached once, shared by every
caller) -- the year is the resource here, not the month.

Months are fetched CONCURRENTLY (a thread pool) rather than sequentially,
so the wall-clock cost of a cold year is closer to "however long the
slowest single month takes" than "12x that."

IMPORTANT: delay-cause fields (CarrierDelay/WeatherDelay/NASDelay/
SecurityDelay/LateAircraftDelay) are only populated from June 2003 onward
and only for flights that were actually delayed -- empty otherwise. This is
a dataset limitation, not a parsing bug.

A single missing/failed month does not fail the whole year: it is recorded
and the annual aggregate is built from whichever months succeeded, with the
gap stated explicitly rather than silently treated as zero flights for that
month.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import httpx

from data.cache import cache_status, get_or_load
from data.degradation import SourceUnavailable, with_retry

logger = logging.getLogger("data.clients.bts_on_time")

_URL_TEMPLATE = (
    "https://transtats.bts.gov/PREZIP/"
    "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{month}.zip"
)
# 10-30MB per month; BTS's server intermittently resets the connection
# mid-download on files of this size. 20s + 1 retry per month.
_TIMEOUT = 20.0
_MONTH_FETCH_CONCURRENCY = 6  # bounded so this doesn't open 12 simultaneous multi-MB downloads

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


def _resource_key(year: int) -> str:
    return f"bts_on_time:resource:{year}"


def _fetch_month_zip(year: int, month: int) -> bytes:
    url = _URL_TEMPLATE.format(year=year, month=month)

    def _fetch():
        resp = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        return resp.content

    return with_retry(_fetch, retries=1, backoff_seconds=0.5)


def _accumulate_month(zip_bytes: bytes, agg: dict[str, dict]) -> None:
    """Parses one month's CSV and adds its rows into the running per-origin
    aggregate dict IN PLACE -- this is what lets 12 months merge into one
    annual total without holding 12 separate indexes in memory at once."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        csv_name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        with z.open(csv_name) as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
            for row in reader:
                origin = row.get("Origin")
                if not origin:
                    continue
                a = agg.setdefault(origin, {
                    "eligible": 0, "delayed": 0, "cancelled": 0, "taxi_out_values": [],
                    "delay_causes": {"carrier": 0.0, "weather": 0.0, "nas": 0.0, "security": 0.0, "late_aircraft": 0.0},
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


def _build_annual_index(year: int) -> dict:
    """Fetches all 12 months concurrently, merges them into one per-origin
    annual aggregate. A month that fails to download/parse is recorded in
    months_failed and excluded from the aggregate -- the year is still
    served from whatever months succeeded, with that gap stated explicitly
    rather than silently counted as zero flights."""
    agg: dict[str, dict] = {}
    months_failed: list[int] = []
    months_ok: list[int] = []

    def _fetch_one(month: int):
        try:
            return month, _fetch_month_zip(year, month)
        except Exception as exc:
            logger.warning("bts_on_time: month %s-%02d failed to download (%s)", year, month, exc)
            return month, None

    with ThreadPoolExecutor(max_workers=_MONTH_FETCH_CONCURRENCY) as pool:
        for month, zip_bytes in pool.map(_fetch_one, range(1, 13)):
            if zip_bytes is None:
                months_failed.append(month)
                continue
            try:
                _accumulate_month(zip_bytes, agg)
                months_ok.append(month)
            except Exception as exc:
                logger.warning("bts_on_time: month %s-%02d failed to parse (%s)", year, month, exc)
                months_failed.append(month)

    if not months_ok:
        raise SourceUnavailable(
            source_name="BTS Reporting Carrier On-Time Performance",
            detail=f"All 12 months failed to download/parse for {year} -- no annual data available.",
        )

    index: dict[str, dict] = {}
    for origin, a in agg.items():
        eligible = a["eligible"]
        taxi_values = sorted(a["taxi_out_values"])
        median_taxi = taxi_values[len(taxi_values) // 2] if taxi_values else None
        index[origin] = {
            "airport_code": origin,
            "year": year,
            "months_included": sorted(months_ok),
            "months_failed": sorted(months_failed),
            "flight_count": eligible,
            "delay_rate": (a["delayed"] / eligible) if eligible else None,
            "cancellation_rate": (a["cancelled"] / eligible) if eligible else None,
            "median_taxi_out_minutes": median_taxi,
            "delay_causes": a["delay_causes"],
        }
    return index


def _load_national_index(year: int) -> dict[str, dict]:
    """Single-flight-protected: concurrent callers for the same year share
    one fetch-all-12-months+merge rather than each triggering their own."""
    return get_or_load(_resource_key(year), lambda: _build_annual_index(year), ttl_seconds=24 * 60 * 60)


def get_on_time_records(airport_code: str, year: int) -> dict:
    """Returns on-time performance AGGREGATES for one airport for the FULL
    given year (all 12 months merged). Internally shares one national
    fetch+parse per year across all callers -- this is a thin per-airport
    lookup into that shared annual index, not an independent fetch."""
    code = airport_code.strip().upper()
    resource_was_cached = cache_status(_resource_key(year)) == "hit"
    index = _load_national_index(year)
    cache_status_value = "hit" if resource_was_cached else "miss"

    record = index.get(code)
    if record is None:
        return {
            "airport_code": code, "year": year, "months_included": [], "months_failed": list(range(1, 13)),
            "flight_count": 0, "delay_rate": None, "cancellation_rate": None,
            "median_taxi_out_minutes": None, "delay_causes": None,
            "delay_cause_coverage_note": "Delay-cause fields are only populated for delayed flights from June 2003 onward.",
            "source_name": "BTS Reporting Carrier On-Time Performance",
            "retrieved_at": date.today().isoformat(),
            "note": f"No on-time records found for origin {code!r} in {year}.",
            "cache_status": cache_status_value,
        }

    coverage_note = f"Covers {len(record['months_included'])}/12 months of {year}."
    if record["months_failed"]:
        coverage_note += f" Months {record['months_failed']} could not be retrieved and are excluded from these figures."

    return {
        **record,
        "coverage_note": coverage_note,
        "delay_cause_coverage_note": "Delay-cause fields are only populated for delayed flights from June 2003 onward.",
        "source_name": "BTS Reporting Carrier On-Time Performance",
        "retrieved_at": date.today().isoformat(),
        "cache_status": cache_status_value,
    }


def get_on_time_records_bulk(airport_codes: list[str], year: int) -> dict[str, dict]:
    """Multi-airport interface: one shared annual fetch+merge serves every
    airport code requested. Use this instead of N calls to
    get_on_time_records whenever more than one airport is needed for the
    same year -- e.g. compare_airports, rank_airports."""
    _load_national_index(year)  # ensure the shared annual load happens once, up front
    return {code.strip().upper(): get_on_time_records(code, year) for code in airport_codes}
