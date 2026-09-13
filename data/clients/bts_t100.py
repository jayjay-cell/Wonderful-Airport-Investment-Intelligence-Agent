"""BTS T-100 Segment/Market connector — live, on-demand, no airport-coverage
restriction, per the plan's T-100 decision.

VERIFIED LIVE during implementation (feasibility proof, see plan Section 4):
- The correct table is T-100 SEGMENT (has DEPARTURES_PERFORMED/SCHEDULED and
  SEATS), reached via gnoyr_VQ=FMG. The gnoyr_VQ=FMF page is T-100 MARKET
  (passengers/freight/mail only, no departures or seats) and must not be
  used for long-haul or capacity calculations.
- The outer <form id="form1"> posts back to itself (DL_SelectFields.aspx),
  NOT to Search.asp (Search.asp is only the small sidebar site-search box
  and is a red herring in the page's markup).
- The form's only origin-scoping mechanism is `cboGeography` (US state, or
  "All") plus `cboYear`/`cboPeriod` (month or "All" for the year) — there is
  no direct per-airport-code filter field on this form. Using
  cboGeography="All" works and avoids needing to know an airport's state in
  advance; the response is filtered to the requested origin code in memory
  after download, and only that filtered slice is kept/cached.
- The POST response body IS the zip file directly (Content-Type:
  application/zip, Content-Disposition gives a real filename like
  T_T100_SEGMENT_ALL_CARRIER_<timestamp>.zip) — no separate "resolve a
  generated download link" step is needed for this table.
- The zip contains two files: "Documentation.csv" (a field glossary — must
  be skipped) and the actual data CSV.

Interface contract matches the plan's spec:
    get_routes(origin="ANC", year=2024, fields=[...])
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import date

import httpx

from data.cache import cache_get, cache_set
from data.degradation import SourceUnavailable, with_retry

_FORM_URL = (
    "https://transtats.bts.gov/DL_SelectFields.aspx"
    "?gnoyr_VQ=FMG&QO_fu146_anzr=Nv4+Pn44vr45"
)
_TIMEOUT = 8.0
# No retries anywhere in this module. Worst case per get_routes() call:
# GET + POST, one attempt each, 8s cap each = 16s max, ever.

_SELECTED_FIELDS = [
    "ORIGIN", "DEST", "DISTANCE",
    "DEPARTURES_PERFORMED", "DEPARTURES_SCHEDULED",
    "PASSENGERS", "SEATS", "YEAR", "MONTH",
]


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
        html = with_retry(_get_form, retries=0)
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
        return with_retry(_post_form, retries=0)
    except Exception as exc:
        raise SourceUnavailable(
            source_name="BTS T-100 Segment",
            detail=f"TranStats form submission failed for {year}-{month}: {exc}",
        ) from exc


def _parse_zip_for_origin(zip_bytes: bytes, origin: str) -> list[dict]:
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            data_name = next(n for n in z.namelist() if n != "Documentation.csv")
            with z.open(data_name) as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                return [row for row in reader if row.get("ORIGIN") == origin]
    except Exception as exc:
        raise SourceUnavailable(
            source_name="BTS T-100 Segment",
            detail=f"Failed to parse TranStats response zip: {exc}",
        ) from exc


def get_routes(origin: str, year: int, month: int | str = "All") -> dict:
    """Returns route-level T-100 Segment records for one origin airport and
    year (optionally a single month). Queries live and caches only the
    filtered, airport-scoped result — never persists the full national
    monthly/annual file.
    """
    code = origin.strip().upper()
    cache_key = f"bts_t100:{code}:{year}:{month}"
    cached = cache_get(cache_key)
    if cached is not None:
        result = dict(cached)
        result["cache_status"] = "hit"
        return result

    zip_bytes = _fetch_segment_zip(year=year, month=month)
    rows = _parse_zip_for_origin(zip_bytes, code)

    routes = []
    for row in rows:
        try:
            routes.append({
                "dest": row["DEST"],
                "distance_miles": float(row["DISTANCE"]),
                "departures_scheduled": float(row["DEPARTURES_SCHEDULED"]),
                "departures_performed": float(row["DEPARTURES_PERFORMED"]),
                "passengers": float(row["PASSENGERS"]),
                "seats": float(row["SEATS"]),
                "year": row["YEAR"],
                "month": row["MONTH"],
            })
        except (KeyError, ValueError):
            continue  # skip malformed row rather than failing the whole query

    result = {
        "origin": code,
        "year": year,
        "month": month,
        "route_count": len(routes),
        "routes": routes,
        "source_name": "BTS T-100 Segment (transtats.bts.gov, live form query)",
        "retrieved_at": date.today().isoformat(),
        "cache_status": "miss",
    }

    if not routes:
        result["note"] = (
            f"No T-100 Segment records found for origin {code!r} in "
            f"{year}-{month}. This may mean the airport had no scheduled "
            f"commercial service in this period, or the code is invalid."
        )

    cache_set(cache_key, result, ttl_seconds=24 * 60 * 60)
    return result


def get_annual_routes(origin: str, year: int) -> dict:
    """Convenience wrapper: full-year route data for long-haul-share and
    annual demand calculations."""
    return get_routes(origin=origin, year=year, month="All")


def filter_passenger_routes(routes: list[dict]) -> list[dict]:
    """Excludes cargo-only route records (zero passengers, nonzero
    departures) before a long-haul-share or passenger-demand calculation.

    IMPORTANT (discovered during implementation, verified against live ANC
    2024 data): T-100 Segment includes ALL scheduled operations, not just
    passenger service. At ANC specifically, cargo-only routes account for
    ~55% of total departures (51,102 of 93,654 in 2024) — ANC is a major
    cargo hub. Without this filter, a long-haul-share calculation silently
    mixes cargo and passenger operations, which contradicts
    methodology.yaml's long_haul_basis_default definition of "scheduled
    PASSENGER departures." This is not a hypothetical edge case; it
    materially changes the answer (13.75% vs 45.07% long-haul share for
    ANC 2024 in the passenger-only vs. all-operations calculation observed
    during testing) and must always be applied for long-haul/passenger
    demand questions. A separate, explicit all-operations (cargo included)
    view may be offered for flight-capacity/operations-focused questions,
    but must be clearly labeled as including cargo when used.
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
