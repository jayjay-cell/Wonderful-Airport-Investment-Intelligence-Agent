"""FAA Terminal Area Forecast (TAF) connector.

Static ZIP download, no auth, verified live. Internal format confirmed as
XLSX (not DBF). Contains per-airport, per-year enplanement and operations
data from historical actuals through a ~30-year forecast horizon.

IMPORTANT (verified during implementation): the `locid` column in the
underlying workbook has trailing whitespace padding (fixed-width-field
artifact, e.g. "SFO " not "SFO") — all lookups strip before comparing.

Whole file is downloaded once and cached in memory (~15MB, one-time annual
release) — this is a small reference dataset, not the per-flight-record
"nationwide dataset" restriction that applies specifically to T-100.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date

import httpx
import openpyxl

from data.cache import cache_get, cache_set
from data.degradation import SourceUnavailable, with_retry

_URL = "https://taf.faa.gov/Downloads/APO100_TAF_Final_2025.zip"
_COVERAGE = "FY2025 Final TAF release (historical 1976 - forecast horizon)"
_TIMEOUT = 60.0

_ENPLANEMENTS_FILE = "Enplanements.xlsx"
_OPERATIONS_FILE = "AirportsOperations.xlsx"

_ENPLANEMENTS_CACHE_KEY = "faa_taf:enplanements"
_OPERATIONS_CACHE_KEY = "faa_taf:operations"

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


def _load_sheet(zip_bytes: bytes, filename: str, value_cols: list[str]) -> dict[str, list[dict]]:
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


def _get_enplanements_table() -> dict[str, list[dict]]:
    cached = cache_get(_ENPLANEMENTS_CACHE_KEY)
    if cached is not None:
        return cached
    zip_bytes = _download_zip()
    table = _load_sheet(zip_bytes, _ENPLANEMENTS_FILE, [])
    cache_set(_ENPLANEMENTS_CACHE_KEY, table, ttl_seconds=24 * 60 * 60)
    return table


def _get_operations_table() -> dict[str, list[dict]]:
    cached = cache_get(_OPERATIONS_CACHE_KEY)
    if cached is not None:
        return cached
    zip_bytes = _download_zip()
    table = _load_sheet(zip_bytes, _OPERATIONS_FILE, [])
    cache_set(_OPERATIONS_CACHE_KEY, table, ttl_seconds=24 * 60 * 60)
    return table


def get_forecast_series(airport_code: str) -> dict:
    """Returns historical + forecast enplanement and operations series for
    one airport. Explicitly labeled as forecast, never observed demand, per
    the plan's requirement that TAF forecasts not be conflated with actuals.
    """
    code = airport_code.strip().upper()
    enp_rows = _get_enplanements_table().get(code, [])
    ops_rows = _get_operations_table().get(code, [])

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
