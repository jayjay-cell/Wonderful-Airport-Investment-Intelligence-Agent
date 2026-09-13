"""Proves the resource-oriented caching fixes: concurrent requests for the
same national BTS/FAA resource trigger exactly one download+parse, not one
per caller/airport. All network calls are mocked — these tests must not
touch the real internet.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from data.cache import cache_clear, get_or_load


@pytest.fixture(autouse=True)
def _clear_cache():
    cache_clear()
    yield
    cache_clear()


# ---------------------------------------------------------------------------
# Generic single-flight primitive (data/cache.py)
# ---------------------------------------------------------------------------

def test_get_or_load_concurrent_callers_share_one_load():
    """Only the single-flight LEADER ever calls the loader; every other
    concurrent caller blocks on the leader's result instead. A slow loader
    with a short artificial delay (rather than a barrier all threads must
    reach) is what actually proves 9 other threads arrive while the leader
    is still working and correctly wait rather than each calling loader().
    """
    call_count = {"n": 0}
    started = threading.Event()

    def slow_loader():
        call_count["n"] += 1
        started.set()
        time.sleep(0.3)  # gives the other 9 threads time to arrive and block
        return "shared-result"

    def worker():
        return get_or_load("shared-key", slow_loader)

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda _: worker(), range(10)))

    assert call_count["n"] == 1, "loader must run exactly once for 10 concurrent callers"
    assert all(r == "shared-result" for r in results)


def test_get_or_load_second_call_after_completion_uses_cache_not_reload():
    call_count = {"n": 0}

    def loader():
        call_count["n"] += 1
        return "value"

    first = get_or_load("k", loader)
    second = get_or_load("k", loader)

    assert first == second == "value"
    assert call_count["n"] == 1


def test_get_or_load_propagates_exception_to_all_waiters():
    def failing_loader():
        raise ValueError("boom")

    def worker():
        return get_or_load("failing-key", failing_loader)

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(worker) for _ in range(5)]
        results = []
        for f in futures:
            try:
                f.result()
                results.append("ok")
            except ValueError as e:
                results.append(str(e))

    assert all(r == "boom" for r in results)


# ---------------------------------------------------------------------------
# BTS On-Time: shared national download across airports/concurrent callers
# ---------------------------------------------------------------------------

_FAKE_ONTIME_CSV = (
    "Origin,Dest,DepDel15,Cancelled,TaxiOut,CarrierDelay,WeatherDelay,"
    "NASDelay,SecurityDelay,LateAircraftDelay\n"
    "LAX,JFK,0.00,0.00,15.00,,,,,\n"
    "LAX,ORD,1.00,0.00,20.00,10,0,5,0,0\n"
    "SNA,LAX,0.00,1.00,12.00,,,,,\n"
)


def _make_fake_ontime_zip() -> bytes:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("data.csv", _FAKE_ONTIME_CSV)
    return buf.getvalue()


def test_bts_on_time_concurrent_requests_same_month_one_download():
    from data.clients import bts_on_time

    fetch_count = {"n": 0}

    def fake_get(*args, **kwargs):
        fetch_count["n"] += 1
        resp = type("R", (), {})()
        resp.content = _make_fake_ontime_zip()
        resp.raise_for_status = lambda: None
        return resp

    with patch("httpx.get", side_effect=fake_get):
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(
                lambda code: bts_on_time.get_on_time_records(code, 2024, 1),
                ["LAX", "SNA", "LAX", "SNA", "LAX"],
            ))

    assert fetch_count["n"] == 1, "5 concurrent lookups (mixed airports) for the same month must trigger exactly 1 download"


def test_bts_on_time_lax_and_sna_comparison_shares_one_resource_load():
    """Directly models the LAX vs SNA comparison scenario named in the spec."""
    from data.clients import bts_on_time

    fetch_count = {"n": 0}

    def fake_get(*args, **kwargs):
        fetch_count["n"] += 1
        resp = type("R", (), {})()
        resp.content = _make_fake_ontime_zip()
        resp.raise_for_status = lambda: None
        return resp

    with patch("httpx.get", side_effect=fake_get):
        results = bts_on_time.get_on_time_records_bulk(["LAX", "SNA"], 2024, 1)

    assert fetch_count["n"] == 1
    assert results["LAX"]["flight_count"] == 2
    assert results["SNA"]["flight_count"] == 1
    # LAX has one delayed flight (DepDel15=1.00) out of 2
    assert results["LAX"]["delay_rate"] == 0.5
    # SNA has one cancelled flight out of 1
    assert results["SNA"]["cancellation_rate"] == 1.0


def test_bts_on_time_returns_aggregates_not_raw_flight_list():
    """Confirms the client no longer retains per-flight records — only
    aggregated metrics — per the memory-growth requirement."""
    from data.clients import bts_on_time

    def fake_get(*args, **kwargs):
        resp = type("R", (), {})()
        resp.content = _make_fake_ontime_zip()
        resp.raise_for_status = lambda: None
        return resp

    with patch("httpx.get", side_effect=fake_get):
        result = bts_on_time.get_on_time_records("LAX", 2024, 1)

    assert "flights" not in result
    assert "delay_rate" in result
    assert "cancellation_rate" in result
    assert "median_taxi_out_minutes" in result


# ---------------------------------------------------------------------------
# BTS T-100: shared national download across airports/concurrent callers
# ---------------------------------------------------------------------------

_FAKE_T100_CSV = (
    "ORIGIN,DEST,DISTANCE,DEPARTURES_SCHEDULED,DEPARTURES_PERFORMED,"
    "PASSENGERS,SEATS,YEAR,MONTH\n"
    "ANC,SEA,1448,100,98,15000,18000,2024,1\n"
    "ANC,ORD,2846,10,10,1500,1800,2024,1\n"
    "ANC,ANC,0,5,5,0,0,2024,1\n"  # cargo/ferry — zero passengers
    "SFO,LAX,337,500,490,80000,90000,2024,1\n"
)


def _make_fake_t100_zip() -> bytes:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Documentation.csv", "field,desc\nORIGIN,origin airport\n")
        z.writestr("T_T100_SEGMENT_ALL_CARRIER.csv", _FAKE_T100_CSV)
    return buf.getvalue()


def _fake_t100_form_html() -> str:
    return (
        '<input id="__VIEWSTATE" value="vs" />'
        '<input id="__VIEWSTATEGENERATOR" value="vsg" />'
        '<input id="__EVENTVALIDATION" value="ev" />'
    )


def test_bts_t100_concurrent_requests_same_year_one_form_submission():
    from data.clients import bts_t100

    get_count = {"n": 0}
    post_count = {"n": 0}

    class FakeResp:
        def __init__(self, text=None, content=None, content_type="text/html"):
            self.text = text
            self.content = content
            self.headers = {"content-type": content_type}

        def raise_for_status(self):
            pass

    class FakeSession:
        def __init__(self, *a, **kw):
            pass

        def get(self, *a, **kw):
            get_count["n"] += 1
            return FakeResp(text=_fake_t100_form_html())

        def post(self, *a, **kw):
            post_count["n"] += 1
            return FakeResp(content=_make_fake_t100_zip(), content_type="application/zip")

    with patch("httpx.Client", FakeSession):
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(
                lambda code: bts_t100.get_annual_routes(code, 2024),
                ["ANC", "SFO", "ANC", "SFO", "ANC"],
            ))

    assert get_count["n"] == 1, "concurrent lookups for the same year must trigger exactly 1 form GET"
    assert post_count["n"] == 1, "concurrent lookups for the same year must trigger exactly 1 form POST/download"


def test_bts_t100_different_airports_get_correct_distinct_results():
    """Confirms the shared-index refactor still returns CORRECT
    airport-specific data, not just deduplicated calls."""
    from data.clients import bts_t100

    class FakeResp:
        def __init__(self, text=None, content=None, content_type="text/html"):
            self.text = text
            self.content = content
            self.headers = {"content-type": content_type}

        def raise_for_status(self):
            pass

    class FakeSession:
        def __init__(self, *a, **kw):
            pass

        def get(self, *a, **kw):
            return FakeResp(text=_fake_t100_form_html())

        def post(self, *a, **kw):
            return FakeResp(content=_make_fake_t100_zip(), content_type="application/zip")

    with patch("httpx.Client", FakeSession):
        anc = bts_t100.get_annual_routes("ANC", 2024)
        sfo = bts_t100.get_annual_routes("SFO", 2024)

    assert anc["route_count"] == 3  # SEA, ORD, and the ANC->ANC cargo/ferry row
    assert sfo["route_count"] == 1
    assert {r["dest"] for r in anc["routes"]} == {"SEA", "ORD", "ANC"}
    assert sfo["routes"][0]["dest"] == "LAX"

    # Cargo/passenger distinction must survive the refactor (ANC->ANC has 0 passengers)
    anc_passenger_routes = bts_t100.filter_passenger_routes(anc["routes"])
    assert len(anc_passenger_routes) == 2
    assert all(r["passengers"] > 0 for r in anc_passenger_routes)


def test_bts_t100_bulk_interface_one_download_for_multiple_airports():
    from data.clients import bts_t100

    post_count = {"n": 0}

    class FakeResp:
        def __init__(self, text=None, content=None, content_type="text/html"):
            self.text = text
            self.content = content
            self.headers = {"content-type": content_type}

        def raise_for_status(self):
            pass

    class FakeSession:
        def __init__(self, *a, **kw):
            pass

        def get(self, *a, **kw):
            return FakeResp(text=_fake_t100_form_html())

        def post(self, *a, **kw):
            post_count["n"] += 1
            return FakeResp(content=_make_fake_t100_zip(), content_type="application/zip")

    with patch("httpx.Client", FakeSession):
        results = bts_t100.get_routes_bulk(["ANC", "SFO"], 2024)

    assert post_count["n"] == 1
    assert results["ANC"]["route_count"] == 3
    assert results["SFO"]["route_count"] == 1


# ---------------------------------------------------------------------------
# FAA TAF: shared release download for enplanements + operations
# ---------------------------------------------------------------------------

def _make_fake_taf_zip() -> bytes:
    import io
    import zipfile
    import openpyxl

    enp_wb = openpyxl.Workbook()
    enp_ws = enp_wb.active
    enp_ws.append(["locid", "scenario", "ayear", "aac", "aat", "commuter"])
    enp_ws.append(["SFO ", 0, 2023, 1000000, 100, 5000])
    enp_ws.append(["SFO ", 1, 2024, 1050000, 105, 5100])
    enp_buf = io.BytesIO()
    enp_wb.save(enp_buf)

    ops_wb = openpyxl.Workbook()
    ops_ws = ops_wb.active
    ops_ws.append(["locid", "scenario", "ayear", "tot_overs"])
    ops_ws.append(["SFO ", 0, 2023, 300000])
    ops_ws.append(["SFO ", 1, 2024, 310000])
    ops_buf = io.BytesIO()
    ops_wb.save(ops_buf)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Enplanements.xlsx", enp_buf.getvalue())
        z.writestr("AirportsOperations.xlsx", ops_buf.getvalue())
    return buf.getvalue()


def test_faa_taf_passenger_and_operations_share_one_download():
    from data.clients import faa_taf

    fetch_count = {"n": 0}

    def fake_get(*args, **kwargs):
        fetch_count["n"] += 1
        resp = type("R", (), {})()
        resp.content = _make_fake_taf_zip()
        resp.raise_for_status = lambda: None
        return resp

    with patch("httpx.get", side_effect=fake_get):
        result = faa_taf.get_forecast_series("SFO")

    assert fetch_count["n"] == 1, "one get_forecast_series call must download the release zip exactly once for both sheets"
    assert result["found"] is True
    assert len(result["enplanements"]) == 2
    assert len(result["operations"]) == 2


def test_faa_taf_concurrent_different_airports_one_download():
    from data.clients import faa_taf

    fetch_count = {"n": 0}

    def fake_get(*args, **kwargs):
        fetch_count["n"] += 1
        resp = type("R", (), {})()
        resp.content = _make_fake_taf_zip()
        resp.raise_for_status = lambda: None
        return resp

    with patch("httpx.get", side_effect=fake_get):
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(lambda code: faa_taf.get_forecast_series(code), ["SFO", "LAX", "SFO", "ORD"]))

    assert fetch_count["n"] == 1
