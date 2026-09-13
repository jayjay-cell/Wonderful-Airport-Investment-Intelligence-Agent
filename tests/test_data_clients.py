"""Live integration tests for the data/ layer. These hit real external
sources (no mocks) since the whole point is to verify the actual scraping/
parsing logic against the real government sites, per the plan's feasibility
requirements. Marked slow; skip in fast local iteration with
`pytest -m "not live"` if needed (marker registered below).
"""

import pytest

from data.cache import cache_clear
from data.clients import bts_on_time, bts_t100, faa_airports, faa_enplanements, faa_taf

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def _clear_cache():
    cache_clear()
    yield
    cache_clear()


def test_faa_airports_identity_lookup():
    airport, source = faa_airports.get_airport_identity("ANC")
    assert airport.code == "ANC"
    assert "ANCHORAGE" in airport.name.upper()
    assert airport.state == "AK"
    assert source.origin == "live"


def test_faa_airports_cache_hit_on_repeat():
    faa_airports.get_airport_identity("SFO")
    _, source2 = faa_airports.get_airport_identity("SFO")
    assert source2.origin == "cache"


def test_faa_enplanements_lookup():
    record = faa_enplanements.get_enplanements("SFO")
    assert record is not None
    assert record["code"] == "SFO"
    assert record["enplanements_current_year"] > 0


def test_faa_enplanements_hub_class_is_labeled_not_guessed():
    from core.models import HubClass
    record = faa_enplanements.get_enplanements("LAX")
    assert record["hub_class"] == HubClass.LARGE


def test_faa_taf_forecast_series():
    result = faa_taf.get_forecast_series("SFO")
    assert result["found"] is True
    assert len(result["enplanements"]) > 0
    forecast_rows = [r for r in result["enplanements"] if r["is_forecast"]]
    historical_rows = [r for r in result["enplanements"] if not r["is_forecast"]]
    assert len(forecast_rows) > 0
    assert len(historical_rows) > 0


def test_bts_on_time_records():
    result = bts_on_time.get_on_time_records("ANC", 2024, 1)
    assert result["flight_count"] > 0
    assert result["cache_status"] == "miss"
    result2 = bts_on_time.get_on_time_records("ANC", 2024, 1)
    assert result2["cache_status"] == "hit"


def test_bts_t100_routes_and_passenger_filter():
    result = bts_t100.get_annual_routes("ANC", 2024)
    assert result["route_count"] > 0

    adapted = bts_t100.adapt_for_long_haul_share(result["routes"])
    # Every adapted route must have come from a passenger-carrying record
    assert all(r["passengers"] > 0 for r in adapted)
    # Cargo-only routes must be excluded, not just zero-weighted
    assert len(adapted) < result["route_count"]


def test_bts_t100_cache_hit_on_repeat():
    bts_t100.get_annual_routes("MSY", 2024)
    result2 = bts_t100.get_annual_routes("MSY", 2024)
    assert result2["cache_status"] == "hit"
