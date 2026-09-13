"""BTS T-100 Segment/Market connector — live, on-demand, no pre-downloaded
snapshot and no airport-coverage restriction, since BTS publishes no
per-airport API for this dataset.

Notes on the underlying government form (transtats.bts.gov), none of which
is documented anywhere except by inspecting the page:
- The correct table is T-100 SEGMENT (has DEPARTURES_PERFORMED/SCHEDULED and
  SEATS), reached via gnoyr_VQ=FMG. The gnoyr_VQ=FMF page is T-100 MARKET
  (passengers/freight/mail only, no departures or seats) and must not be
  used for long-haul or capacity calculations.
- The outer <form id="form1"> posts back to itself (DL_SelectFields.aspx).
- The form's only origin-scoping mechanism is `cboGeography` (US state, or
  "All") plus `cboYear`/`cboPeriod` (month or "All" for the year) — there is
  no per-airport-code filter field. Using cboGeography="All" is what makes
  this a genuinely national download.
- The POST response body is the zip file directly (Content-Type:
  application/zip) — no separate "resolve a generated download link" step.
- The zip contains two files: "Documentation.csv" (a field glossary, must
  be skipped) and the actual data CSV.

Caching is resource-oriented, not per-airport: since cboGeography="All"
means every request already downloads the full national dataset for the
requested year/month, the cache key is that year/month resource, not the
airport. The national zip is downloaded and parsed exactly once per
year/month (single-flight via data.cache.get_or_load), producing an index
of routes keyed by origin airport; any number of airports for that period
are served from the one shared index. Assessing 8 airports for the same
year triggers one form submission/download, not eight.

Interface contract:
    get_routes(origin="ANC", year=2024, fields=[...])
    get_routes_bulk(origins=["ANC", "SFO", ...], year=2024)
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import date

import httpx

from data.cache import cache_status, get_or_load
from data.degradation import SourceUnavailable, with_retry

_FORM_URL = (
    "https://transtats.bts.gov/DL_SelectFields.aspx"
    "?gnoyr_VQ=FMG&QO_fu146_anzr=Nv4+Pn44vr45"
)
# The POST (form submission + zip download) reliably takes longer than the
# GET (which resolves in under 4s): a short timeout with no retry produces
# spurious SOURCE_UNAVAILABLE results on an otherwise healthy connection.
# 20s + 1 retry gives the POST step headroom without risking a multi-minute
# hang, and this cost is paid once per year/month resource regardless of
# how many airports ask (single-flight, see module docstring).
_TIMEOUT = 20.0

_SELECTED_FIELDS = [
    "ORIGIN", "DEST", "DISTANCE",
    "DEPARTURES_PERFORMED", "DEPARTURES_SCHEDULED",
    "PASSENGERS", "SEATS", "YEAR", "MONTH",
]


def _resource_key(year: int, month: int | str) -> str:
    return f"bts_t100:resource:{year}:{month}"


def _extract_hidden_field(html: str, name: str) -> str:
    match = re.search(rf'id="{name}" value="([^"]*)"', html)
    if not match:
        raise SourceUnavailable(
            source_name="BTS T-100 Segment",
            detail=f"Expected ASP.NET hidden field {name!r} not found on "
                   f"the TranStats form — the page structure may have "
                   f"changed.",
        )
    return match.group(1)


def _fetch_segment_zip(year: int, month: int | str = "All") -> bytes:
    session = httpx.Client(follow_redirects=True, timeout=_TIMEOUT)

    def _get_form():
        resp = session.get(_FORM_URL)
        resp.raise_for_status()
        return resp.text

    try:
        html = with_retry(_get_form, retries=1, backoff_seconds=0.5)
    except Exception as exc:
        raise SourceUnavailable(
            source_name="BTS T-100 Segment",
            detail=f"Failed to load the TranStats download form: {exc}",
        ) from exc

    form_data = {
        "__VIEWSTATE": _extract_hidden_field(html, "__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": _extract_hidden_field(html, "__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": _extract_hidden_field(html, "__EVENTVALIDATION"),
        "cboGeography": "All",
        "cboYear": str(year),
        "cboPeriod": str(month),
        "chkDownloadZip": "on",
        "btnDownload": "Download",
    }
    for field in _SELECTED_FIELDS:
        form_data[field] = "on"

    def _post_form():
        resp = session.post(_FORM_URL, data=form_data, headers={"Referer": _FORM_URL})
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "zip" not in content_type:
            raise ValueError(
                f"Expected a zip response but got content-type={content_type!r} "
                f"— the TranStats form workflow may have changed."
            )
        return resp.content

    try:
        return with_retry(_post_form, retries=1, backoff_seconds=0.5)
    except Exception as exc:
        raise SourceUnavailable(
            source_name="BTS T-100 Segment",
            detail=f"TranStats form submission failed for {year}-{month}: {exc}",
        ) from exc


def _index_by_origin(zip_bytes: bytes) -> dict[str, list[dict]]:
    """Parses the national zip ONCE and returns routes indexed by origin
    airport code — every origin present in the dataset, not just one."""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            data_name = next(n for n in z.namelist() if n != "Documentation.csv")
            with z.open(data_name) as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                index: dict[str, list[dict]] = {}
                for row in reader:
                    origin = row.get("ORIGIN")
                    if not origin:
                        continue
                    try:
                        route = {
                            "dest": row["DEST"],
                            "distance_miles": float(row["DISTANCE"]),
                            "departures_scheduled": float(row["DEPARTURES_SCHEDULED"]),
                            "departures_performed": float(row["DEPARTURES_PERFORMED"]),
                            "passengers": float(row["PASSENGERS"]),
                            "seats": float(row["SEATS"]),
                            "year": row["YEAR"],
                            "month": row["MONTH"],
                        }
                    except (KeyError, ValueError):
                        continue  # skip malformed row rather than failing the whole parse
                    index.setdefault(origin, []).append(route)
                return index
    except Exception as exc:
        raise SourceUnavailable(
            source_name="BTS T-100 Segment",
            detail=f"Failed to parse TranStats response zip: {exc}",
        ) from exc


def _load_national_index(year: int, month: int | str) -> dict[str, list[dict]]:
    """Single-flight-protected: concurrent callers for the same year/month
    share one form-submission+download+parse rather than each triggering
    their own."""
    def _load():
        zip_bytes = _fetch_segment_zip(year=year, month=month)
        return _index_by_origin(zip_bytes)

    return get_or_load(_resource_key(year, month), _load, ttl_seconds=24 * 60 * 60)


def get_routes(origin: str, year: int, month: int | str = "All") -> dict:
    """Returns route-level T-100 Segment records for one origin airport and
    year (optionally a single month). Internally shares one national
    download+parse per year/month across all callers (see module docstring)
    — this is a thin per-airport lookup into that shared index.
    """
    code = origin.strip().upper()
    # Checked BEFORE _load_national_index, which will itself populate the
    # cache — reflects whether the shared national resource was already
    # warm when this specific call arrived.
    resource_was_cached = cache_status(_resource_key(year, month)) == "hit"
    index = _load_national_index(year, month)
    routes = index.get(code, [])

    result = {
        "origin": code,
        "year": year,
        "month": month,
        "route_count": len(routes),
        "routes": routes,
        "source_name": "BTS T-100 Segment (transtats.bts.gov, live form query)",
        "retrieved_at": date.today().isoformat(),
        "cache_status": "hit" if resource_was_cached else "miss",
    }

    if not routes:
        result["note"] = (
            f"No T-100 Segment records found for origin {code!r} in "
            f"{year}-{month}. This may mean the airport had no scheduled "
            f"commercial service in this period, or the code is invalid."
        )

    return result


def get_routes_bulk(origins: list[str], year: int, month: int | str = "All") -> dict[str, dict]:
    """Multi-airport interface: one shared national download+parse serves
    every origin requested. Use this (instead of N calls to get_routes)
    whenever more than one airport is needed for the same period — e.g.
    rank_airports assessing a shortlist.
    """
    _load_national_index(year, month)  # ensures shared load happens once, up front
    return {origin.strip().upper(): get_routes(origin, year, month) for origin in origins}


def get_annual_routes(origin: str, year: int) -> dict:
    """Convenience wrapper: full-year route data for long-haul-share and
    annual demand calculations."""
    return get_routes(origin=origin, year=year, month="All")


def filter_passenger_routes(routes: list[dict]) -> list[dict]:
    """Excludes cargo-only route records (zero passengers, nonzero
    departures) before a long-haul-share or passenger-demand calculation.

    T-100 Segment includes all scheduled operations, not just passenger
    service. At a major cargo hub like ANC, cargo-only routes can account
    for roughly half of total departures — without this filter, a
    long-haul-share calculation silently mixes cargo and passenger
    operations, contradicting methodology.yaml's definition of "performed
    passenger departures" and materially changing the result (observed:
    13.75% vs. 45.07% long-haul share for ANC 2024, passenger-only vs.
    all-operations). This filter must always be applied for long-haul or
    passenger-demand questions. A separate, explicitly labeled
    all-operations view may be offered for flight-capacity/operations
    questions where cargo is relevant.
    """
    return [r for r in routes if r["passengers"] > 0]


def adapt_for_long_haul_share(routes: list[dict]) -> list[dict]:
    """Adapts this connector's route records to core.metrics.long_haul_share's
    generic {"distance_miles", "departures", "passengers"} contract, after
    filtering to passenger-carrying routes only (see filter_passenger_routes).
    """
    passenger_routes = filter_passenger_routes(routes)
    return [
        {
            "distance_miles": r["distance_miles"],
            "departures": r["departures_performed"],
            "passengers": r["passengers"],
        }
        for r in passenger_routes
    ]
