"""Live integration tests for the agent/tools/ layer — the wiring between
data/ and core/. Hits real external sources, same as test_data_clients.py.
"""

import pytest

from agent.tools.airport_profile import get_airport_profile
from agent.tools.assess_opportunity import assess_airport_opportunity
from agent.tools.compare_airports import compare_airports
from agent.tools.find_airports import find_airports
from agent.tools.long_haul import calculate_long_haul_share
from data.cache import cache_clear

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def _clear_cache():
    cache_clear()
    yield
    cache_clear()


def test_find_airports_new_england_returns_only_commercial_service():
    result = find_airports(region="New England")
    assert "error" not in result
    assert result["count"] > 0
    codes = {a.code for a in result["airports"]}
    assert "BOS" in codes
    assert "BDL" in codes
    # small GA fields with negligible enplanements must be excluded
    assert "SNC" not in codes  # Chester CT, ~4 enplanements/year


def test_find_airports_unknown_region_returns_ambiguous_error():
    result = find_airports(region="Narnia")
    assert result.get("error") == "AMBIGUOUS_QUERY"


def test_get_airport_profile_anc():
    result = get_airport_profile("ANC")
    assert "error" not in result
    profile = result["profile"]
    assert profile.airport.code == "ANC"
    assert profile.passengers.value is not None


def test_get_airport_profile_invalid_code():
    result = get_airport_profile("ZZZZ")
    assert result.get("error") == "INVALID_AIRPORT"


def test_calculate_long_haul_share_anc():
    result = calculate_long_haul_share("ANC")
    assert "error" not in result
    assert 0 <= result["percentage"] <= 1
    assert result["numerator"] <= result["denominator"]
    assert result["threshold_miles"] == 1500
    assert result["basis"] == "departures"


def test_compare_airports_lax_sna():
    result = compare_airports(["LAX", "SNA"])
    assert "error" not in result
    assert len(result["airports"]) == 2
    codes = {a["airport_code"] for a in result["airports"]}
    assert codes == {"LAX", "SNA"}
    for a in result["airports"]:
        assert a["congestion_level"] in ("High", "Medium", "Low")


def test_compare_airports_requires_two():
    result = compare_airports(["LAX"])
    assert result.get("error") == "AMBIGUOUS_QUERY"


def test_assess_airport_opportunity_sfo():
    result = assess_airport_opportunity("SFO", investment_focus="general_modernization")
    assert "error" not in result
    assessment = result["assessment"]
    assert assessment.airport.code == "SFO"
    assert assessment.final_classification is not None
    assert assessment.confidence is not None


def test_assess_airport_opportunity_invalid_focus():
    result = assess_airport_opportunity("SFO", investment_focus="not_a_real_focus")
    assert result.get("error") == "AMBIGUOUS_QUERY"
