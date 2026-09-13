"""FAA Terminal Area Forecast (TAF) connector.

Static ZIP download, no auth, verified live. Internal format confirmed as
XLSX (not DBF). Contains per-airport, per-year enplanement and operations
data from historical actuals through a ~30-year forecast horizon.

IMPORTANT (verified during implementation): the `locid` column in the
underlying workbook has trailing whitespace padding (fixed-width-field
artifact, e.g. "SFO " not "SFO") — all lookups strip before comparing.

SHARED-DOWNLOAD FIX (root cause: passenger and operations forecasts each
independently downloaded the same ~15MB release zip — a cold airport
profile request downloaded it twice). Now there is exactly one release
loader (_load_release, single-flight-protected via data.cache.get_or_load)
that downloads the zip once and reads BOTH the Enplanements.xlsx and
AirportsOperations.xlsx sheets from that one downloaded archive, caching
both normalized tables together under one resource key.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date

import httpx
import openpyxl

from data.cache import get_or_load
from data.degradation import SourceUnavailable, with_retry

_URL = "https://taf.faa.gov/Downloads/APO100_TAF_Final_2025.zip"
_COVERAGE = "FY2025 Final TAF release (historical 1976 - forecast horizon)"
_TIMEOUT = 60.0  # ~15MB file; genuinely needs more headroom than the small
                 # JSON/XLSX sources, but no retry (see _load_release below)

_ENPLANEMENTS_FILE = "Enplanements.xlsx"
_OPERATIONS_FILE = "AirportsOperations.xlsx"

_RELEASE_CACHE_KEY = "faa_taf:release"

SCENARIO_HISTORICAL = 0
SCENARIO_FORECAST = 1


def _download_zip() -> bytes:
    def _fetch():
        resp = httpx.get(_URL, timeout=_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        return resp.content

    try:
        return with_retry(_fetch, retries=0)  # ~15MB file — a retry just doubles the wait
    except Exception as exc:
        raise SourceUnavailable(
            source_name="FAA Terminal Area Forecast (TAF)",
            detail=f"Failed to download TAF release zip: {exc}",
        ) from exc


def _load_sheet_from_zip(zip_bytes: bytes, filename: str) -> dict[str, list[dict]]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        content = z.read(filename)
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = ws.iter_rows(values_only=True)
    header = [str(h).strip() for h in next(rows)]

    by_airport: dict[str, list[dict]] = {}
    for row in rows:
        if not row or not row[0]:
            continue
        record = dict(zip(header, row))
        locid = str(record.get("locid", "")).strip().upper()
        if not locid:
            continue
        by_airport.setdefault(locid, []).append(record)
    return by_airport


def _load_release() -> dict[str, dict[str, list[dict]]]:
    """Downloads the TAF release zip exactly once (single-flight-protected
    for concurrent cold callers) and reads BOTH required sheets from that
    one archive, returning {"enplanements": {...}, "operations": {...}}.
    """
    def _load():
        zip_bytes = _download_zip()
        return {
            "enplanements": _load_sheet_from_zip(zip_bytes, _ENPLANEMENTS_FILE),
            "operations": _load_sheet_from_zip(zip_bytes, _OPERATIONS_FILE),
        }

    return get_or_load(_RELEASE_CACHE_KEY, _load, ttl_seconds=24 * 60 * 60)


def get_forecast_series(airport_code: str) -> dict:
    """Returns historical + forecast enplanement and operations series for
    one airport. Explicitly labeled as forecast, never observed demand, per
    the plan's requirement that TAF forecasts not be conflated with actuals.
    """
    code = airport_code.strip().upper()
    release = _load_release()
    enp_rows = release["enplanements"].get(code, [])
    ops_rows = release["operations"].get(code, [])

    if not enp_rows and not ops_rows:
        return {
            "airport_code": code,
            "found": False,
            "enplanements": [],
            "operations": [],
            "source": _COVERAGE,
        }

    enplanements = [
        {
            "year": r["ayear"],
            "is_forecast": r["scenario"] == SCENARIO_FORECAST,
            "air_carrier": r.get("aac"),
            "air_taxi": r.get("aat"),
            "commuter": r.get("commuter"),
        }
        for r in sorted(enp_rows, key=lambda r: r["ayear"])
    ]
    operations = [
        {
            "year": r["ayear"],
            "is_forecast": r["scenario"] == SCENARIO_FORECAST,
            "total_operations": r.get("tot_overs"),
        }
        for r in sorted(ops_rows, key=lambda r: r["ayear"])
    ]

    return {
        "airport_code": code,
        "found": True,
        "enplanements": enplanements,
        "operations": operations,
        "source": _COVERAGE,
    }


def source_metadata() -> dict:
    return {
        "source_name": "FAA Terminal Area Forecast (TAF)",
        "coverage_period": _COVERAGE,
        "retrieved_at": date.today().isoformat(),
    }
