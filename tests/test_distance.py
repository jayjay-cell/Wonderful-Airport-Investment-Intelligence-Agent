"""Tests for great-circle distance + find_nearby_airports.

Verifies the calculation against known real-world airport separations so a
regression in the math is caught, not just "it returned a number".
"""

from __future__ import annotations

from unittest.mock import patch

from core.metrics import great_circle_distance_miles

# Real FAA-published coordinates
JFK = (40.63992805, -73.77869222)
LGA = (40.77724, -73.87261)
EWR = (40.6925, -74.168667)
LAX = (33.94254, -118.40807)


def test_distance_jfk_to_lga_matches_known_separation():
    d = great_circle_distance_miles(*JFK, *LGA)
    # JFK-LGA is ~10 miles straight line
    assert 9.0 < d < 12.0, f"expected ~10mi, got {d}"


def test_distance_jfk_to_ewr_matches_known_separation():
    d = great_circle_distance_miles(*JFK, *EWR)
    # JFK-EWR is ~21 miles straight line
    assert 19.0 < d < 23.0, f"expected ~21mi, got {d}"


def test_distance_jfk_to_lax_matches_known_transcon():
    d = great_circle_distance_miles(*JFK, *LAX)
    # JFK-LAX great circle is ~2145 miles (the published airway distance
    # commonly cited is ~2475 statute mi driving / 2145 nm-ish great circle
    # in statute ~2469). Use a wide but meaningful band.
    assert 2400 < d < 2500, f"expected ~2470mi, got {d}"


def test_distance_is_symmetric():
    assert abs(
        great_circle_distance_miles(*JFK, *LAX) - great_circle_distance_miles(*LAX, *JFK)
    ) < 0.001


def test_distance_to_self_is_zero():
    assert great_circle_distance_miles(*JFK, *JFK) < 0.001


# ---------------------------------------------------------------------------
# find_nearby_airports tool
# ---------------------------------------------------------------------------

def _fake_bbox_response():
    return {
        "features": [
            {"attributes": {"ARPT_ID": "JFK", "ARPT_NAME": "JOHN F KENNEDY INTL",
                             "CITY": "NEW YORK", "STATE_CODE": "NY",
                             "LAT_DECIMAL": JFK[0], "LONG_DECIMAL": JFK[1]}},
            {"attributes": {"ARPT_ID": "LGA", "ARPT_NAME": "LAGUARDIA",
                             "CITY": "NEW YORK", "STATE_CODE": "NY",
                             "LAT_DECIMAL": LGA[0], "LONG_DECIMAL": LGA[1]}},
            {"attributes": {"ARPT_ID": "EWR", "ARPT_NAME": "NEWARK LIBERTY INTL",
                             "CITY": "NEWARK", "STATE_CODE": "NJ",
                             "LAT_DECIMAL": EWR[0], "LONG_DECIMAL": EWR[1]}},
            {"attributes": {"ARPT_ID": "XYZ", "ARPT_NAME": "TINY GRASS STRIP",
                             "CITY": "NOWHERE", "STATE_CODE": "NY",
                             "LAT_DECIMAL": 40.70, "LONG_DECIMAL": -73.80}},
        ]
    }


def test_find_nearby_airports_returns_sorted_real_distances():
    from core.models import Airport, SourceRecord
    from datetime import date
    from agent.tools.nearby_airports import find_nearby_airports
    from data.cache import cache_clear

    cache_clear()

    jfk_airport = Airport(code="JFK", name="JOHN F KENNEDY INTL", city="NEW YORK",
                           state="NY", latitude=JFK[0], longitude=JFK[1])
    source = SourceRecord(source_name="FAA", retrieved_at=date.today(),
                           coverage_period="current", origin="live")

    def fake_enp(code):
        # LGA/EWR are real commercial airports; XYZ is not
        if code in ("LGA", "EWR"):
            return {"enplanements_current_year": 15_000_000}
        return None

    class FakeResp:
        def json(self):
            return _fake_bbox_response()

        def raise_for_status(self):
            pass

    with patch("data.clients.faa_airports.get_airport_identity", return_value=(jfk_airport, source)), \
         patch("data.clients.faa_enplanements.get_enplanements", side_effect=fake_enp), \
         patch("httpx.get", return_value=FakeResp()):
        result = find_nearby_airports("JFK", limit=5)

    assert "error" not in result
    codes = [a["airport_code"] for a in result["nearby_airports"]]

    # The grass strip must be filtered out by commercial_only
    assert "XYZ" not in codes
    # LGA is closer to JFK than EWR — order must reflect real computed distance
    assert codes == ["LGA", "EWR"], codes
    # JFK must not appear in its own nearby list
    assert "JFK" not in codes

    lga = result["nearby_airports"][0]
    assert 9.0 < lga["distance_miles"] < 12.0
    assert "straight-line" in result["distance_definition"].lower() or \
           "great-circle" in result["distance_definition"].lower()


def test_find_nearby_airports_invalid_code_returns_error_not_crash():
    from agent.tools.nearby_airports import find_nearby_airports
    from data.degradation import SourceUnavailable

    with patch("data.clients.faa_airports.get_airport_identity",
               side_effect=SourceUnavailable("FAA Airports and Runways", "no record for ZZZZ")):
        result = find_nearby_airports("ZZZZ")

    assert result["error"] == "INVALID_AIRPORT"
